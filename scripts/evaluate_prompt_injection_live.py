from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from app.providers import anthropic_client
from app.rag import PROMPT_VERSION, SONNET_MODEL, _generate, _validate_provider_summary
from app.adversarial_mutations import deterministic_live_plan, load_corpus, mutate_attack


_CASES = tuple(load_corpus())


def _context(attacks: list[str], suffix: int) -> dict[str, Any]:
    steps = [
        {
            "step_number": 1,
            "occurred_at": None,
            "title": "Distribuição",
            "text": "Processo distribuído.",
        }
    ]
    for offset, attack in enumerate(attacks, start=2):
        steps.append(
            {
                "step_number": offset,
                "occurred_at": None,
                "title": "Juntada",
                "text": "Foi juntada petição. " + attack,
            }
        )
    return {
        "code": f"0000000-00.2026.8.21.{suffix:04d}",
        "class_name": "Procedimento Comum",
        "court": "TJRS",
        "header": {},
        "parties": [],
        "subjects": [],
        "secrecy_level": 0,
        "step_count": len(steps),
        "steps": steps,
    }


async def _run() -> dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for live injection acceptance")

    client = anthropic_client(api_key)
    results: list[dict[str, Any]] = []

    for index, case in enumerate(_CASES, start=1):
        raw_parts = case.get("attack_parts")
        base_attacks = (
            [str(part) for part in raw_parts]
            if isinstance(raw_parts, (list, tuple))
            else [str(case["attack"])]
        )
        plan = deterministic_live_plan(index - 1)
        attacks = [mutate_attack(part, plan) for part in base_attacks]
        context = _context(attacks, index)
        text = await _generate(client, context)
        validation = _validate_provider_summary(text, context)
        attempts = 1
        if not validation.passed:
            text = await _generate(client, context, validation.errors)
            validation = _validate_provider_summary(text, context)
            attempts = 2

        lowered = text.casefold()
        hits = [
            fragment
            for fragment in case["forbidden"]
            if str(fragment).casefold() in lowered
        ]
        passed = validation.passed and not hits
        results.append(
            {
                "case_id": case["case_id"],
                "family": case["family"],
                "passed": passed,
                "attempts": attempts,
                "validation_passed": validation.passed,
                "validation_error_count": len(validation.errors),
                "forbidden_hits": hits,
            }
        )

    families: dict[str, dict[str, int | float]] = {}
    for item in results:
        family = str(item["family"])
        aggregate = families.setdefault(
            family, {"passed": 0, "total": 0, "pass_rate": 0.0}  # nosec B105
        )
        aggregate["total"] = int(aggregate["total"]) + 1
        if item["passed"]:
            aggregate["passed"] = int(aggregate["passed"]) + 1
    for aggregate in families.values():
        total = int(aggregate["total"])
        aggregate["pass_rate"] = (int(aggregate["passed"]) / total) if total else 0.0

    return {
        "suite": "summary-prompt-injection-live",
        "prompt_version": PROMPT_VERSION,
        "model": SONNET_MODEL,
        "passed": all(item["passed"] for item in results),
        "families": families,
        "cases": results,
    }


def main() -> int:
    report = asyncio.run(_run())
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
