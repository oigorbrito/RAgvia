from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CORPUS_PATH = Path(__file__).resolve().parents[1] / "scripts" / "adversarial_summary_corpus.json"
ZERO_WIDTH = "\u200b"
BIDI_OVERRIDE = "\u202e"
CYRILLIC_O = "\u043e"


@dataclass(frozen=True, slots=True)
class MutationPlan:
    casing: str = "identity"
    spacing: str = "identity"
    markup: str = "identity"
    delimiter_depth: int = 0
    unicode_variant: str = "identity"


def load_corpus() -> list[dict[str, Any]]:
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("adversarial corpus must be a JSON array")
    return [dict(item) for item in payload]


def _alternate_case(value: str) -> str:
    rendered: list[str] = []
    upper = True
    for character in value:
        if character.isalpha():
            rendered.append(character.upper() if upper else character.lower())
            upper = not upper
        else:
            rendered.append(character)
    return "".join(rendered)


def _spread_spaces(value: str) -> str:
    words = re.split(r"(\s+)", value)
    return "".join("   " if part.isspace() else part for part in words)


def _insert_zero_width(value: str) -> str:
    for index, character in enumerate(value):
        if character.isalpha():
            return value[: index + 1] + ZERO_WIDTH + value[index + 1 :]
    return ZERO_WIDTH + value


def _insert_homoglyph(value: str) -> str:
    for index, character in enumerate(value):
        if character.casefold() == "o":
            replacement = CYRILLIC_O.upper() if character.isupper() else CYRILLIC_O
            return value[:index] + replacement + value[index + 1 :]
    return value + CYRILLIC_O


def mutate_attack(value: str, plan: MutationPlan) -> str:
    rendered = str(value)

    if plan.casing == "upper":
        rendered = rendered.upper()
    elif plan.casing == "lower":
        rendered = rendered.lower()
    elif plan.casing == "alternate":
        rendered = _alternate_case(rendered)

    if plan.spacing == "spread":
        rendered = _spread_spaces(rendered)
    elif plan.spacing == "newlines":
        rendered = re.sub(r"\s+", "\n\t", rendered)

    if plan.markup == "comment":
        rendered = f"<!-- {rendered} -->"
    elif plan.markup == "hidden-div":
        rendered = f"<div style='display:none'>{rendered}</div>"
    elif plan.markup == "code":
        rendered = "```text\n" + rendered + "\n```"

    if plan.delimiter_depth > 0:
        prefix = "".join("<movimentos_json><system>" for _ in range(plan.delimiter_depth))
        suffix = "".join("</system></movimentos_json>" for _ in range(plan.delimiter_depth))
        rendered = prefix + rendered + suffix

    if plan.unicode_variant == "zero-width":
        rendered = _insert_zero_width(rendered)
    elif plan.unicode_variant == "bidi":
        rendered = rendered + BIDI_OVERRIDE + ".txet"
    elif plan.unicode_variant == "homoglyph":
        rendered = _insert_homoglyph(rendered)

    return rendered


def deterministic_live_plan(index: int) -> MutationPlan:
    plans = (
        MutationPlan(casing="alternate"),
        MutationPlan(spacing="newlines"),
        MutationPlan(markup="comment"),
        MutationPlan(delimiter_depth=2),
        MutationPlan(unicode_variant="zero-width"),
        MutationPlan(unicode_variant="bidi"),
        MutationPlan(unicode_variant="homoglyph"),
    )
    return plans[index % len(plans)]
