from __future__ import annotations

import json
from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector

from app.json_utils import loads_strict_json


def _encode_json(value: Any) -> str:
    if isinstance(value, str):
        try:
            loads_strict_json(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return json.dumps(value, allow_nan=False)
        return value
    return json.dumps(value, allow_nan=False)


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "json",
        schema="pg_catalog",
        encoder=_encode_json,
        decoder=json.loads,
        format="text",
    )
    await conn.set_type_codec(
        "jsonb",
        schema="pg_catalog",
        encoder=_encode_json,
        decoder=json.loads,
        format="text",
    )
    await register_vector(conn)


async def create_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=database_url,
        min_size=min_size,
        max_size=max_size,
        command_timeout=60,
        init=_init_connection,
    )
