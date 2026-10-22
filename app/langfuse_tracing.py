from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:@/+-]{1,160}$")
_ALLOWED_RESULT_FIELDS = {
    "model",
    "generation_ms",
    "cache_hit",
    "cost_usd",
    "persisted",
    "reused",
    "summary_id",
}
_ALLOWED_USAGE_FIELDS = {
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
}


def langfuse_enabled() -> bool:
    return os.getenv("LANGFUSE_ENABLED", "false").strip().lower() in _TRUE_VALUES


def _safe_identifier(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    if not _SAFE_IDENTIFIER.fullmatch(rendered):
        return None
    return rendered


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > 160:
            return None
        return value
    return None


def build_summary_trace_metadata(
    *,
    job_id: Any = None,
    process_id: Any = None,
    version_id: Any = None,
    judit_request_id: Any = None,
    result: dict[str, Any] | None = None,
    source_ids: list[Any] | None = None,
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key, value in (
        ("job_id", job_id),
        ("process_id", process_id),
        ("version_id", version_id),
        ("judit_request_id", judit_request_id),
    ):
        safe = _safe_identifier(value)
        if safe is not None:
            metadata[key] = safe

    if result:
        for key in _ALLOWED_RESULT_FIELDS:
            if key not in result:
                continue
            safe = _safe_scalar(result[key])
            if safe is not None:
                metadata[key] = safe

        usage = result.get("usage")
        if isinstance(usage, dict):
            for key in _ALLOWED_USAGE_FIELDS:
                if key not in usage:
                    continue
                safe = _safe_scalar(usage[key])
                if safe is not None:
                    metadata[f"usage_{key}"] = safe

        validation = result.get("validation")
        if isinstance(validation, dict) and isinstance(validation.get("passed"), bool):
            metadata["validation_passed"] = validation["passed"]

    safe_sources: list[str] = []
    for value in (source_ids or [])[:64]:
        safe = _safe_identifier(value)
        if safe is not None:
            safe_sources.append(safe)
    if safe_sources:
        metadata["source_ids"] = safe_sources

    if validation_errors:
        # Do not export error strings: validation messages can echo process content.
        metadata["validation_error_count"] = len(validation_errors)

    return metadata


@dataclass(slots=True)
class SummaryTrace:
    observation: Any | None = None

    def finish(
        self,
        *,
        result: dict[str, Any] | None = None,
        source_ids: list[Any] | None = None,
        validation_errors: list[str] | None = None,
        error_type: str | None = None,
    ) -> None:
        if self.observation is None:
            return
        try:
            update: dict[str, Any] = {
                "metadata": build_summary_trace_metadata(
                    result=result,
                    source_ids=source_ids,
                    validation_errors=validation_errors,
                )
            }
            if error_type:
                update["level"] = "ERROR"
                update["status_message"] = _safe_identifier(error_type) or "task_error"
            self.observation.update(**update)
        except Exception as exc:  # pragma: no cover - defensive fail-open boundary
            logger.warning("Langfuse trace update failed (%s)", type(exc).__name__)
        finally:
            try:
                self.observation.end()
            except Exception as exc:  # pragma: no cover - defensive fail-open boundary
                logger.warning("Langfuse trace end failed (%s)", type(exc).__name__)


def start_summary_trace(
    *,
    job_id: Any = None,
    process_id: Any = None,
    version_id: Any = None,
    judit_request_id: Any = None,
    client: Any | None = None,
) -> SummaryTrace:
    if client is None and not langfuse_enabled():
        return SummaryTrace()

    if client is None:
        try:
            from langfuse import get_client

            client = get_client()
        except Exception as exc:
            logger.warning("Langfuse client unavailable (%s)", type(exc).__name__)
            return SummaryTrace()

    metadata = build_summary_trace_metadata(
        job_id=job_id,
        process_id=process_id,
        version_id=version_id,
        judit_request_id=judit_request_id,
    )
    try:
        observation = client.start_observation(
            name="rpy.process-summary",
            as_type="chain",
            metadata=metadata,
            version="process-summary-v1",
        )
    except Exception as exc:
        logger.warning("Langfuse trace start failed (%s)", type(exc).__name__)
        return SummaryTrace()
    return SummaryTrace(observation=observation)
