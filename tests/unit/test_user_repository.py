"""User schemas and the MongoDB user repository."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.database import ensure_indexes
from app.repositories.user_repository import (
    EmailAlreadyRegisteredError,
    UserRepository,
    get_user_repository,
)
from app.schemas.user import (
    TokenSchema,
    UserCreateSchema,
    UserLoginSchema,
    UserResponseSchema,
)


@pytest.fixture
async def repository(db) -> UserRepository:
    await ensure_indexes(db)
    return UserRepository(db)


async def test_create_stores_the_validated_document_shape(repository, db):
    created = await repository.create(
        name="Ana", email="ana@example.com", password_hash="hash"
    )

    stored = await db.users.find_one({"_id": created["_id"]})
    assert isinstance(stored["_id"], str)
    assert stored["email"] == "ana@example.com"
    assert stored["password_hash"] == "hash"
    assert stored["preferences"] == {}
    assert isinstance(stored["created_at"], datetime)
    assert created["created_at"].microsecond % 1000 == 0  # BSON precision


async def test_create_rejects_a_registered_email(repository):
    await repository.create(name="Ana", email="ana@example.com", password_hash="a")

    with pytest.raises(EmailAlreadyRegisteredError):
        await repository.create(
            name="Outra", email="ana@example.com", password_hash="b"
        )


async def test_lookup_by_email_and_id(repository):
    created = await repository.create(
        name="Ana", email="ana@example.com", password_hash="hash"
    )

    assert (await repository.get_by_email("ana@example.com"))["_id"] == created["_id"]
    assert (await repository.get_by_id(created["_id"]))["email"] == "ana@example.com"
    assert await repository.get_by_email("ninguem@example.com") is None
    assert await repository.get_by_id("missing") is None


def test_dependency_wraps_the_request_database(db):
    assert isinstance(get_user_repository(db), UserRepository)


def test_create_schema_normalizes_email_and_name():
    data = UserCreateSchema(
        name="  Ana  ", email="  Ana@Example.COM ", password="12345678"
    )

    assert data.name == "Ana"
    assert data.email == "ana@example.com"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Ana", "email": "not-an-email", "password": "12345678"},
        {"name": "Ana", "email": "ana@example.com", "password": "1234567"},
        {"name": "   ", "email": "ana@example.com", "password": "12345678"},
        {"name": "Ana", "email": "ana@example.com", "password": "ç" * 37},
    ],
    ids=["invalid-email", "short-password", "blank-name", "over-72-bytes"],
)
def test_create_schema_rejects_invalid_input(payload):
    with pytest.raises(ValidationError):
        UserCreateSchema(**payload)


def test_login_schema_normalizes_email_and_requires_password():
    assert UserLoginSchema(email="ANA@example.com", password="x").email == (
        "ana@example.com"
    )
    with pytest.raises(ValidationError):
        UserLoginSchema(email="ana@example.com", password="")


def test_response_schema_hides_the_hash_and_marks_dates_as_utc():
    document = {
        "_id": "abc",
        "name": "Ana",
        "email": "ana@example.com",
        "password_hash": "secret",
        "created_at": datetime(2026, 11, 6, 12, 0),
    }

    user = UserResponseSchema.from_document(document)

    assert "password_hash" not in user.model_dump()
    assert user.id == "abc"
    assert user.created_at is not None
    assert user.created_at.tzinfo is UTC


async def test_response_schema_keeps_aware_dates(repository):
    created = await repository.create(
        name="Ana", email="ana@example.com", password_hash="hash"
    )

    user = UserResponseSchema.from_document(created)

    assert user.created_at == created["created_at"]


def test_token_schema_defaults_to_bearer():
    token = TokenSchema(access_token="t", expires_in=60)

    assert token.token_type == "bearer"
