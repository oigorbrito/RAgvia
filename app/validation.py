from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from app.legal_facts import decimal_amount as _decimal_amount
from app.summary_contract import CONDITIONAL_SECTION_ORDER, CORE_SECTION_ORDER

_CPF_CNPJ_RE = re.compile(
    r"(?<!\d)(?:\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)"
)
_CNJ_RE = re.compile(r"\b\d{7}-?\d{2}\.?\d{4}\.?\d\.?\d{2}\.?\d{4}\b")
_LONG_DIGITS_RE = re.compile(r"(?<!\d)\d{11,}(?!\d)")
_PERSONAL_ID_KEY_RE = re.compile(
    r"(?:^|_)(?:cpf|cnpj|document|document_number|tax_id|person_id|national_id|identifier|id)(?:$|_)",
    re.IGNORECASE,
)
_IDENTIFIER_TOKEN_RE = re.compile(r"(?<!\d)(?:\d[\s./-]?){7,}\d(?!\d)")
_CLASS_RE = re.compile(r"\bclass\s*=", re.IGNORECASE)
_FORECAST_RE = re.compile(
    r"provavelmente\s+ser[áa]\s+condenad|chances?\s+de|tende\s+a\s+ganhar|recomendo\s+que",
    re.IGNORECASE,
)
_PARTY_TAG_RE = re.compile(r"<Party\s+name=[\"']([^\"']+)[\"'][^>]*/?>", re.IGNORECASE)
_ROLE = r"autor(?:a)?|r[ée]u|requerente|requerid[oa]|exequente|executad[oa]"
_CAPITALIZED_TOKEN = r"[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][A-Za-zÁÀÂÃÉÊÍÓÔÕÚÇáàâãéêíóôõúç0-9&.'/-]*"
_CONNECTOR = r"(?:da|de|do|das|dos|e)"
_PROPER_NAME = rf"{_CAPITALIZED_TOKEN}(?:\s+(?:{_CONNECTOR}\s+)?{_CAPITALIZED_TOKEN}){{1,7}}"
_ROLE_NAME_RE = re.compile(
    rf"\b(?:(?i:o|a)\s+)?(?:(?i:{_ROLE}))\s*"
    rf"(?:(?::|[-–—])\s*|(?:(?i:é|foi|seria|denominad[oa]|identificad[oa]\s+como))\s+)?"
    rf"(?P<name>{_PROPER_NAME})"
)
_NAME_ROLE_RE = re.compile(
    rf"\b(?P<name>{_PROPER_NAME})\s*,\s*"
    rf"(?:(?i:na\s+qualidade\s+de|como)\s+)?(?:(?i:{_ROLE}))\b"
)
_JSX_TAG_RE = re.compile(r"<(/?)([A-Z][A-Za-z0-9]*)(?:\s[^<>]*?)?(/?)>")
_ALLOWED_JSX_COMPONENTS = frozenset({"Party", "ProcessHeader"})
_MOVEMENT_COUNT_RE = re.compile(
    r"\b(?P<count>\d+)\s+(?:movimentos?|movimenta(?:ç|c)(?:ão|oes|ões))\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"(?<!\d)(?:\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])|"
    r"(?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])/\d{4})(?!\d)"
)
_ISO_DATETIME_RE = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})(?!\d)"
)
_SAO_PAULO = ZoneInfo("America/Sao_Paulo")
_ATTENTION_HEADING_RE = re.compile(
    r"^#{1,6}\s+Pontos\s+de\s+aten(?:ç|c)(?:ão|ao)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_NEXT_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$", re.MULTILINE)
_SOURCE_BACKED_CLAIM_RE = re.compile(
    r"^(?:[-*]\s*)?(?P<label>Área|Assuntos?|Tags?|Comarca|Órgão julgador|Classe|Fase|Rito|Justiça gratuita|Gratuidade)\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_AMOUNT_CLAIM_RE = re.compile(
    r"^(?:[-*]\s*)?(?P<label>Valor(?: da causa)?)\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
_META_OUTPUT_MARKERS = (
    "system prompt",
    "prompt do sistema",
    "mensagem de sistema",
    "developer message",
    "mensagem de desenvolvedor",
    "instruções internas",
    "instrucoes internas",
    "ignore as instruções anteriores",
    "ignore as instrucoes anteriores",
    "as a language model",
    "como modelo de linguagem",
)


@dataclass(slots=True)
class ValidationResult:
    passed: bool
    errors: list[str]


def _normalize_digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _normalize_party_name(value: Any) -> str:
    return _cached_normalize_party_name(str(value or ""))


@lru_cache(maxsize=2048)
def _cached_normalize_party_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    words = re.findall(r"[\w]+", without_marks.casefold(), flags=re.UNICODE)
    return " ".join(words)


def _party_names(parties: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for party in parties:
        name = party.get("name") or party.get("nome")
        if name:
            normalized = _normalize_party_name(str(name))
            if normalized:
                names.add(normalized)
    return names


def _mentioned_party_names(text: str) -> set[str]:
    raw_names = set(_PARTY_TAG_RE.findall(text))
    raw_names.update(match.group("name") for match in _ROLE_NAME_RE.finditer(text))
    raw_names.update(match.group("name") for match in _NAME_ROLE_RE.finditer(text))
    return {
        normalized
        for name in raw_names
        if (normalized := _normalize_party_name(name))
    }


def _party_names_present_verbatim(text: str, parties: list[dict[str, Any]]) -> set[str]:
    normalized_text = f" {_normalize_party_name(text)} "
    return {
        name
        for name in _party_names(parties)
        if f" {name} " in normalized_text
    }


def _personal_identifiers(value: Any, *, key: str = "") -> set[str]:
    identifiers: set[str] = set()
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            identifiers.update(_personal_identifiers(child_value, key=str(child_key)))
        return identifiers
    if isinstance(value, list):
        for child in value:
            identifiers.update(_personal_identifiers(child, key=key))
        return identifiers
    if not _PERSONAL_ID_KEY_RE.search(key):
        return identifiers
    digits = _normalize_digits(str(value))
    if len(digits) >= 8:
        identifiers.add(digits)
    return identifiers


def _party_identifiers(parties: list[dict[str, Any]]) -> set[str]:
    identifiers: set[str] = set()
    for party in parties:
        identifiers.update(_personal_identifiers(party))
    return identifiers


def _rendered_identifiers(text: str) -> set[str]:
    return {
        digits
        for match in _IDENTIFIER_TOKEN_RE.finditer(text)
        if len(digits := _normalize_digits(match.group(0))) >= 8
    }


def _jsx_errors(text: str) -> list[str]:
    errors: list[str] = []
    if _CLASS_RE.search(text):
        errors.append("JSX must use className= instead of class=")

    stack: list[str] = []
    disallowed: set[str] = set()
    for match in _JSX_TAG_RE.finditer(text):
        closing, tag, self_closing = match.groups()
        if tag not in _ALLOWED_JSX_COMPONENTS:
            disallowed.add(tag)
        if self_closing:
            continue
        if closing:
            if not stack or stack[-1] != tag:
                errors.append(f"unbalanced JSX tag: {tag}")
                continue
            stack.pop()
        else:
            stack.append(tag)
    if stack:
        errors.append(f"unclosed JSX tags: {', '.join(stack)}")
    for tag in sorted(disallowed):
        errors.append(f"JSX component is not allowed: {tag}")
    return errors


def _document_title_errors(text: str) -> list[str]:
    canonical = "# Resumo do processo"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    errors: list[str] = []
    if not lines or lines[0] != canonical:
        errors.append(f"summary must start with exact document title: {canonical}")

    normalized_title = _normalize_party_name("Resumo do processo")
    occurrences = 0
    for line in lines:
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match and _normalize_party_name(match.group(1)) == normalized_title:
            occurrences += 1
    if occurrences > 1:
        errors.append("duplicate document title: Resumo do processo")
    return errors


def _core_section_order_errors(text: str) -> list[str]:
    canonical = {
        _normalize_party_name(title): (index, title)
        for index, title in enumerate(CORE_SECTION_ORDER)
    }
    observed: list[tuple[int, int, str]] = []
    seen: set[str] = set()

    for match in _HEADING_RE.finditer(text):
        title = match.group("title").strip()
        normalized = _normalize_party_name(title)
        if normalized not in canonical:
            continue
        order, exact_title = canonical[normalized]
        if title != exact_title:
            return [
                f"core section title must be exactly: {exact_title}; got: {title}"
            ]
        if normalized in seen:
            return [f"duplicate core section: {exact_title}"]
        seen.add(normalized)
        observed.append((match.start(), order, exact_title))

    previous_order = -1
    for _, order, title in observed:
        if order < previous_order:
            return [
                "core sections must follow order: "
                + " → ".join(CORE_SECTION_ORDER)
                + f"; out-of-order section: {title}"
            ]
        previous_order = order
    return []


def _conditional_section_order_errors(text: str) -> list[str]:
    expected = {
        _normalize_party_name(title): index
        for index, title in enumerate(CONDITIONAL_SECTION_ORDER)
    }
    observed: list[tuple[int, int, str]] = []
    for match in _HEADING_RE.finditer(text):
        title = match.group("title").strip()
        normalized = _normalize_party_name(title)
        if normalized in expected:
            observed.append((match.start(), expected[normalized], title))

    previous_order = -1
    for _, order, title in observed:
        if order < previous_order:
            return [
                "conditional sections must follow order: "
                + " → ".join(CONDITIONAL_SECTION_ORDER)
                + f"; out-of-order section: {title}"
            ]
        previous_order = order
    return []


def _unexpected_heading_errors(
    text: str,
    allowed_headings: list[str] | tuple[str, ...],
) -> list[str]:
    allowed = {_normalize_party_name(title) for title in allowed_headings}
    errors: list[str] = []
    for match in _HEADING_RE.finditer(text):
        title = match.group("title").strip()
        if _normalize_party_name(title) not in allowed:
            errors.append(f"summary heading is not allowed: {title}")
    return errors


def _source_amounts(source_text: str) -> set[Decimal]:
    try:
        payload = json.loads(source_text)
    except (json.JSONDecodeError, TypeError):
        return set()

    amounts: set[Decimal] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).casefold() == "amount":
                    parsed = _decimal_amount(child)
                    if parsed is not None:
                        amounts.add(parsed)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return amounts


def _amount_claim_errors(text: str, source_text: str) -> list[str]:
    allowed = _source_amounts(source_text)
    errors: list[str] = []
    for match in _AMOUNT_CLAIM_RE.finditer(text):
        label = match.group("label").strip()
        raw_value = match.group("value").strip()
        parsed = _decimal_amount(raw_value)
        if parsed is None or parsed not in allowed:
            errors.append(f"source-backed amount mismatch: {label}={raw_value}")
    return errors


def _movement_source_text(source_text: str) -> str:
    try:
        payload = json.loads(source_text)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    movements = payload.get("movimentos")
    if not isinstance(movements, list):
        return ""
    return json.dumps(movements, ensure_ascii=False, default=str)


def _source_backed_claim_errors(text: str, source_text: str) -> list[str]:
    normalized_source = f" {_normalize_party_name(source_text)} "
    normalized_movements = f" {_normalize_party_name(_movement_source_text(source_text))} "
    errors: list[str] = []
    for match in _SOURCE_BACKED_CLAIM_RE.finditer(text):
        label = match.group("label").strip()
        normalized_label = _normalize_party_name(label)
        raw_value = match.group("value").strip()
        values = [raw_value]
        if normalized_label in {"assunto", "assuntos", "tag", "tags"}:
            values = [part.strip() for part in re.split(r"[,;]", raw_value) if part.strip()]
        evidence = (
            normalized_movements
            if normalized_label in {"justica gratuita", "gratuidade"}
            else normalized_source
        )
        for value in values:
            normalized_value = _normalize_party_name(value)
            if normalized_value and f" {normalized_value} " not in evidence:
                errors.append(f"source-backed field mismatch: {label}={value}")
    return errors


def _untrusted_output_errors(text: str, source_text: str) -> list[str]:
    errors: list[str] = []
    normalized_text = _normalize_party_name(text)
    normalized_source = _normalize_party_name(source_text)
    for marker in _META_OUTPUT_MARKERS:
        normalized_marker = _normalize_party_name(marker)
        if normalized_marker in normalized_text and normalized_marker not in normalized_source:
            errors.append(f"meta-output marker not present in source context: {marker}")

    source_urls = set(_URL_RE.findall(source_text))
    for url in sorted(set(_URL_RE.findall(text)) - source_urls):
        errors.append(f"URL not present in source context: {url}")
    return errors


@lru_cache(maxsize=512)
def _canonical_date(value: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _dates(text: str) -> set[str]:
    dates: set[str] = set()
    for match in _DATE_RE.finditer(text):
        if canonical := _canonical_date(match.group(0)):
            dates.add(canonical)
    return dates


def _source_dates(text: str) -> set[str]:
    """Accept literal source dates plus the correct São Paulo day for zoned timestamps."""
    dates = _dates(text)
    for match in _ISO_DATETIME_RE.finditer(text):
        rendered = match.group(0)
        try:
            value = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
        except ValueError:
            continue
        if value.tzinfo is None:
            continue
        dates.add(value.astimezone(_SAO_PAULO).date().isoformat())
    return dates


def _attention_body(text: str) -> str | None:
    heading = _ATTENTION_HEADING_RE.search(text)
    if heading is None:
        return None
    start = heading.end()
    next_heading = _NEXT_HEADING_RE.search(text, start)
    end = next_heading.start() if next_heading else len(text)
    return text[start:end].strip()


def validar(
    *,
    text: str,
    code: str,
    parties: list[dict[str, Any]],
    steps: list[dict[str, Any]] | None = None,
    expected_step_count: int | None = None,
    source_text: str | None = None,
    additional_source_dates: list[str] | None = None,
    require_attention_section: bool = False,
    required_attention_phrases: list[str] | None = None,
    forbid_party_names: bool = False,
    require_document_title: bool = False,
    allowed_headings: list[str] | tuple[str, ...] | None = None,
) -> ValidationResult:
    errors: list[str] = []

    if require_document_title:
        errors.extend(_document_title_errors(text))

    if _CPF_CNPJ_RE.search(text):
        errors.append("possible unmasked CPF/CNPJ")

    leaked_identifiers = _party_identifiers(parties) & _rendered_identifiers(text)
    if leaked_identifiers:
        errors.append("personal identifier from party data is prohibited")

    expected_cnj = _normalize_digits(code)
    for found in _CNJ_RE.findall(text):
        if _normalize_digits(found) != expected_cnj:
            errors.append(f"CNJ mismatch: {found}")

    for found in _LONG_DIGITS_RE.findall(text):
        if _normalize_digits(found) != expected_cnj:
            errors.append(f"CNJ mismatch: {found}")

    if forbid_party_names:
        if _party_names_present_verbatim(text, parties):
            errors.append("party names are prohibited for secret summary")
    else:
        allowed_parties = _party_names(parties)
        mentioned_names = _mentioned_party_names(text)
        unknown = sorted(name for name in mentioned_names if name not in allowed_parties)
        if unknown:
            errors.append("hallucinated parties: " + ", ".join(unknown))

    if _FORECAST_RE.search(text):
        errors.append("prognostic language is prohibited")

    count_to_validate = expected_step_count
    if count_to_validate is None and steps is not None:
        count_to_validate = len(steps)
    if count_to_validate is not None:
        for match in _MOVEMENT_COUNT_RE.finditer(text):
            stated_count = int(match.group("count"))
            if stated_count != count_to_validate:
                errors.append(
                    f"movement count mismatch: stated {stated_count}, expected {count_to_validate}"
                )

    if source_text is not None:
        allowed_dates = _source_dates(source_text)
        for source_date in additional_source_dates or []:
            allowed_dates.update(_source_dates(source_date))
        for generated_date in sorted(_dates(text) - allowed_dates):
            errors.append(f"date not present in source context: {generated_date}")
        errors.extend(_source_backed_claim_errors(text, source_text))
        errors.extend(_amount_claim_errors(text, source_text))
        errors.extend(_untrusted_output_errors(text, source_text))

    attention_body = _attention_body(text)
    if require_attention_section:
        if attention_body is None:
            errors.append("Pontos de atenção section is required")
        elif not attention_body:
            errors.append("Pontos de atenção section must not be empty")

    if required_attention_phrases:
        if attention_body is None:
            if "Pontos de atenção section is required" not in errors:
                errors.append("Pontos de atenção section is required")
        else:
            normalized_attention = _normalize_party_name(attention_body)
            for phrase in required_attention_phrases:
                normalized_phrase = _normalize_party_name(phrase)
                if normalized_phrase and normalized_phrase not in normalized_attention:
                    errors.append(f"required attention fact missing: {phrase}")

    errors.extend(_jsx_errors(text))
    errors.extend(_core_section_order_errors(text))
    errors.extend(_conditional_section_order_errors(text))
    if allowed_headings is not None:
        errors.extend(_unexpected_heading_errors(text, allowed_headings))
    return ValidationResult(passed=not errors, errors=errors)
