"""Sign-up and login rules, independent of HTTP and of MongoDB."""

import asyncio
from functools import cache
from typing import Annotated, Any, Protocol

from fastapi import Depends

from ..core.security import create_access_token, hash_password, verify_password
from ..repositories.user_repository import UserRepository, get_user_repository
from ..schemas.user import (
    TokenSchema,
    UserCreateSchema,
    UserLoginSchema,
    UserResponseSchema,
)


class UserStore(Protocol):
    """What the service needs from storage; `UserRepository` or a test fake."""

    async def create(
        self, *, name: str, email: str, password_hash: str
    ) -> dict[str, Any]: ...

    async def get_by_email(self, email: str) -> dict[str, Any] | None: ...


class InvalidCredentialsError(Exception):
    """Unknown e-mail or wrong password, deliberately indistinguishable."""


@cache
def _dummy_password_hash() -> str:
    return hash_password("spacos-dummy-password")


class AuthService:
    def __init__(self, users: UserStore) -> None:
        self._users = users

    async def register(self, data: UserCreateSchema) -> UserResponseSchema:
        """Create the account; raises `EmailAlreadyRegisteredError` on duplicates."""
        # bcrypt is deliberately slow; keep it off the event loop.
        password_hash = await asyncio.to_thread(hash_password, data.password)
        document = await self._users.create(
            name=data.name, email=data.email, password_hash=password_hash
        )
        return UserResponseSchema.from_document(document)

    async def authenticate(self, data: UserLoginSchema) -> TokenSchema:
        """Issue a token; raises `InvalidCredentialsError` otherwise."""
        user = await self._users.get_by_email(data.email)
        # Unknown e-mails still pay for a bcrypt check, so response time does not
        # reveal which e-mails have an account.
        password_hash = user["password_hash"] if user else _dummy_password_hash()
        matches = await asyncio.to_thread(verify_password, data.password, password_hash)
        if user is None or not matches:
            raise InvalidCredentialsError
        token, expires_in = create_access_token(user["_id"])
        return TokenSchema(access_token=token, expires_in=expires_in)


def get_auth_service(
    users: Annotated[UserRepository, Depends(get_user_repository)],
) -> AuthService:
    return AuthService(users)
