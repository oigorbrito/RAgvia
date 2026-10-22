from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

_CNJ_CANONICAL_RE = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")
_CNJ_DIGITS_RE = re.compile(r"^\d{20}$")
_PERSONAL_ID_RE = re.compile(r"(?<!\d)(?:\d{11}|\d{14})(?!\d)")
_FORMATTED_PERSONAL_ID_RE = re.compile(
    r"(?<!\d)(?:\d{3}[. ]?\d{3}[. ]?\d{3}[- ]?\d{2}|"
    r"\d{2}[. ]?\d{3}[. ]?\d{3}/?\d{4}[- ]?\d{2})(?!\d)"
)
_LEADING_STEP_NUMBER_RE = re.compile(r"^\s*\d+\s*(?:[-–—.:)]\s*|\s+)")
_ELETRONICA_REFER_RE = re.compile(r"\bELETRÔNICAREFER\b", re.IGNORECASE)
_GLUE_BOUNDARY_RE = re.compile(r"(?<=[a-záàâãéêíóôõúç])(?=[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ])")
_WHITESPACE_RE = re.compile(r"\s+")
_SAO_PAULO = ZoneInfo("America/Sao_Paulo")
_SOURCE_STEP_NUMBER_KEYS = (
    "source_step_number",
    "step_number",
    "event_number",
    "movement_number",
    "sequence_number",
)
_SECRET_HEADER_KEYS = (
    "instance",
    "area",
    "justice_description",
    "county",
    "state",
    "city",
)
_PUBLIC_HEADER_KEYS = (
    "name",
    *_SECRET_HEADER_KEYS,
    "amount",
    "phase",
    "status",
    "distribution_date",
    "judge",
    "court_unit",
)
_PUBLIC_SOURCE_HEADER_LIMITS = {
    "phase": 160,
    "status": 160,
    "distribution_date": 64,
    "judge": 240,
    "court_unit": 240,
}
_REPRESENTATIVE_PERSON_TYPES = {
    "ADVOGADO",
    "ADVOGADA",
    "ATTORNEY",
    "LAWYER",
    "COUNSEL",
    "PROCURADOR",
    "PROCURADORA",
    "REPRESENTANTE",
    "REPRESENTATIVE",
    "LEGAL_REPRESENTATIVE",
    "REPRESENTANTE_LEGAL",
    "ADVOGADO_A",
    "PROCURADOR_A",
    "DEFENSOR",
    "DEFENSORA",
    "DEFENSOR_PUBLICO",
    "DEFENSORA_PUBLICA",
}
_REPRESENTED_PARTY_KEYS = (
    "represented_party",
    "represented_party_name",
    "party_name",
    "client",
    "principal",
)


@dataclass(slots=True)
class JuditEvent:
    event_type: str
    request_id: str | None
    callback_id: str | None
    response_id: str | None
    response_type: str | None
    code: str | None
    cached_response: bool
    raw: dict[str, Any]
    response_data: dict[str, Any] | None

    @property
    def request_completed(self) -> bool:
        if self.event_type == "request_completed":
            return True
        if self.event_type != "response_created" or self.response_type != "application_info":
            return False
        data = self.response_data or {}
        message = str(data.get("message") or "").strip().upper()
        try:
            info_code = int(data.get("code")) if data.get("code") is not None else None
        except (TypeError, ValueError):
            info_code = None
        return message == "REQUEST_COMPLETED" or info_code == 600

    @property
    def is_lawsuit_response(self) -> bool:
        return self.event_type == "response_created" and self.response_type == "lawsuit"


def normalize_cnj(value: str) -> str:
    candidate = value.strip()
    if not (
        _CNJ_CANONICAL_RE.fullmatch(candidate)
        or _CNJ_DIGITS_RE.fullmatch(candidate)
    ):
        raise ValueError("invalid CNJ process code")
    digits = "".join(character for character in candidate if character.isdigit())
    return (
        f"{digits[:7]}-{digits[7:9]}."
        f"{digits[9:13]}.{digits[13]}.{digits[14:16]}.{digits[16:20]}"
    )


def _reject_non_finite_numbers(value: Any) -> None:
    pending: list[Any] = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, float):
            if not math.isfinite(current):
                raise ValueError("webhook payload contains non-finite number")
            continue
        if isinstance(current, dict):
            pending.extend(current.values())
            continue
        if isinstance(current, list):
            pending.extend(current)


def parse_event(body: dict[str, Any]) -> JuditEvent:
    if not isinstance(body, dict):
        raise ValueError("invalid webhook envelope")
    _reject_non_finite_numbers(body)

    event_type = str(body.get("event_type") or "").strip().lower()
    reference_type = str(body.get("reference_type") or "").strip().lower()
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}

    explicit_request_id = (
        payload.get("request_id")
        or body.get("request_id")
        or body.get("requestId")
    )
    request_id = (
        explicit_request_id
        if explicit_request_id
        else (body.get("reference_id") if reference_type != "tracking" else None)
    )
    callback_id = body.get("callback_id")
    response_id = payload.get("response_id")
    response_type = payload.get("response_type")
    response_data = payload.get("response_data")
    if response_data is not None and not isinstance(response_data, dict):
        response_data = None

    if not event_type:
        status = str(body.get("status") or "").lower()
        event_type = (
            "request_completed"
            if status in {"completed", "request_completed"}
            else "response_created"
        )
        if response_data is None:
            response_data = body
        response_type = response_type or "lawsuit"

    tags = payload.get("tags") if isinstance(payload.get("tags"), dict) else {}
    cached_response = bool(
        tags.get("cached_response")
        if "cached_response" in tags
        else payload.get("cached_response", body.get("cached_response", False))
    )

    code: str | None = None
    if isinstance(response_data, dict):
        value = response_data.get("code") or response_data.get("process_code")
        if value:
            code = str(value).strip() or None

    event = JuditEvent(
        event_type=event_type,
        request_id=str(request_id) if request_id else None,
        callback_id=str(callback_id) if callback_id else None,
        response_id=str(response_id) if response_id else None,
        response_type=str(response_type) if response_type else None,
        code=code,
        cached_response=cached_response,
        raw=body,
        response_data=response_data,
    )

    if event.is_lawsuit_response:
        if not event.code:
            raise ValueError("lawsuit response missing process code")
        event.code = normalize_cnj(event.code)
        if not event.request_id:
            raise ValueError("lawsuit response missing request id")
        if not (event.response_id or event.callback_id):
            raise ValueError("lawsuit response missing stable response identifier")

    if event.request_completed and not event.request_id:
        raise ValueError("request completion missing request id")

    return event


def _personal_id_digits(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits if len(digits) in {11, 14} else None


def _party_personal_id(party: dict[str, Any]) -> str | None:
    direct = _personal_id_digits(party.get("main_document"))
    if direct:
        return direct
    for document in party.get("documents") or []:
        if not isinstance(document, dict):
            continue
        candidate = _personal_id_digits(document.get("document"))
        if candidate:
            return candidate
    return None


def _masked_personal_id(value: str) -> str:
    if len(value) == 11:
        return f"***.***.***-{value[-2:]}"
    if len(value) == 14:
        return f"**.***.***/****-{value[-2:]}"
    raise ValueError("personal id must contain 11 or 14 digits")


def _normalized_person_type(value: Any) -> str:
    return _cached_normalized_person_type(str(value or "").strip())


@lru_cache(maxsize=1024)
def _cached_normalized_person_type(rendered_value: str) -> str:
    rendered = unicodedata.normalize("NFKD", rendered_value)
    ascii_value = "".join(
        character
        for character in rendered
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^A-Z0-9]+", "_", ascii_value.upper()).strip("_")


def _is_representative(party: dict[str, Any]) -> bool:
    return _normalized_person_type(party.get("person_type")) in _REPRESENTATIVE_PERSON_TYPES


def _represented_party_name(party: dict[str, Any]) -> str | None:
    for key in _REPRESENTED_PARTY_KEYS:
        value = party.get(key)
        if isinstance(value, dict):
            value = value.get("name")
        if value is None:
            continue
        rendered = str(value).strip()
        if rendered:
            return rendered
    return None


def _safe_party_entity(party: dict[str, Any]) -> dict[str, Any] | None:
    name = str(party.get("name") or "").strip()
    if not name:
        return None
    normalized: dict[str, Any] = {
        "name": name,
        "side": party.get("side"),
        "person_type": party.get("person_type"),
    }
    personal_id = _party_personal_id(party)
    if personal_id:
        normalized["masked_person_id"] = _masked_personal_id(personal_id)
    return normalized


def _safe_parties_and_representatives(
    process: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parties: list[dict[str, Any]] = []
    representatives: list[dict[str, Any]] = []
    for party in process.get("parties") or []:
        if not isinstance(party, dict):
            continue
        normalized = _safe_party_entity(party)
        if normalized is None:
            continue
        if _is_representative(party):
            represented_party = _represented_party_name(party)
            if represented_party:
                normalized["represents"] = represented_party
            representatives.append(normalized)
            continue
        parties.append(normalized)
    return parties, representatives


def _safe_subjects(process: dict[str, Any]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for subject in process.get("subjects") or []:
        if isinstance(subject, dict):
            safe.append({"code": subject.get("code"), "name": subject.get("name")})
        elif subject is not None:
            safe.append({"code": None, "name": str(subject)})
    return safe


def _safe_public_source_header(value: Any, *, limit: int) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("description") or value.get("value")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    rendered = _WHITESPACE_RE.sub(" ", str(value)).strip()
    rendered = _PERSONAL_ID_RE.sub("[documento removido]", rendered)
    rendered = _FORMATTED_PERSONAL_ID_RE.sub("[documento removido]", rendered)
    return rendered[:limit] or None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_SAO_PAULO)


def _safe_attachments(process: dict[str, Any]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    seen: set[str] = set()
    for attachment in process.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        attachment_id = str(attachment.get("attachment_id") or "").strip()
        if not attachment_id or attachment_id in seen:
            continue
        seen.add(attachment_id)
        raw_name = attachment.get("attachment_name")
        if raw_name is None:
            raw_name = attachment.get("content")
        name = str(raw_name).strip() if raw_name is not None else None
        raw_status = attachment.get("status")
        provider_status = str(raw_status).strip().lower() if raw_status is not None else None
        safe.append(
            {
                "attachment_id": attachment_id,
                "attachment_date": _parse_datetime(attachment.get("attachment_date")),
                "attachment_name": name or None,
                "provider_status": provider_status or None,
            }
        )
    return safe


def _normalize_step_text(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ")
    text = _LEADING_STEP_NUMBER_RE.sub("", text, count=1)
    text = _ELETRONICA_REFER_RE.sub("ELETRÔNICA REFER", text)
    text = _GLUE_BOUNDARY_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return _PERSONAL_ID_RE.sub("[documento removido]", text)


def _source_step_number(step: dict[str, Any]) -> int | None:
    for key in _SOURCE_STEP_NUMBER_KEYS:
        value = step.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.isdigit():
                return int(candidate)
    return None


def extract_promotable_fields(process: dict[str, Any]) -> dict[str, Any]:
    classifications = process.get("classifications") or []
    class_name = None
    class_code = None
    if classifications and isinstance(classifications[0], dict):
        class_name = classifications[0].get("name")
        class_code = classifications[0].get("code")
    class_name = class_name or process.get("class_name") or process.get("class")

    courts = process.get("courts") or []
    court = process.get("tribunal_acronym") or process.get("court")
    if not court and courts and isinstance(courts[0], dict):
        court = courts[0].get("name") or courts[0].get("code")

    secrecy_level = int(process.get("secrecy_level") or process.get("secrecyLevel") or 0)
    raw_code = process.get("code") or process.get("process_code")
    code = None
    if raw_code:
        try:
            code = normalize_cnj(str(raw_code))
        except ValueError:
            code = str(raw_code).strip() or None

    header_keys = _SECRET_HEADER_KEYS if secrecy_level > 0 else _PUBLIC_HEADER_KEYS
    header = {
        key: process.get(key)
        for key in header_keys
        if process.get(key) is not None
    }
    if secrecy_level == 0:
        courts = process.get("courts") or []
        if courts and isinstance(courts[0], dict):
            header["court_unit"] = courts[0].get("name")
        for key, limit in _PUBLIC_SOURCE_HEADER_LIMITS.items():
            safe_value = _safe_public_source_header(header.get(key), limit=limit)
            if safe_value is None:
                header.pop(key, None)
            else:
                header[key] = safe_value
    if secrecy_level == 0 and class_code is not None:
        rendered_class_code = str(class_code).strip()
        if rendered_class_code:
            header["class_code"] = rendered_class_code

    if secrecy_level > 0:
        return {
            "header": header,
            "parties": [],
            "representatives": [],
            "subjects": [],
            "steps": [],
            "attachments": [],
            "court": court,
            "class_name": class_name,
            "secrecy_level": secrecy_level,
        }

    steps = process.get("steps") or process.get("movements") or process.get("events") or []
    normalized_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        step_date = (
            step.get("step_date")
            or step.get("occurred_at")
            or step.get("date")
            or step.get("datetime")
        )
        step_type = step.get("step_type") or step.get("title") or step.get("type")
        raw_text = step.get("content") or step.get("text") or step.get("description") or ""
        occurred_at = _parse_datetime(step_date)
        normalized_steps.append(
            {
                "step_number": index,
                "occurred_at": occurred_at,
                "title": step_type,
                "text": _normalize_step_text(raw_text),
                "metadata": {
                    "cnj": code,
                    "instance": process.get("instance"),
                    "court": court,
                    "type": step_type,
                    "step_id": step.get("step_id"),
                    "step_number": index,
                    "source_step_number": _source_step_number(step),
                    "private": step.get("private"),
                    "secrecy_level": secrecy_level,
                    "tags": step.get("tags") or {},
                    "source_step_date": step_date,
                    "occurred_at_sao_paulo": occurred_at.isoformat() if occurred_at else None,
                },
            }
        )

    parties, representatives = _safe_parties_and_representatives(process)

    return {
        "header": header,
        "parties": parties,
        "representatives": representatives,
        "subjects": _safe_subjects(process),
        "steps": normalized_steps,
        "attachments": _safe_attachments(process),
        "court": court,
        "class_name": class_name,
        "secrecy_level": secrecy_level,
    }
