"""AuthService rules, with storage replaced by an in-memory fake."""

from typing import Any

import pytest

from app.core.security import decode_access_token, verify_password
from app.repositories.user_repository import EmailAlreadyRegisteredError
from app.schemas.user import UserCreateSchema, UserLoginSchema
from app.services.auth import AuthService, InvalidCredentialsError, get_auth_service


class FakeUserStore:
    """Implements the `UserStore` protocol without MongoDB."""

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}

    async def create(
        self, *, name: str, email: str, password_hash: str
    ) -> dict[str, Any]:
        if any(user["email"] == email for user in self.users.values()):
            raise EmailAlreadyRegisteredError(email)
        user_id = f"user-{len(self.users) + 1}"
        document = {
            "_id": user_id,
            "name": name,
            "email": email,
            "password_hash": password_hash,
        }
        self.users[user_id] = document
        return document

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        return next((u for u in self.users.values() if u["email"] == email), None)


@pytest.fixture
def store() -> FakeUserStore:
    return FakeUserStore()


@pytest.fixture
def service(store: FakeUserStore) -> AuthService:
    return AuthService(store)


def sign_up(email: str = "ana@example.com") -> UserCreateSchema:
    return UserCreateSchema(name="Ana", email=email, password="senha-segura")


async def test_register_stores_a_hash_and_returns_no_secret(service, store):
    user = await service.register(sign_up())

    stored = store.users[user.id]
    assert stored["password_hash"] != "senha-segura"
    assert verify_password("senha-segura", stored["password_hash"])
    assert user.email == "ana@example.com"
    assert "password_hash" not in user.model_dump()


async def test_register_rejects_a_duplicate_email(service):
    await service.register(sign_up())

    with pytest.raises(EmailAlreadyRegisteredError):
        await service.register(sign_up("ANA@example.com"))


async def test_valid_login_issues_a_token_for_the_user(service):
    user = await service.register(sign_up())

    token = await service.authenticate(
        UserLoginSchema(email="ana@example.com", password="senha-segura")
    )

    assert token.token_type == "bearer"
    assert token.expires_in > 0
    assert decode_access_token(token.access_token) == user.id


@pytest.mark.parametrize(
    ("email", "password"),
    [("ana@example.com", "senha-errada"), ("ninguem@example.com", "senha-segura")],
    ids=["wrong-password", "unknown-email"],
)
async def test_invalid_login_is_indistinguishable(service, email, password):
    await service.register(sign_up())

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(UserLoginSchema(email=email, password=password))


def test_dependency_builds_the_service(store):
    assert isinstance(get_auth_service(store), AuthService)
