from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

from app.summary_output import (
    _HEADER_FIELDS,
    CONDITIONAL_SUMMARY_FIELDS,
    structured_summary_document_is_canonical,
    structured_summary_document_matches_process,
    structured_summary_process,
)


def _text(value: Any) -> str:
    return html.escape(str(value or "").strip(), quote=False)


def _list_markup(values: list[Any]) -> list[str]:
    rendered = [_text(value) for value in values if str(value or "").strip()]
    if not rendered:
        return []
    return [
        "<ul>",
        *(
            "  <li>"
            "<p className=\"text-foreground/70 text-md font-normal\">"
            f"{value}</p></li>"
            for value in rendered
        ),
        "</ul>",
        "",
    ]


def _items(title: str, values: list[Any]) -> list[str]:
    markup = _list_markup(values)
    return [f"## {title}", "", *markup] if markup else []


def render_judit_compatible_summary(
    structured_output: dict[str, Any], context: dict[str, Any]
) -> str:
    """Render the validated RAGVIA document in the legacy Judit summary_ia shape.

    This is a presentation adapter only: structured claims and process identity
    remain owned by the canonical schema, while all dynamic text is HTML-escaped.
    """
    if not structured_summary_document_is_canonical(structured_output):
        raise ValueError("summary document is not canonical")
    if not structured_summary_document_matches_process(structured_output, context):
        raise ValueError("summary document does not match its authorized process")

    summary = structured_output["summary"]
    process = structured_summary_process(context)
    lines = ["# Resumo do processo", "", "<ul className=\"summary-process-header\">"]
    identity_fields = [("Processo", process["cnj"])]
    if process["class_name"]:
        identity_fields.append(("Classe", process["class_name"]))
    if process["court"]:
        identity_fields.append(("Tribunal", process["court"]))
    identity_fields.extend(
        (label, process["header"][key])
        for key, label in _HEADER_FIELDS
        if process["header"].get(key) is not None
    )
    lines.extend(
        f'<li className="summary-header-field"><strong>{_text(label)}:</strong> {_text(value)}</li>'
        for label, value in identity_fields
    )
    lines.extend(["</ul>", ""])
    synthesis = str(summary["synthesis"])
    context_prefix = "Contexto do processo:"
    progress_prefix = "Andamento processual:"
    if context_prefix in synthesis and progress_prefix in synthesis:
        context_text, progress_text = synthesis.split(progress_prefix, 1)
        context_text = context_text.removeprefix(context_prefix).strip()
        lines.extend(
            [
                f'<p>{context_prefix} {_text(context_text)}</p>',
                "",
                f'<p>{progress_prefix} {_text(progress_text)}</p>',
                "",
            ]
        )
    else:
        lines.extend([f"<p>{_text(synthesis)}</p>", ""])

    parties = context.get("parties") if isinstance(context.get("parties"), list) else []
    if parties:
        lines.extend(["## Partes", "", "<ul>"])
        for party in parties:
            if not isinstance(party, dict) or not str(party.get("name") or "").strip():
                continue
            name = _text(party.get("name"))
            side = str(party.get("side") or "").casefold()
            side_label = {"active": "ativo", "passive": "passivo", "ativo": "ativo", "passivo": "passivo"}.get(side)
            suffix = f" — polo cadastrado: {side_label}" if side_label else ""
            detail = (
                f'<br /><span className="text-foreground/70 text-md font-normal">{_text(suffix).lstrip(" —")}</span>'
                if suffix else ""
            )
            lines.append(
                "  <li><p>"
                '<strong className="text-foreground text-base leading-5 font-semibold">'
                f"{name}</strong>{detail}</p></li>"
            )
        lines.extend(["</ul>", ""])

    representatives = context.get("representatives") if isinstance(context.get("representatives"), list) else []
    if representatives:
        lines.extend(["## Representantes", "", "<ul>"])
        for representative in representatives:
            if not isinstance(representative, dict) or not str(representative.get("name") or "").strip():
                continue
            name = _text(representative.get("name"))
            represented = str(representative.get("represents") or "").strip()
            known = {str(p.get("name") or "").casefold() for p in parties if isinstance(p, dict)}
            relation = f" (representa: {_text(represented)})" if represented and represented.casefold() in known else ""
            lines.append(
                "  <li><p>"
                '<strong className="text-foreground text-base leading-5 font-semibold">'
                f"{name}</strong>{relation}</p></li>"
            )
        lines.extend(["</ul>", ""])

    class_name = str(context.get("class_name") or "").strip()
    if class_name:
        lines.extend(_items("Classe", [class_name]))
    subjects = context.get("subjects") if isinstance(context.get("subjects"), list) else []
    subject_names = [
        item.get("name") if isinstance(item, dict) else item
        for item in subjects
    ]
    lines.extend(_items("Assuntos", subject_names))
    lines.extend(["## Movimentações", "", *_list_markup(summary["timeline"])])
    lines.extend(
        [
            '<p className="text-foreground/70 text-md font-normal">'
            f"<strong>Estado atual:</strong> {_text(summary['current_status'])}</p>",
            "",
        ]
    )
    if summary["attention"]:
        lines.extend(
            ["<p><strong>Pontos de atenção</strong></p>", "", *_list_markup(summary["attention"])]
        )
    for key, title in CONDITIONAL_SUMMARY_FIELDS:
        lines.extend(_items(title, summary[key]))
    return "\n".join(lines).strip()


def judit_summary_item(
    summary_ia: str,
    *,
    created_at: datetime | str,
    generator: str | None,
    source_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Package an already-published RAGVIA summary as a Judit summary response."""
    if not isinstance(summary_ia, str) or not summary_ia.strip():
        raise ValueError("summary_ia must be a non-empty string")
    if isinstance(created_at, datetime):
        timestamp = created_at
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        created = timestamp.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    else:
        created = str(created_at).strip()
        if not created:
            raise ValueError("created_at must be present")
        if created.endswith("+00:00"):
            created = created[:-6] + "Z"

    return {
        "response_type": "summary",
        "response_data": {"data": [summary_ia], "origin_id": None, "origin": "response"},
        "tags": {
            "gerador": str(generator or "ragvia"),
            "alertas": [],
            "fontes": sorted({str(ref) for ref in (source_refs or []) if str(ref)}),
        },
        "created_at": created,
    }
