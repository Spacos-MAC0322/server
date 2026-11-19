"""Access to `users` and `user_places`; services depend on this, never on MongoDB."""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from ..core.database import get_database
from ..schemas.place import PlaceSummarySchema

USERS_COLLECTION = "users"
USER_PLACES_COLLECTION = "user_places"
PLACES_COLLECTION = "places"
PLACE_CURATIONS_COLLECTION = "place_curations"


class EmailAlreadyRegisteredError(Exception):
    """Another user already has this e-mail (unique index `email_unique`)."""


class PlaceNotFoundError(Exception):
    """The target place does not exist or is not published."""


class UserRepository:
    def __init__(self, database: AsyncDatabase) -> None:
        self._users = database[USERS_COLLECTION]
        self._user_places = database[USER_PLACES_COLLECTION]
        self._places = database[PLACES_COLLECTION]
        self._curations = database[PLACE_CURATIONS_COLLECTION]

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

    async def add_favorite(self, user_id: str, place_id: str) -> PlaceSummarySchema:
        """Mark a published place as favorite for the user (idempotent)."""
        place = await self._places.find_one(
            {"_id": place_id, "publication_status": "published"}
        )
        if place is None:
            raise PlaceNotFoundError(place_id)
        await self._user_places.update_one(
            {"user_id": user_id, "place_id": place_id},
            {
                "$set": {"is_favorite": True},
                "$setOnInsert": {
                    "_id": uuid4().hex,
                    "user_id": user_id,
                    "place_id": place_id,
                    "is_visited": False,
                },
            },
            upsert=True,
        )
        curation = await self._curations.find_one({"place_id": place_id})
        return PlaceSummarySchema.from_mongo(place, curation=curation)

    async def remove_favorite(self, user_id: str, place_id: str) -> bool:
        """Unmark a place as favorite; returns True if a favorite was removed."""
        result = await self._user_places.update_one(
            {"user_id": user_id, "place_id": place_id, "is_favorite": True},
            {"$set": {"is_favorite": False}},
        )
        return result.modified_count > 0

    async def list_favorites(self, user_id: str) -> list[PlaceSummarySchema]:
        """Return populated PlaceSummarySchema objects for the user's favorites."""
        links = (
            await self._user_places.find(
                {"user_id": user_id, "is_favorite": True}
            )
            .sort("place_id", 1)
            .to_list()
        )
        place_ids = [link["place_id"] for link in links]
        if not place_ids:
            return []
        places_docs = await self._places.find(
            {"_id": {"$in": place_ids}, "publication_status": "published"}
        ).to_list()
        places_by_id = {doc["_id"]: doc for doc in places_docs}
        curation_rows = await self._curations.find(
            {"place_id": {"$in": list(places_by_id)}}
        ).to_list()
        curations_by_id = {row["place_id"]: row for row in curation_rows}
        return [
            PlaceSummarySchema.from_mongo(
                places_by_id[pid], curation=curations_by_id.get(pid)
            )
            for pid in place_ids
            if pid in places_by_id
        ]


def get_user_repository(
    database: Annotated[AsyncDatabase, Depends(get_database)],
) -> UserRepository:
    """FastAPI dependency; tests replace it to avoid touching the database."""
    return UserRepository(database)
