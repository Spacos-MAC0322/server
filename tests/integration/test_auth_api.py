"""The /api/auth endpoints through the whole application, on in-memory MongoDB."""

from datetime import timedelta

import pytest
from fastapi import FastAPI

from app import main
from app.core.database import ensure_indexes
from app.core.security import create_access_token

ANA = {"name": "Ana", "email": "ana@example.com", "password": "senha-segura"}


@pytest.fixture(autouse=True)
async def indexes(db) -> None:
    # The unique e-mail index is what makes duplicate sign-ups fail.
    await ensure_indexes(db)


async def register(client, **overrides):
    return await client.post("/api/auth/register", json=ANA | overrides)


async def login(client, email=ANA["email"], password=ANA["password"]):
    return await client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


async def test_register_creates_the_account(client, db):
    response = await register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ana@example.com"
    assert body["name"] == "Ana"
    assert set(body) == {"id", "name", "email", "created_at"}
    assert await db.users.count_documents({}) == 1


async def test_register_blocks_a_duplicate_email_in_any_case(client):
    await register(client)

    response = await register(client, email="  ANA@Example.com ")

    assert response.status_code == 409
    assert response.json()["detail"] == "Este e-mail já está cadastrado."


@pytest.mark.parametrize(
    "overrides",
    [{"email": "ana"}, {"password": "curta"}, {"password": "ç" * 37}, {"name": ""}],
    ids=["invalid-email", "short-password", "over-72-bytes", "blank-name"],
)
async def test_register_validates_the_payload(client, overrides):
    response = await register(client, **overrides)

    assert response.status_code == 422


async def test_login_then_me_returns_the_user(client):
    created = (await register(client)).json()

    response = await login(client)

    assert response.status_code == 200
    token = response.json()
    assert token["token_type"] == "bearer"
    assert token["expires_in"] == 24 * 60 * 60

    me = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json() == created


@pytest.mark.parametrize(
    ("email", "password"),
    [("ana@example.com", "senha-errada"), ("ninguem@example.com", "senha-segura")],
    ids=["wrong-password", "unknown-email"],
)
async def test_invalid_login_is_rejected(client, email, password):
    await register(client)

    response = await login(client, email, password)

    assert response.status_code == 401
    assert response.json()["detail"] == "E-mail ou senha incorretos."
    assert "access_token" not in response.json()


async def test_me_requires_authentication(client):
    response = await client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"] == "Não autenticado."


@pytest.mark.parametrize("kind", ["garbage", "expired"])
async def test_me_rejects_invalid_or_expired_tokens(client, kind):
    user_id = (await register(client)).json()["id"]
    token = "garbage"
    if kind == "expired":
        token, _ = create_access_token(user_id, expires_delta=timedelta(seconds=-1))

    response = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401
    assert response.json()["detail"].startswith("Sessão inválida ou expirada")


async def test_cors_preflight_allows_the_dev_frontend(client):
    response = await client.options(
        "/api/auth/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_origins_come_from_the_environment(monkeypatch):
    assert main.cors_origins() == ["http://localhost:5173", "http://127.0.0.1:5173"]

    monkeypatch.setenv("CORS_ORIGINS", " https://spacos.app , ,http://localhost:4173")
    assert main.cors_origins() == ["https://spacos.app", "http://localhost:4173"]


async def test_startup_fails_without_a_jwt_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY")

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        async with main.lifespan(FastAPI()):
            pass  # pragma: no cover - startup must not get here
