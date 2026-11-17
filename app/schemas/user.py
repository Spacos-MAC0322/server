"""Request and response contracts for users and authentication."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

# bcrypt only looks at the first 72 bytes; longer passwords are rejected, not cut.
BCRYPT_MAX_PASSWORD_BYTES = 72
PASSWORD_MIN_LENGTH = 8


def _normalize_email(value: Any) -> Any:
    """Compare e-mails case-insensitively, so the unique index catches duplicates."""
    return value.strip().lower() if isinstance(value, str) else value


def _check_password_bytes(value: str) -> str:
    if len(value.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(
            f"A senha deve ter no máximo {BCRYPT_MAX_PASSWORD_BYTES} bytes."
        )
    return value


Email = Annotated[EmailStr, BeforeValidator(_normalize_email)]
Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
NewPassword = Annotated[
    str,
    Field(min_length=PASSWORD_MIN_LENGTH),
    AfterValidator(_check_password_bytes),
]


class UserCreateSchema(BaseModel):
    name: Name
    email: Email
    password: NewPassword


class UserLoginSchema(BaseModel):
    email: Email
    password: Annotated[str, Field(min_length=1)]


class UserResponseSchema(BaseModel):
    """What the API exposes about a user; never includes `password_hash`."""

    id: str
    name: str | None
    email: str
    created_at: datetime | None = None

    @field_validator("created_at")
    @classmethod
    def _assume_utc(cls, value: datetime | None) -> datetime | None:
        # MongoDB returns naive datetimes that are already in UTC.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Self:
        return cls(
            id=document["_id"],
            name=document.get("name"),
            email=document["email"],
            created_at=document.get("created_at"),
        )


class TokenSchema(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Validade do token, em segundos.")
