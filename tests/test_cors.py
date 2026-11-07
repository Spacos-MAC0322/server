import importlib.util

import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.core.config import get_cors_origins


@pytest.fixture
async def cors_client(monkeypatch):
    # Load a fresh application so environment settings do not affect other tests.
    monkeypatch.setenv("CORS_ORIGINS", "https://frontend.example.com")
    spec = importlib.util.spec_from_file_location("app.cors_test_main", main.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async with AsyncClient(
        transport=ASGITransport(app=module.app), base_url="http://test"
    ) as client:
        yield client


def test_default_origins(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert get_cors_origins() == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" https://frontend.example.com, , http://localhost:5174 ",
         ["https://frontend.example.com", "http://localhost:5174"]),
        ("", []),
    ],
)
def test_configured_origins_replace_defaults(monkeypatch, value, expected):
    monkeypatch.setenv("CORS_ORIGINS", value)
    assert get_cors_origins() == expected


def test_wildcard_is_rejected(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(ValueError, match="explicit origins"):
        get_cors_origins()


async def test_allowed_origin_and_no_cookie_credentials(cors_client):
    response = await cors_client.get(
        "/template/", headers={"Origin": "https://frontend.example.com"}
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "https://frontend.example.com"
    )
    assert "Origin" in response.headers["vary"]
    assert "access-control-allow-credentials" not in response.headers


async def test_unknown_origin_is_not_authorized(cors_client):
    response = await cors_client.get(
        "/template/", headers={"Origin": "https://unknown.example.com"}
    )
    # CORS controls browser access to the response, not access to the endpoint.
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def test_json_and_bearer_token_preflight(cors_client, method):
    response = await cors_client.options(
        "/template/",
        headers={
            "Origin": "https://frontend.example.com",
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "https://frontend.example.com"
    )
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers


@pytest.mark.parametrize(
    "overrides",
    [
        {"Origin": "https://unknown.example.com"},
        {"Access-Control-Request-Method": "TRACE"},
        {"Access-Control-Request-Headers": "x-unapproved-header"},
    ],
)
async def test_disallowed_preflight(cors_client, overrides):
    response = await cors_client.options(
        "/template/",
        headers={
            "Origin": "https://frontend.example.com",
            "Access-Control-Request-Method": "POST",
            **overrides,
        },
    )
    assert response.status_code == 400


async def test_handled_error_has_cors_headers(cors_client):
    response = await cors_client.get(
        "/missing", headers={"Origin": "https://frontend.example.com"}
    )
    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == (
        "https://frontend.example.com"
    )


async def test_request_without_origin_is_unchanged(cors_client):
    response = await cors_client.get("/template/")
    assert response.status_code == 200
    assert response.json() == {}
    assert "access-control-allow-origin" not in response.headers
