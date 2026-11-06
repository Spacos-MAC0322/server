"""Access to the `users` collection; services depend on this, never on MongoDB."""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from ..core.database import get_database

USERS_COLLECTION = "users"


class EmailAlreadyRegisteredError(Exception):
    """Another user already has this e-mail (unique index `email_unique`)."""


class UserRepository:
    def __init__(self, database: AsyncDatabase) -> None:
        self._users = database[USERS_COLLECTION]

    async def create(
        self, *, name: str, email: str, password_hash: str
    ) -> dict[str, Any]:
        """Insert a user; `_id` is a string, as the collection validator requires."""
        now = datetime.now(UTC)
        document = {
            "_id": uuid4().hex,
            "name": name,
            "email": email,
            "password_hash": password_hash,
            "preferences": {},
            # BSON dates keep milliseconds; truncate so the returned document
            # matches what later reads from MongoDB give back.
            "created_at": now.replace(microsecond=now.microsecond // 1000 * 1000),
        }
        try:
            await self._users.insert_one(document)
        except DuplicateKeyError as error:
            raise EmailAlreadyRegisteredError(email) from error
        return document

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        return await self._users.find_one({"email": email})

    async def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        return await self._users.find_one({"_id": user_id})


def get_user_repository(
    database: Annotated[AsyncDatabase, Depends(get_database)],
) -> UserRepository:
    """FastAPI dependency; tests replace it to avoid touching the database."""
    return UserRepository(database)
