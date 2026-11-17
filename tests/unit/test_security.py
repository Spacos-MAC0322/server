"""Password hashing, JWT handling and the authentication dependencies."""

from datetime import timedelta

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core import security
from app.core.config import Settings
from app.core.database import ensure_indexes
from app.core.security import (
    CurrentUser,
    OptionalUser,
    configure_security,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.repositories.user_repository import UserRepository, get_user_repository

from ..conftest import TEST_JWT_SECRET


def test_hash_round_trip():
    hashed = hash_password("senha-segura")

    assert hashed != "senha-segura"
    assert hashed.startswith("$2b$04$")  # cost lowered by the conftest fixture
    assert verify_password("senha-segura", hashed)
    assert not verify_password("outra-senha", hashed)


def test_verify_rejects_malformed_hashes_and_oversized_passwords():
    hashed = hash_password("senha-segura")

    assert not verify_password("senha-segura", "not-a-bcrypt-hash")
    assert not verify_password("x" * 73, hashed)


def test_token_round_trip_reports_lifetime():
    configure_security(Settings(jwt_secret=TEST_JWT_SECRET, jwt_expire_minutes=30))

    token, expires_in = create_access_token("user-1")

    assert expires_in == 30 * 60
    assert decode_access_token(token) == "user-1"


def test_default_lifetime_is_one_day():
    _, expires_in = create_access_token("user-1")

    assert expires_in == 24 * 60 * 60


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("garbage", id="malformed"),
        pytest.param(
            jwt.encode({"sub": "user-1", "exp": 9999999999}, "x" * 32, "HS256"),
            id="other-secret",
        ),
        pytest.param(
            jwt.encode({"exp": 9999999999}, TEST_JWT_SECRET),
            id="missing-subject",
        ),
        pytest.param(
            jwt.encode({"sub": "", "exp": 9999999999}, TEST_JWT_SECRET),
            id="empty-subject",
        ),
    ],
)
def test_decode_rejects_invalid_tokens(token):
    assert decode_access_token(token) is None


def test_decode_rejects_expired_and_tampered_tokens():
    expired, _ = create_access_token("user-1", expires_delta=timedelta(seconds=-1))
    valid, _ = create_access_token("user-1")
    header, payload, signature = valid.split(".")
    tampered = f"{header}.{payload}.{signature[::-1]}"

    assert decode_access_token(expired) is None
    assert decode_access_token(tampered) is None


def test_secret_is_required_and_settings_are_validated():
    with pytest.raises(ValidationError, match="jwt_expire_minutes"):
        Settings(jwt_secret=TEST_JWT_SECRET, jwt_expire_minutes=0)
    with pytest.raises(ValidationError, match="32 characters"):
        Settings(jwt_secret="short")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        configure_security(Settings())


def test_settings_load_on_first_use_without_the_api_lifespan(monkeypatch):
    settings = Settings(jwt_secret=TEST_JWT_SECRET, jwt_expire_minutes=5)
    monkeypatch.setattr(security, "get_settings", lambda: settings)

    _, expires_in = create_access_token("user-1")

    assert expires_in == 5 * 60


@pytest.fixture
async def users(db) -> UserRepository:
    await ensure_indexes(db)
    return UserRepository(db)


@pytest.fixture
async def guarded(users: UserRepository):
    """A tiny app with one private and one personalized public route."""
    api = FastAPI()

    @api.get("/private")
    async def private(user: CurrentUser):
        return {"id": user["_id"]}

    @api.get("/public")
    async def public(user: OptionalUser):
        return {"id": user["_id"] if user else None}

    api.dependency_overrides[get_user_repository] = lambda: users
    transport = ASGITransport(app=api)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def bearer_for(users: UserRepository) -> dict[str, str]:
    user = await users.create(name="Ana", email="ana@example.com", password_hash="h")
    token, _ = create_access_token(user["_id"])
    return {"Authorization": f"Bearer {token}"}


async def test_private_route_accepts_a_valid_token(guarded, users):
    headers = await bearer_for(users)

    response = await guarded.get("/private", headers=headers)

    assert response.status_code == 200
    assert response.json()["id"]


@pytest.mark.parametrize(
    ("headers", "detail"),
    [
        ({}, security.NOT_AUTHENTICATED),
        ({"Authorization": "Basic YW5hOnNlbmhh"}, security.NOT_AUTHENTICATED),
        ({"Authorization": "Bearer garbage"}, security.INVALID_SESSION),
    ],
    ids=["no-header", "other-scheme", "invalid-token"],
)
async def test_private_route_rejects_missing_or_invalid_tokens(
    guarded, headers, detail
):
    response = await guarded.get("/private", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"] == detail


async def test_token_of_a_deleted_user_stops_working(guarded, users, db):
    headers = await bearer_for(users)
    await db.users.delete_many({})

    response = await guarded.get("/private", headers=headers)

    assert response.status_code == 401
    assert response.json()["detail"] == security.INVALID_SESSION


async def test_public_route_personalizes_only_with_a_valid_token(guarded, users):
    headers = await bearer_for(users)

    anonymous = await guarded.get("/public")
    stale = await guarded.get("/public", headers={"Authorization": "Bearer garbage"})
    logged_in = await guarded.get("/public", headers=headers)

    assert anonymous.json() == {"id": None}
    assert stale.json() == {"id": None}
    assert logged_in.json()["id"]
