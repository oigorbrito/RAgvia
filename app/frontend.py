from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.tracking_api import router as tracking_router


router = APIRouter()
router.include_router(tracking_router)
_FRONTEND_DIR = Path(__file__).with_name("frontend")


@router.get("/", include_in_schema=False)
async def frontend_index() -> FileResponse:
    return FileResponse(
        _FRONTEND_DIR / "index.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@router.get("/favicon.svg", include_in_schema=False)
async def frontend_favicon() -> FileResponse:
    return FileResponse(
        _FRONTEND_DIR / "favicon.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@router.get("/app.css", include_in_schema=False)
async def frontend_css() -> FileResponse:
    return FileResponse(
        _FRONTEND_DIR / "app.css",
        media_type="text/css",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@router.get("/config.js", include_in_schema=False)
async def frontend_config() -> FileResponse:
    return FileResponse(
        _FRONTEND_DIR / "config.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@router.get("/app.js", include_in_schema=False)
async def frontend_js() -> FileResponse:
    return FileResponse(
        _FRONTEND_DIR / "app.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )
