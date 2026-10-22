from __future__ import annotations

from typing import Any

from app.datajud_client import datajud_config, lookup_datajud_metadata
from app.tasks import PermanentTaskError, task
from app.judit import normalize_cnj


@task("lookup_datajud_metadata")
async def lookup_datajud_metadata_task(payload: dict[str, Any]) -> dict[str, Any]:
    tenant_id = payload.get("tenant_id")
    raw_code = payload.get("code")
    raw_secrecy_level = payload.get("secrecy_level")
    if (
        not isinstance(tenant_id, str)
        or not isinstance(raw_code, str)
        or (raw_secrecy_level is not None and (
            isinstance(raw_secrecy_level, bool)
            or not isinstance(raw_secrecy_level, int)
            or raw_secrecy_level < 0
        ))
    ):
        raise PermanentTaskError("Invalid DataJud job payload")
    if raw_secrecy_level is not None and raw_secrecy_level > 0:
        return {"status": "skipped_secrecy"}
    try:
        code = normalize_cnj(raw_code)
        config = datajud_config()
    except (ValueError, RuntimeError):
        raise PermanentTaskError("DataJud worker configuration or request is invalid") from None
    if not config.enabled or not config.authorized_use or not config.api_key:
        raise PermanentTaskError("DataJud worker is not authorized")

    result = await lookup_datajud_metadata(
        code=code,
        secrecy_level=raw_secrecy_level,
        config=config,
    )
    metadata = result.metadata
    if result.status != "ok" or metadata is None:
        return {"status": result.status, "error_code": result.error_code}
    return {
        "status": "ok",
        "metadata": {
            "class_name": metadata.class_name,
            "class_code": metadata.class_code,
            "subjects": [dict(subject) for subject in metadata.subjects],
            "adjudicating_body": metadata.adjudicating_body,
            "county": metadata.county,
        },
    }
