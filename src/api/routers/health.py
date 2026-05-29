"""Health + readiness + metrics."""
from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from src.monitoring.health import check_all

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> Response:
    checks = await check_all()
    ok = all(c["ok"] for c in checks.values())
    import json
    return Response(
        content=json.dumps({"checks": checks}),
        status_code=200 if ok else 503,
        media_type="application/json",
    )


@router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
