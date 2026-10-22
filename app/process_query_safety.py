from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg


SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")

READ_EXISTING = "read_existing"
ACQUISITION_PENDING = "acquisition_pending"
ACQUISITION_REQUIRED = "acquisition_required"
REFRESH_REQUIRES_CONFIRMATION = "refresh_requires_confirmation"
AMBIGUOUS_FAILURE = "ambiguous_failure"

ALL_CLASSIFICATIONS = {
    READ_EXISTING,
    ACQUISITION_PENDING,
    ACQUISITION_REQUIRED,
    REFRESH_REQUIRES_CONFIRMATION,
    AMBIGUOUS_FAILURE,
}


def get_sao_paulo_civil_date(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SAO_PAULO_TZ).date().isoformat()


def is_acquired_today_sao_paulo(dt: datetime | None) -> bool:
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_sp = dt.astimezone(SAO_PAULO_TZ).date()
    now_sp = datetime.now(timezone.utc).astimezone(SAO_PAULO_TZ).date()
    return dt_sp == now_sp


class ProcessQuerySafetyError(Exception):
    """Base exception for process query safety errors."""


class AmbiguousFailureError(ProcessQuerySafetyError):
    """Raised when an acquisition cannot be repeated due to an ambiguous failure."""


class RefreshRequiresConfirmationError(ProcessQuerySafetyError):
    """Raised when attempting to refresh a process without explicit confirmation."""


class InvalidAuthorizationError(ProcessQuerySafetyError):
    """Raised when an authorization token is invalid or does not match request context."""


class AuthorizationAlreadyConsumedError(ProcessQuerySafetyError):
    """Raised when attempting to reuse an already-consumed authorization token."""


class AuthorizationExpiredError(ProcessQuerySafetyError):
    """Raised when an authorization token has expired."""


@dataclass(frozen=True, slots=True)
class CnjStatusResult:
    code: str
    classification: str
    history_available: bool
    source: str
    last_acquisition_at: datetime | None
    version_date: datetime | None
    process_updated_at: datetime | None
    refresh_classification: str = REFRESH_REQUIRES_CONFIRMATION
    acquired_today: bool = False
    civil_date_sao_paulo: str | None = None
    advisory: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "classification": self.classification,
            "status": self.classification,
            "history_available": self.history_available,
            "refresh_classification": self.refresh_classification,
            "source": self.source,
            "last_acquisition_at": (
                self.last_acquisition_at.isoformat()
                if self.last_acquisition_at
                else None
            ),
            "version_date": (
                self.version_date.isoformat()
                if self.version_date
                else None
            ),
            "process_updated_at": (
                self.process_updated_at.isoformat()
                if self.process_updated_at
                else None
            ),
            "acquired_today": self.acquired_today,
            "civil_date_sao_paulo": self.civil_date_sao_paulo,
            "advisory": self.advisory,
        }


@dataclass(frozen=True, slots=True)
class AcquisitionAuthorization:
    id: UUID
    token: str
    tenant_id: UUID
    process_code: str
    source: str
    operation: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None


async def classify_cnj_status(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    code: str,
    operation: str | None = None,
) -> CnjStatusResult:
    """Classify the persisted state of a CNJ for a specific tenant in a read-only way.

    Guarantees strict tenant isolation: never reveals or queries data of other tenants.
    """
    process_row = await conn.fetchrow(
        """
        SELECT p.id, p.updated_at, pv.created_at AS version_created_at, pv.finalized_at
        FROM processes p
        JOIN tenant_processes tp ON tp.process_id = p.id
        LEFT JOIN process_versions pv ON pv.id = p.current_version_id
        WHERE tp.tenant_id = $1 AND p.code = $2
        """,
        tenant_id,
        code,
    )

    request_row = await conn.fetchrow(
        """
        SELECT id, status, judit_request_id, created_at, completed_at, attempt_number
        FROM tenant_judit_requests
        WHERE tenant_id = $1 AND process_code = $2
        """,
        tenant_id,
        code,
    )

    history_available = process_row is not None
    version_date = (
        (process_row["finalized_at"] or process_row["version_created_at"])
        if process_row
        else None
    )
    process_updated_at = process_row["updated_at"] if process_row else None
    last_acquisition_at = None
    if request_row is not None:
        last_acquisition_at = request_row["completed_at"] or request_row["created_at"]

    if request_row is not None and request_row["status"] == "processing":
        classification = ACQUISITION_PENDING
    elif request_row is not None and request_row["status"] == "failed_ambiguous":
        classification = AMBIGUOUS_FAILURE
    elif history_available:
        if operation in {"refresh", "acquisition"}:
            classification = REFRESH_REQUIRES_CONFIRMATION
        else:
            classification = READ_EXISTING
    else:
        classification = ACQUISITION_REQUIRED

    acquired_today = is_acquired_today_sao_paulo(last_acquisition_at)
    civil_date_sp = get_sao_paulo_civil_date(last_acquisition_at)

    advisory: str | None = None
    if classification == AMBIGUOUS_FAILURE:
        advisory = (
            "Última solicitação apresentou resultado incerto (falha ambígua). "
            "Nova aquisição automática bloqueada para evitar duplicidade de cobrança."
        )
    elif classification == ACQUISITION_PENDING:
        advisory = (
            "Solicitação de aquisição em andamento. "
            "Aguarde a conclusão antes de emitir nova requisição."
        )
    elif acquired_today:
        advisory = (
            "Processo consultado hoje no fuso America/Sao_Paulo. "
            "Uma nova consulta poderá gerar cobrança adicional desnecessária."
        )
    elif history_available:
        advisory = (
            "Processo possui histórico disponível no sistema. "
            "Uma nova consulta atualizará os dados e poderá gerar cobrança."
        )
    else:
        advisory = (
            "Nenhum histórico encontrado para este CNJ. "
            "Primeira aquisição necessária."
        )

    return CnjStatusResult(
        code=code,
        classification=classification,
        history_available=history_available,
        source="judit",
        last_acquisition_at=last_acquisition_at,
        version_date=version_date,
        process_updated_at=process_updated_at,
        acquired_today=acquired_today,
        civil_date_sao_paulo=civil_date_sp,
        advisory=advisory,
    )


async def create_authorization(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    code: str,
    source: str = "judit",
    operation: str = "refresh",
    ttl_seconds: int = 900,
) -> AcquisitionAuthorization:
    """Create a server-validated, single-use, time-limited acquisition authorization."""
    if source != "judit":
        raise ValueError("source must be 'judit'")
    if operation not in {"refresh", "acquisition"}:
        raise ValueError("operation must be 'refresh' or 'acquisition'")

    token = f"auth_{uuid4().hex}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    row = await conn.fetchrow(
        """
        INSERT INTO acquisition_authorizations (
            tenant_id, process_code, source, operation, token, expires_at
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, token, tenant_id, process_code, source, operation, expires_at, created_at
        """,
        tenant_id,
        code,
        source,
        operation,
        token,
        expires_at,
    )
    return AcquisitionAuthorization(
        id=row["id"],
        token=row["token"],
        tenant_id=row["tenant_id"],
        process_code=row["process_code"],
        source=row["source"],
        operation=row["operation"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


async def consume_authorization(
    conn: asyncpg.Connection,
    *,
    token: str,
    tenant_id: UUID,
    code: str,
    source: str = "judit",
    operation: str = "refresh",
    request_id: UUID | None = None,
) -> UUID:
    """Atomically validate and consume an authorization token."""
    token = token.strip()
    if not token:
        raise InvalidAuthorizationError("authorization token cannot be empty")

    consumed = await conn.fetchrow(
        """
        UPDATE acquisition_authorizations
        SET consumed_at = NOW(),
            consumed_by_request_id = $6
        WHERE token = $1
          AND tenant_id = $2
          AND process_code = $3
          AND source = $4
          AND operation = $5
          AND expires_at > NOW()
          AND consumed_at IS NULL
        RETURNING id
        """,
        token,
        tenant_id,
        code,
        source,
        operation,
        request_id,
    )
    if consumed is not None:
        return consumed["id"]

    row = await conn.fetchrow(
        """
        SELECT id, tenant_id, process_code, source, operation, expires_at, consumed_at
        FROM acquisition_authorizations
        WHERE token = $1
        """,
        token,
    )
    if row is None:
        raise InvalidAuthorizationError("authorization token not found")

    if row["tenant_id"] != tenant_id or row["process_code"] != code:
        raise InvalidAuthorizationError(
            "authorization token does not belong to this tenant or process"
        )
    if row["source"] != source or row["operation"] != operation:
        raise InvalidAuthorizationError(
            f"authorization token was issued for {row['operation']}:{row['source']}, not {operation}:{source}"
        )
    if row["consumed_at"] is not None:
        raise AuthorizationAlreadyConsumedError(
            "authorization token has already been consumed"
        )
    now = datetime.now(timezone.utc)
    if row["expires_at"] <= now:
        raise AuthorizationExpiredError("authorization token has expired")

    raise InvalidAuthorizationError("authorization token validation failed")
