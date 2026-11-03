import importlib.util
import logging

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app import main
from app.core.exceptions import (
    ExternalIntegrationError,
    ExternalServiceInvalidResponse,
    ExternalServiceTimeout,
    ExternalServiceUnavailable,
)

SECRET_CAUSE = "https://upstream.example.com/?token=private-secret"


class SpecializedTimeout(ExternalServiceTimeout):
    """An adapter may specialize a failure without losing its HTTP contract."""


@pytest.fixture
async def error_client(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://frontend.example.com")
    spec = importlib.util.spec_from_file_location("app.error_test_main", main.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    failures = {
        "unavailable": ExternalServiceUnavailable,
        "timeout": ExternalServiceTimeout,
        "invalid": ExternalServiceInvalidResponse,
        "generic": ExternalIntegrationError,
        "specialized": SpecializedTimeout,
    }

    @module.app.get("/test-integration")
    async def failing_integration(kind: str):
        raise failures[kind]("open_meteo") from RuntimeError(SECRET_CAUSE)

    @module.app.get("/test-auth")
    async def unauthorized():
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @module.app.get("/test-bug")
    async def programming_error():
        raise RuntimeError("unrelated programming error")

    async with AsyncClient(
        transport=ASGITransport(app=module.app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.parametrize(
    ("kind", "status_code", "code", "message"),
    [
        (
            "unavailable", 503, "EXTERNAL_SERVICE_UNAVAILABLE",
            "O serviço externo está temporariamente indisponível.",
        ),
        (
            "timeout", 504, "EXTERNAL_SERVICE_TIMEOUT",
            "O serviço externo excedeu o tempo de resposta.",
        ),
        (
            "invalid", 502, "EXTERNAL_SERVICE_INVALID_RESPONSE",
            "O serviço externo retornou uma resposta inválida.",
        ),
        (
            "generic", 502, "EXTERNAL_INTEGRATION_ERROR",
            "Não foi possível concluir a comunicação com o serviço externo.",
        ),
        (
            "specialized", 504, "EXTERNAL_SERVICE_TIMEOUT",
            "O serviço externo excedeu o tempo de resposta.",
        ),
    ],
)
async def test_integration_error_contract(
    error_client, caplog, kind, status_code, code, message
):
    with caplog.at_level(logging.WARNING, logger="app.api.error_handlers"):
        response = await error_client.get(
            "/test-integration",
            params={"kind": kind},
            headers={"Origin": "https://frontend.example.com"},
        )

    assert response.status_code == status_code
    assert response.json() == {"error": {"code": code, "message": message}}
    assert response.headers["content-type"] == "application/json"
    assert response.headers["access-control-allow-origin"] == (
        "https://frontend.example.com"
    )
    assert "service=open_meteo" in caplog.text
    assert f"code={code}" in caplog.text
    assert "private-secret" not in response.text
    assert "open_meteo" not in response.text
    assert SECRET_CAUSE not in caplog.text


async def test_validation_error_keeps_fastapi_contract(error_client):
    response = await error_client.get("/test-integration")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "kind"]


async def test_http_exception_keeps_status_detail_and_headers(error_client):
    response = await error_client.get("/test-auth")
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    assert response.headers["www-authenticate"] == "Bearer"


async def test_unrelated_exception_is_not_translated(error_client):
    with pytest.raises(RuntimeError, match="unrelated programming error"):
        await error_client.get("/test-bug")
