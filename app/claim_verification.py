from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any, Protocol, Sequence
from zoneinfo import ZoneInfo

from app.legal_facts import decimal_amount as _decimal_amount

_EVIDENCE_REF_RE = re.compile(r"^[pma]-[0-9a-f]{32}$")
_CNJ_RE = re.compile(r"\b\d{7}-?\d{2}\.?\d{4}\.?\d\.?\d{2}\.?\d{4}\b")
_DATE_RE = re.compile(
    r"(?<!\d)(?:\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])|"
    r"(?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])/\d{4})(?![T\d])"
)
_ISO_DATETIME_RE = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})(?!\d)"
)
_AMOUNT_RE = re.compile(
    r"(?<!\w)(?:R\$|BRL)\s*-?\d(?:[\d.\s]*\d)?(?:,\d+)?(?!\w)",
    re.IGNORECASE,
)
_LABELED_AMOUNT_RE = re.compile(
    r"\bvalor(?:\s+da\s+causa)?\s*"
    r"(?::|é(?:\s+de)?|de)?\s*"
    r"(?:(?:R\$|BRL)\s*)?"
    r"(?P<amount>-?\d{1,3}(?:\.\d{3})*(?:,\d+)?|-?\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_STEP_COUNT_RE = re.compile(
    r"\b(?:o\s+processo\s+possui|há|total(?:iza)?)\s+"
    r"(?P<count>\d+)\s+movimentos\b",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
_SAO_PAULO = ZoneInfo("America/Sao_Paulo")
_MAX_EXCERPT_CHARS = 4000
VERIFICATION_STATUSES = frozenset(
    {"supported", "contradicted", "insufficient", "not_evaluated"}
)
_INTRINSICALLY_MATERIAL_CLAIM_CLASSES = frozenset(
    {"procedural_event", "decision", "deadline", "related_process", "attachment"}
)


class ClaimLike(Protocol):
    claim_id: str
    claim_class: str
    text: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    evidence_ref: str
    kind: str
    text: str
    cnjs: frozenset[str]
    dates: frozenset[str]
    amounts: frozenset[Decimal]
    party_names: frozenset[str]
    step_counts: frozenset[int]
    exact_texts: frozenset[str]
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None


@dataclass(frozen=True, slots=True)
class ClaimRelationVerification:
    evidence_ref: str
    status: str
    reason: str
    evidence_excerpt: str | None
    evidence_excerpt_sha256: str | None
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None


@dataclass(frozen=True, slots=True)
class ClaimVerification:
    claim_id: str
    claim_class: str
    status: str
    reason: str
    deterministic_fact_count: int
    relations: tuple[ClaimRelationVerification, ...]


@dataclass(frozen=True, slots=True)
class _ClaimFacts:
    normalized_text: str
    cnjs: frozenset[str]
    dates: frozenset[str]
    amounts: frozenset[Decimal]
    labeled_process_amounts: frozenset[Decimal]
    parties: frozenset[str]
    step_counts: frozenset[int]

    @property
    def count(self) -> int:
        return (
            len(self.cnjs)
            + len(self.dates)
            + len(self.amounts)
            + len(self.parties)
            + len(self.step_counts)
        )


def _normalized_text(value: Any) -> str:
    rendered = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _SPACE_RE.sub(" ", rendered).strip()


def _normalized_exact_text(value: Any) -> str:
    return _normalized_text(value).rstrip(" .;:!?")


def _exact_text_segments(value: Any) -> frozenset[str]:
    rendered = str(value or "").strip()
    if not rendered:
        return frozenset()
    return frozenset(
        normalized
        for part in _SENTENCE_SPLIT_RE.split(rendered)
        if (normalized := _normalized_exact_text(part))
    )


def _scalar_text_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            item
            for child in value.values()
            for item in _scalar_text_values(child)
        ]
    if isinstance(value, list):
        return [
            item
            for child in value
            for item in _scalar_text_values(child)
        ]
    if value is None or isinstance(value, bool):
        return []
    return [str(value)]


def _canonical_cnj(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _cnjs(text: str) -> frozenset[str]:
    return frozenset(_canonical_cnj(match.group(0)) for match in _CNJ_RE.finditer(text))


@lru_cache(maxsize=512)
def _canonical_date(value: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _dates(text: str) -> frozenset[str]:
    values: set[str] = set()
    for match in _DATE_RE.finditer(text):
        canonical = _canonical_date(match.group(0))
        if canonical is not None:
            values.add(canonical)
    for match in _ISO_DATETIME_RE.finditer(text):
        try:
            value = datetime.fromisoformat(match.group(0).replace("Z", "+00:00"))
        except ValueError:
            continue
        if value.tzinfo is not None:
            values.add(value.astimezone(_SAO_PAULO).date().isoformat())
    return frozenset(values)


def _amounts(text: str) -> frozenset[Decimal]:
    values: set[Decimal] = set()
    candidates = [
        *(match.group(0) for match in _AMOUNT_RE.finditer(text)),
        *(match.group("amount") for match in _LABELED_AMOUNT_RE.finditer(text)),
    ]
    for candidate in candidates:
        parsed = _decimal_amount(candidate)
        if parsed is not None:
            values.add(parsed)
    return frozenset(values)


def _labeled_process_amounts(text: str) -> frozenset[Decimal]:
    values: set[Decimal] = set()
    for match in _LABELED_AMOUNT_RE.finditer(text):
        parsed = _decimal_amount(match.group("amount"))
        if parsed is not None:
            values.add(parsed)
    return frozenset(values)


def _step_counts(text: str) -> frozenset[int]:
    return frozenset(
        int(match.group("count"))
        for match in _STEP_COUNT_RE.finditer(text)
    )


def _known_party_names(context: dict[str, Any]) -> frozenset[str]:
    values: set[str] = set()
    for party in context.get("parties", []):
        if not isinstance(party, dict):
            continue
        name = _normalized_text(party.get("name"))
        if name:
            values.add(name)
    return frozenset(values)


@lru_cache(maxsize=1024)
def _party_name_regex(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")


def _party_names_in_text(text: str, known: frozenset[str]) -> frozenset[str]:
    if not known or not text:
        return frozenset()
    normalized = _normalized_text(text)
    if not normalized:
        return frozenset()
    return frozenset(
        name
        for name in known
        if name in normalized and _party_name_regex(name).search(normalized)
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _verification_evidence(
    *,
    evidence_ref: str,
    kind: str,
    text: str,
    known_parties: frozenset[str],
    amounts: frozenset[Decimal] | None = None,
    step_counts: frozenset[int] | None = None,
    exact_values: Sequence[Any] | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    char_start: int | None = None,
    char_end: int | None = None,
) -> VerificationEvidence:
    return VerificationEvidence(
        evidence_ref=evidence_ref,
        kind=kind,
        text=text,
        cnjs=_cnjs(text),
        dates=_dates(text),
        amounts=frozenset(amounts if amounts is not None else _amounts(text)),
        party_names=_party_names_in_text(text, known_parties),
        step_counts=frozenset(
            step_counts if step_counts is not None else _step_counts(text)
        ),
        exact_texts=frozenset(
            segment
            for value in (exact_values if exact_values is not None else [text])
            for segment in _exact_text_segments(value)
        ),
        page_start=page_start,
        page_end=page_end,
        char_start=char_start,
        char_end=char_end,
    )


def verification_evidence_catalog(
    context: dict[str, Any],
) -> dict[str, VerificationEvidence]:
    known_parties = _known_party_names(context)
    catalog: dict[str, VerificationEvidence] = {}

    process_ref = str(context.get("_process_evidence_ref") or "")
    if _EVIDENCE_REF_RE.fullmatch(process_ref):
        process_payload = {
            "code": context.get("code"),
            "court": context.get("court"),
            "class_name": context.get("class_name"),
            "subjects": (
                context.get("subjects")
                if isinstance(context.get("subjects"), list)
                else []
            ),
            "parties": (
                context.get("parties")
                if isinstance(context.get("parties"), list)
                else []
            ),
            "representatives": (
                context.get("representatives")
                if isinstance(context.get("representatives"), list)
                else []
            ),
            "secrecy_level": context.get("secrecy_level"),
            "header": (
                context.get("header")
                if isinstance(context.get("header"), dict)
                else {}
            ),
            "step_count": context.get("step_count"),
            "source_warnings": (
                context.get("source_warnings")
                if isinstance(context.get("source_warnings"), list)
                else []
            ),
        }
        process_amounts: set[Decimal] = set()
        header = process_payload["header"]
        if isinstance(header, dict):
            parsed_amount = _decimal_amount(header.get("amount"))
            if parsed_amount is not None:
                process_amounts.add(parsed_amount)
        catalog[process_ref] = _verification_evidence(
            evidence_ref=process_ref,
            kind="process",
            text=_canonical_json(process_payload),
            known_parties=known_parties,
            amounts=frozenset(process_amounts),
            step_counts=(
                frozenset({int(context["step_count"])})
                if isinstance(context.get("step_count"), int)
                and not isinstance(context.get("step_count"), bool)
                and int(context["step_count"]) >= 0
                else frozenset()
            ),
            exact_values=_scalar_text_values(process_payload),
        )

    for step in context.get("steps", []):
        if not isinstance(step, dict):
            continue
        ref = str(step.get("evidence_ref") or "")
        if not _EVIDENCE_REF_RE.fullmatch(ref):
            continue
        payload = {
            "step_number": step.get("step_number"),
            "occurred_at": step.get("occurred_at"),
            "title": step.get("title"),
            "text": step.get("text"),
        }
        catalog[ref] = _verification_evidence(
            evidence_ref=ref,
            kind="movement",
            text=_canonical_json(payload),
            known_parties=known_parties,
            exact_values=[payload["title"], payload["text"]],
        )

    for attachment in context.get("attachments", []):
        if not isinstance(attachment, dict):
            continue
        ref = str(attachment.get("evidence_ref") or "")
        if not _EVIDENCE_REF_RE.fullmatch(ref):
            continue
        catalog[ref] = _verification_evidence(
            evidence_ref=ref,
            kind="attachment",
            text=str(attachment.get("text") or ""),
            known_parties=known_parties,
            exact_values=[attachment.get("text")],
            page_start=_optional_int(attachment.get("page_start")),
            page_end=_optional_int(attachment.get("page_end")),
            char_start=_optional_int(attachment.get("char_start")),
            char_end=_optional_int(attachment.get("char_end")),
        )
    return catalog


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _claim_facts(text: str, known_parties: frozenset[str]) -> _ClaimFacts:
    return _ClaimFacts(
        normalized_text=_normalized_exact_text(text),
        cnjs=_cnjs(text),
        dates=_dates(text),
        amounts=_amounts(text),
        labeled_process_amounts=_labeled_process_amounts(text),
        parties=_party_names_in_text(text, known_parties),
        step_counts=_step_counts(text),
    )


def _relation_status(
    *,
    facts: _ClaimFacts,
    evidence: VerificationEvidence,
) -> tuple[str, str, int]:
    if facts.normalized_text and facts.normalized_text in evidence.exact_texts:
        return "supported", "exact_text_present", 1

    fact_count = facts.count
    if fact_count == 0:
        return "not_evaluated", "no_deterministic_fact_anchor", 0

    if facts.cnjs and not facts.cnjs.issubset(evidence.cnjs):
        return "insufficient", "cited_source_missing_cnj", fact_count

    if facts.amounts and not facts.amounts.issubset(evidence.amounts):
        if (
            evidence.kind == "process"
            and evidence.amounts
            and facts.labeled_process_amounts
            and not facts.labeled_process_amounts.issubset(evidence.amounts)
        ):
            return "contradicted", "process_amount_mismatch", fact_count
        return "insufficient", "cited_source_missing_amount", fact_count

    if facts.dates and not facts.dates.issubset(evidence.dates):
        return "insufficient", "cited_source_missing_date", fact_count

    if facts.parties and not facts.parties.issubset(evidence.party_names):
        return "insufficient", "cited_source_missing_party", fact_count

    if facts.step_counts and not facts.step_counts.issubset(evidence.step_counts):
        if evidence.kind == "process" and evidence.step_counts:
            return "contradicted", "process_step_count_mismatch", fact_count
        return "insufficient", "cited_source_missing_step_count", fact_count

    return "supported", "deterministic_facts_present", fact_count


def _excerpt(value: str) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    rendered = value[:_MAX_EXCERPT_CHARS]
    return rendered, hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def verify_material_claims(
    claims: Sequence[ClaimLike],
    context: dict[str, Any],
) -> dict[str, ClaimVerification]:
    catalog = verification_evidence_catalog(context)
    known_parties = _known_party_names(context)
    results: dict[str, ClaimVerification] = {}

    for claim in claims:
        facts = _claim_facts(claim.text, known_parties)
        relations: list[ClaimRelationVerification] = []
        cited_evidence: list[VerificationEvidence] = []
        for ref in claim.evidence_refs:
            evidence = catalog.get(ref)
            if evidence is None:
                relations.append(
                    ClaimRelationVerification(
                        evidence_ref=ref,
                        status="insufficient",
                        reason="verification_source_unavailable",
                        evidence_excerpt=None,
                        evidence_excerpt_sha256=None,
                    )
                )
                continue
            cited_evidence.append(evidence)
            status, reason, _ = _relation_status(
                facts=facts,
                evidence=evidence,
            )
            excerpt, excerpt_sha256 = _excerpt(evidence.text)
            relations.append(
                ClaimRelationVerification(
                    evidence_ref=ref,
                    status=status,
                    reason=reason,
                    evidence_excerpt=excerpt,
                    evidence_excerpt_sha256=excerpt_sha256,
                    page_start=evidence.page_start,
                    page_end=evidence.page_end,
                    char_start=evidence.char_start,
                    char_end=evidence.char_end,
                )
            )

        fact_count = facts.count
        exact_support = any(
            facts.normalized_text and facts.normalized_text in evidence.exact_texts
            for evidence in cited_evidence
        )

        combined_cnjs = frozenset(
            value for evidence in cited_evidence for value in evidence.cnjs
        )
        combined_dates = frozenset(
            value for evidence in cited_evidence for value in evidence.dates
        )
        combined_amounts = frozenset(
            value for evidence in cited_evidence for value in evidence.amounts
        )
        combined_parties = frozenset(
            value for evidence in cited_evidence for value in evidence.party_names
        )
        combined_step_counts = frozenset(
            value for evidence in cited_evidence for value in evidence.step_counts
        )

        process_evidence = [
            evidence for evidence in cited_evidence if evidence.kind == "process"
        ]
        process_amounts = frozenset(
            value for evidence in process_evidence for value in evidence.amounts
        )
        process_step_counts = frozenset(
            value for evidence in process_evidence for value in evidence.step_counts
        )

        relation_statuses = {relation.status for relation in relations}
        if "contradicted" in relation_statuses:
            status = "contradicted"
            reason = "at_least_one_cited_source_contradicts_deterministic_fact"
        elif exact_support:
            status = "supported"
            reason = "exact_text_present_in_cited_source"
        elif fact_count == 0:
            status = "not_evaluated"
            reason = "no_deterministic_fact_anchor"
        elif (
            facts.labeled_process_amounts
            and not facts.labeled_process_amounts.issubset(combined_amounts)
            and process_amounts
        ):
            status = "contradicted"
            reason = "process_amount_mismatch"
        elif (
            facts.step_counts
            and not facts.step_counts.issubset(combined_step_counts)
            and process_step_counts
        ):
            status = "contradicted"
            reason = "process_step_count_mismatch"
        elif (
            facts.cnjs.issubset(combined_cnjs)
            and facts.dates.issubset(combined_dates)
            and facts.amounts.issubset(combined_amounts)
            and facts.parties.issubset(combined_parties)
            and facts.step_counts.issubset(combined_step_counts)
        ):
            status = "supported"
            reason = "deterministic_facts_present_across_cited_sources"
        else:
            status = "insufficient"
            reason = "cited_sources_do_not_support_all_deterministic_facts"

        results[claim.claim_id] = ClaimVerification(
            claim_id=claim.claim_id,
            claim_class=claim.claim_class,
            status=status,
            reason=reason,
            deterministic_fact_count=fact_count,
            relations=tuple(relations),
        )
    return results


def verification_errors(
    verification: dict[str, ClaimVerification],
) -> list[str]:
    errors: list[str] = []
    for claim_id, result in verification.items():
        if result.status == "contradicted":
            errors.append(f"claim contradicted by cited evidence: {claim_id}")
        elif result.status == "insufficient" and result.deterministic_fact_count > 0:
            errors.append(f"claim has insufficient cited evidence: {claim_id}")
        elif (
            result.status == "not_evaluated"
            and result.claim_class in _INTRINSICALLY_MATERIAL_CLAIM_CLASSES
        ):
            errors.append(f"material claim was not evaluated: {claim_id}")
    return errors
