"""Lightweight API liveness check without dependency probes."""

from typing import Literal

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health", summary="Verifica se a API está respondendo")
async def get_health() -> dict[str, Literal["ok"]]:
    return {"status": "ok"}
