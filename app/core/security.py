"""Password hashing, JWT access tokens and the FastAPI authentication dependencies.

Settings come from the environment and are read on use, not at import:
`JWT_SECRET_KEY` is required; `JWT_EXPIRE_MINUTES` defaults to one day.

Private routes declare `user: CurrentUser`; public routes that only personalize
their answer declare `user: OptionalUser`, which is `None` for visitors.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..repositories.user_repository import UserRepository, get_user_repository

JWT_ALGORITHM = "HS256"
DEFAULT_EXPIRE_MINUTES = 24 * 60
BCRYPT_ROUNDS = 12

NOT_AUTHENTICATED = "Não autenticado."
INVALID_SESSION = "Sessão inválida ou expirada. Faça login novamente."


def _secret_key() -> str:
    secret = os.environ.get("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY is required to sign access tokens.")
    return secret


def _expire_minutes() -> int:
    value = os.environ.get("JWT_EXPIRE_MINUTES", "")
    minutes = int(value) if value else DEFAULT_EXPIRE_MINUTES
    if minutes <= 0:
        raise RuntimeError("JWT_EXPIRE_MINUTES must be a positive integer.")
    return minutes


def validate_security_settings() -> None:
    """Fail at startup, rather than at the first login, on missing settings."""
    _secret_key()
    _expire_minutes()


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """False for a wrong password, a malformed hash or a password over 72 bytes."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def create_access_token(
    user_id: str, *, expires_delta: timedelta | None = None
) -> tuple[str, int]:
    """Sign a token for `user_id`; returns it with its lifetime in seconds."""
    if expires_delta is None:
        expires_delta = timedelta(minutes=_expire_minutes())
    now = datetime.now(UTC)
    payload = {"sub": user_id, "iat": now, "exp": now + expires_delta}
    token = jwt.encode(payload, _secret_key(), algorithm=JWT_ALGORITHM)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> str | None:
    """The user id in a valid token; `None` if expired, tampered or malformed."""
    try:
        payload = jwt.decode(
            token,
            _secret_key(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
    except jwt.InvalidTokenError:
        return None
    return payload["sub"] or None


bearer_scheme = HTTPBearer(
    auto_error=False, description="Token JWT obtido em POST /api/auth/login."
)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]
Repository = Annotated[UserRepository, Depends(get_user_repository)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _user_from_credentials(
    credentials: HTTPAuthorizationCredentials, repository: UserRepository
) -> dict[str, Any] | None:
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        return None
    # A token outlives a deleted account; the lookup makes it stop working.
    return await repository.get_by_id(user_id)


async def get_current_user(
    credentials: Credentials, repository: Repository
) -> dict[str, Any]:
    """The logged-in user's document, or 401 for every private route."""
    if credentials is None:
        raise _unauthorized(NOT_AUTHENTICATED)
    user = await _user_from_credentials(credentials, repository)
    if user is None:
        raise _unauthorized(INVALID_SESSION)
    return user


async def get_current_user_optional(
    credentials: Credentials, repository: Repository
) -> dict[str, Any] | None:
    """Like `get_current_user`, but visitors and stale tokens get `None`."""
    if credentials is None:
        return None
    return await _user_from_credentials(credentials, repository)


CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
OptionalUser = Annotated[dict[str, Any] | None, Depends(get_current_user_optional)]
