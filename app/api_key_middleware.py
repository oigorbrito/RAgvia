from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.api_key_auth import (
    api_key_environment,
    apply_rate_limit_headers,
    authenticate_api_key,
    bearer_credential,
    log_principal_access,
)
from app.judit import normalize_cnj


class ApiKeySecurityMiddleware(BaseHTTPMiddleware):
    """Authorize API keys before process data or provider-backed handlers run.

    Legacy bearer credentials are deliberately ignored here and continue through
    the existing route authentication path during the migration period.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        route = self._process_route(request)
        if route is None:
            return await call_next(request)

        try:
            token = bearer_credential(request)
        except HTTPException:
            return await call_next(request)
        if api_key_environment(token) is None:
            return await call_next(request)

        raw_code, action = route
        try:
            canonical_code = normalize_cnj(raw_code)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "invalid process code"})

        try:
            principal = await authenticate_api_key(
                request,
                token=token,
                process_code=canonical_code,
            )
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )

        request.state.principal = principal
        async with request.app.state.pool.acquire() as conn:
            await log_principal_access(
                conn,
                principal=principal,
                process_id=None,
                process_code=canonical_code,
                action=action,
                metadata={"method": request.method},
            )

        response = await call_next(request)
        apply_rate_limit_headers(response, principal)
        return response

    @staticmethod
    def _process_route(request: Request) -> tuple[str, str] | None:
        parts = [part for part in request.url.path.split("/") if part]
        if request.method == "GET" and len(parts) == 2 and parts[0] == "processes":
            return parts[1], "api_key_read_process"
        if (
            request.method == "GET"
            and len(parts) == 4
            and parts[0] == "v1"
            and parts[1] == "processos"
            and parts[3] in {"resumo", "fontes"}
        ):
            action = (
                "api_key_read_process_summary"
                if parts[3] == "resumo"
                else "api_key_read_process_sources"
            )
            return parts[2], action
        if (
            request.method == "POST"
            and len(parts) == 3
            and parts[0] == "processes"
            and parts[2] == "request"
        ):
            return parts[1], "api_key_request_process"
        return None
