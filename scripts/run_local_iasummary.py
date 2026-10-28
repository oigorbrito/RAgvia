from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.rag as rag
from app.claim_evidence import process_evidence_ref
from app.judit import extract_lawsuit_processes, extract_promotable_fields
from app.provenance import selected_movement_sources
from app.reranking import select_context_steps
from app.retrieval import Step
from app.tpu_glossary import resolve_process_tpu_definitions


def _replacement_count(value: Any) -> int:
    if isinstance(value, str):
        return value.count("\ufffd")
    if isinstance(value, dict):
        return sum(_replacement_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_replacement_count(item) for item in value)
    return 0


async def _build_lawsuit_context(
    lawsuit_record: dict[str, Any], *, source_digest: str, source_was_cached: bool
) -> tuple[dict[str, Any], list[Step]]:
    lawsuit_data = lawsuit_record.get("response_data")
    if not isinstance(lawsuit_data, dict):
        raise ValueError("lawsuit response_data must be an object")
    normalized = extract_promotable_fields(
        {"response_type": "lawsuit", "response_data": lawsuit_data}
    )
    if normalized["secrecy_level"] > 0:
        raise ValueError("local public extractive run is disabled for secret proceedings")

    internal_version_id = uuid5(NAMESPACE_URL, f"rpy-local-lawsuit:{source_digest}")
    raw_steps: list[Step] = []
    source_refs_by_step_id: dict[str, str] = {}
    for index, source_step in enumerate(normalized["steps"], start=1):
        metadata = source_step.get("metadata") or {}
        source_step_id = metadata.get("step_id") or index
        step_id = uuid5(internal_version_id, f"source-step:{source_step_id}:{index}")
        parser_metadata = metadata.get("source_parser") or {}
        if isinstance(parser_metadata, dict) and parser_metadata.get("source_ref"):
            source_refs_by_step_id[str(step_id)] = str(parser_metadata["source_ref"])
        raw_steps.append(
            Step(
                id=step_id,
                step_number=int(source_step.get("step_number") or index),
                text=str(source_step.get("text") or ""),
                title=(str(source_step["title"]) if source_step.get("title") is not None else None),
                occurred_at=source_step.get("occurred_at"),
                source_step_number=metadata.get("source_step_number"),
            )
        )

    ranked = await select_context_steps(
        query=rag.RETRIEVAL_QUERY,
        steps=raw_steps,
    )
    context: dict[str, Any] = {
        "code": normalized["header"].get("code") or lawsuit_data.get("code"),
        "court": normalized["court"],
        "class_name": normalized["class_name"],
        "subjects": normalized["subjects"],
        "parties": normalized["parties"],
        "representatives": normalized["representatives"],
        "secrecy_level": normalized["secrecy_level"],
        "header": normalized["header"],
        "attachments": normalized["attachments"],
        "step_count": len(raw_steps),
        "_process_evidence_ref": process_evidence_ref(internal_version_id),
        "_require_context_and_progress": True,
        "_source_cached_response": source_was_cached,
        "_datajud_conflict_fields": [],
        "_selected_sources": selected_movement_sources(ranked),
        "_attachment_sources": [],
        "_glossary_sources": [],
        "_unicode_security_flags": [],
        "_local_source_step_refs": source_refs_by_step_id,
    }
    for source in context["_selected_sources"]:
        rag._record_unicode_security_flags(
            context, list(source.get("unicode_security_flags") or [])
        )

    class_code = context["header"].get("class_code")
    definitions = resolve_process_tpu_definitions(
        class_code=class_code,
        subjects=context["subjects"],
        class_name=context["class_name"],
    )
    if definitions:
        context["tpu_glossary"] = [item.as_context() for item in definitions]
        context["_glossary_sources"] = [
            item.as_provenance(source_order=index)
            for index, item in enumerate(definitions)
        ]

    context["steps"] = rag._serialize_steps(ranked)
    documentary_limits = rag._undocumented_process_acts_warning(raw_steps)
    if documentary_limits:
        context["source_documentary_limits"] = documentary_limits

    warnings: list[str] = []
    if not raw_steps:
        warnings.append(rag.EMPTY_STEPS_WARNING)
    else:
        warnings.extend(rag._source_step_gap_warnings(raw_steps))
    if source_was_cached:
        warnings.append(rag.CACHED_SOURCE_WARNING)
    warnings.extend(
        rag._passive_party_attention_warnings(
            context["parties"], context["representatives"], raw_steps
        )
    )
    warnings.extend(documentary_limits or [])
    source_parser = context["header"].get("source_parser")
    warnings.extend(rag.source_parser_warnings(context["header"]))
    replacement_chars = _replacement_count(lawsuit_data)
    if replacement_chars:
        warnings.append(
            f"A fonte contém {replacement_chars} caractere(s) U+FFFD; o texto afetado foi preservado sem tentativa de reconstrução."
        )
    context["source_warnings"] = list(dict.fromkeys(warnings))

    knowledge, knowledge_sources = await rag._retrieve_supplemental_knowledge(
        None, context=context, steps=context["steps"]
    )
    if knowledge:
        context["legal_knowledge"] = knowledge
    context["_glossary_sources"].extend(knowledge_sources)
    return context, raw_steps


def _write_artifacts(
    output_dir: Path,
    *,
    result: dict[str, Any],
    report: dict[str, Any],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "iasummary-local-extractive.md"
    report_path = output_dir / "iasummary-local-validation.json"
    markdown = str(result.get("markdown") or "")
    report_markdown = [
        "# RAGVIA iaSummary — execução local extrativa",
        "",
        "- **Modo:** local, determinístico e sem provedor externo.",
        "- **Persistência oficial:** não realizada; nenhum banco foi conectado.",
        f"- **Validação do RAGVIA:** {'aprovada' if report['validation']['passed'] else 'reprovada'}.",
        f"- **Fonte:** `{report['source']['file_name']}`; SHA-256 `{report['source']['sha256']}`.",
        f"- **Entrada para geração:** somente `response_type: lawsuit` ({report['source']['movement_count']} movimentos).",
        "- **Limite de dados:** a geração e a validação consomem somente a resposta `response_type: lawsuit`; uma resposta `summary` não é usada nem comparada.",
        "",
        "## Documento gerado",
        "",
        markdown,
        "",
        "## Validação factual e proveniência",
        "",
        f"- Movimentos selecionados pelo retrieval: {report['retrieval']['selected_movement_count']} de {report['source']['movement_count']}.",
        f"- Recuperação jurídica: {report['retrieval']['legal_knowledge_count']} entradas locais; modo {report['retrieval']['mode']}.",
        f"- Chars U+FFFD na fonte lawsuit: {report['source']['replacement_character_count']}.",
        f"- Saída citada por claims estruturadas: {report['validation']['claim_count']} claims.",
        f"- Erros do validador: {len(report['validation']['errors'])}.",
        "",
        "- A validação determinística comprova estrutura e referências; não prova entailment semântico nem substitui revisão jurídica.",
    ]
    markdown_path.write_text("\n".join(report_markdown).rstrip() + "\n", encoding="utf-8")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return markdown_path, report_path


async def _run(args: argparse.Namespace) -> int:
    input_path = args.input.expanduser().resolve()
    raw_bytes = input_path.read_bytes()
    raw = json.loads(raw_bytes)
    if not isinstance(raw, dict):
        raise ValueError("Judit capture must be a JSON object")
    lawsuits = extract_lawsuit_processes(raw)
    if len(lawsuits) != 1:
        raise ValueError(f"expected exactly one lawsuit response, found {len(lawsuits)}")
    page_data = raw.get("page_data")
    lawsuit_record = next(
        (item for item in page_data if isinstance(item, dict) and item.get("response_type") == "lawsuit"),
        None,
    ) if isinstance(page_data, list) else None
    if lawsuit_record is None:
        lawsuit_record = {"response_type": "lawsuit", "response_data": lawsuits[0]}

    lawsuit_digest = hashlib.sha256(
        json.dumps(lawsuits[0], ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()
    source_digest = hashlib.sha256(raw_bytes).hexdigest()
    cached = bool(
        lawsuit_record.get("cached")
        or (lawsuit_record.get("tags") or {}).get("cached_response")
    )
    context, raw_steps = await _build_lawsuit_context(
        lawsuit_record,
        source_digest=lawsuit_digest,
        source_was_cached=cached,
    )
    if int(context.get("secrecy_level") or 0) > 0:
        raise ValueError("this local public runner does not process secret proceedings")

    # Make the absence of provider use an executable invariant for this runner.
    def _provider_forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("external provider calls are forbidden in the local runner")

    rag.anthropic_client = _provider_forbidden
    os.environ["RAGVIA_LOCAL_SUMMARY_RUNNER"] = "1"
    result = await rag.generate_summary(local_context=context)

    structured = result.get("structured_output")
    claims = (
        structured.get("summary", {}).get("claims", [])
        if isinstance(structured, dict)
        and isinstance(structured.get("summary"), dict)
        else []
    )
    timeline_claims = [
        item for item in claims
        if isinstance(item, dict) and str(item.get("claim_id", "")).startswith("timeline:")
    ]
    expected_movement_refs = {
        str(item.get("evidence_ref"))
        for item in context.get("_selected_sources", [])
        if isinstance(item, dict) and item.get("evidence_ref")
    }
    timeline_refs = {
        str(item["evidence_refs"][0])
        for item in timeline_claims
        if isinstance(item.get("evidence_refs"), list) and len(item["evidence_refs"]) == 1
    }
    all_movement_evidence_linked = (
        len(timeline_claims) == len(raw_steps)
        and timeline_refs == expected_movement_refs
    )
    source_parser = context["header"].get("source_parser")
    report = {
        "source": {
            "file_name": input_path.name,
            "origin": "existing_local_capture_file",
            "acquisition_performed": False,
            "tenant_process_association": None,
            "official_process_summary_persisted": False,
            "summary_reference_used_for_generation": False,
            "summary_response_consumed": False,
            "sha256": source_digest,
            "lawsuit_sha256": lawsuit_digest,
            "lawsuit_count": len(lawsuits),
            "movement_count": len(raw_steps),
            "party_count": len(context.get("parties", [])),
            "representative_count": len(context.get("representatives", [])),
            "secrecy_level": context.get("secrecy_level"),
            "replacement_character_count": _replacement_count(lawsuits[0]),
            "summary_reference_excluded_from_generation": True,
            "normalized_classifications": (
                source_parser.get("classifications", [])
                if isinstance(source_parser, dict)
                else []
            ),
            "normalizer_indicators": (
                source_parser.get("indicators", [])
                if isinstance(source_parser, dict)
                else []
            ),
        },
        "generation": {
            "model": result.get("model"),
            "mode": "local_extractive_no_provider",
            "anthropic_called": False,
            "persisted": result.get("persisted"),
            "reused": result.get("reused"),
            "generation_ms": result.get("generation_ms"),
        },
        "retrieval": {
            "mode": "existing short-process all-movement ranker plus deterministic local legal lookup; no database/vector provider",
            "selected_movement_count": len(context.get("_selected_sources", [])),
            "legal_knowledge_count": len(context.get("legal_knowledge", [])),
            "legal_knowledge_sources": context.get("_glossary_sources", []),
        },
        "validation": {
            "passed": bool(result.get("validation", {}).get("passed")),
            "errors": list(result.get("validation", {}).get("errors", [])),
            "claim_count": len(claims),
            "timeline_claim_count": len(timeline_claims),
            "all_movements_have_structured_evidence_refs": all_movement_evidence_linked,
        },
        "provenance": {
            "movement_sources": [
                {
                    "evidence_ref": item.get("evidence_ref"),
                    "local_evidence_step_uuid": str(item.get("step_id")),
                    "source_ref": context.get("_local_source_step_refs", {}).get(str(item.get("step_id"))),
                    "step_number": item.get("step_number"),
                    "occurred_at": item["occurred_at"].isoformat()
                    if isinstance(item.get("occurred_at"), datetime)
                    else item.get("occurred_at"),
                    "source_text_sha256": item.get("source_text_sha256"),
                    "source_order": item.get("source_order"),
                }
                for item in context.get("_selected_sources", [])
                if isinstance(item, dict)
            ],
            "claim_evidence_refs": [
                {
                    "claim_id": item.get("claim_id"),
                    "evidence_refs": item.get("evidence_refs", []),
                }
                for item in claims
                if isinstance(item, dict)
            ],
        },
    }
    markdown_path, report_path = _write_artifacts(
        args.output_dir, result=result, report=report
    )
    print(
        json.dumps(
            {
                "markdown_path": str(markdown_path),
                "report_path": str(report_path),
                "validation_passed": report["validation"]["passed"],
                "errors": report["validation"]["errors"],
                "generation": report["generation"],
                "source_sha256": source_digest,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["validation"]["passed"] else 2


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run iaSummary locally against only a historical Judit lawsuit response."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
