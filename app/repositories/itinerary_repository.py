"""Access to `itineraries` and to the opening hours of their places.

Every write and owned read filters by `user_id`, so another user's itinerary
behaves as if it did not exist.
"""

from collections.abc import Collection, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

ITINERARIES_COLLECTION = "itineraries"
PLACES_COLLECTION = "places"

# Only what the list endpoint shows; stops are kept just to count them.
SUMMARY_PROJECTION = {
    "title": 1,
    "date": 1,
    "created_at": 1,
    "updated_at": 1,
    "stops.order": 1,
}


class ItineraryRepository:
    def __init__(self, database: AsyncDatabase):
        self.collection = database[ITINERARIES_COLLECTION]
        self.places = database[PLACES_COLLECTION]

    async def find_opening_hours(self, place_ids: Collection[str]) -> dict[str, dict]:
        places = await self.places.find(
            {"_id": {"$in": list(place_ids)}}, {"opening_hours": 1}
        ).to_list()
        return {
            place["_id"]: place["opening_hours"]
            for place in places
            if isinstance(place.get("opening_hours"), dict)
        }

    async def find_missing_places(self, place_ids: Iterable[str]) -> set[str]:
        """Ids that do not match a published place, so they cannot be visited."""
        wanted = set(place_ids)
        if not wanted:
            return set()
        found = await self.places.find(
            {"_id": {"$in": list(wanted)}, "publication_status": "published"},
            {"_id": 1},
        ).to_list()
        return wanted - {place["_id"] for place in found}

    async def create(
        self,
        *,
        user_id: str,
        title: str,
        date: str | None,
        stops: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        now = _now()
        document = {
            "_id": uuid4().hex,
            "user_id": user_id,
            "title": title,
            "date": date,
            "stops": _stored_stops(stops),
            "preferences": {},
            "created_at": now,
            "updated_at": now,
        }
        await self.collection.insert_one(document)
        return document

    async def list_by_user(self, user_id: str) -> list[dict[str, Any]]:
        """Summaries ordered by date (undated first), then by creation."""
        documents = (
            await self.collection.find({"user_id": user_id}, SUMMARY_PROJECTION)
            .sort([("date", 1), ("created_at", 1), ("_id", 1)])
            .to_list()
        )
        for document in documents:
            document["stop_count"] = len(document.pop("stops", None) or [])
        return documents

    async def find_owned(self, itinerary_id: str, user_id: str) -> dict | None:
        return await self.collection.find_one({"_id": itinerary_id, "user_id": user_id})

    async def replace_owned(
        self,
        itinerary_id: str,
        user_id: str,
        *,
        title: str,
        date: str | None,
        stops: Iterable[Mapping[str, Any]],
    ) -> dict | None:
        """Replace title, date and stops; owner, preferences and creation stay."""
        return await self.collection.find_one_and_update(
            {"_id": itinerary_id, "user_id": user_id},
            {
                "$set": {
                    "title": title,
                    "date": date,
                    "stops": _stored_stops(stops),
                    "updated_at": _now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def delete_owned(self, itinerary_id: str, user_id: str) -> bool:
        result = await self.collection.delete_one(
            {"_id": itinerary_id, "user_id": user_id}
        )
        return result.deleted_count == 1


def _stored_stops(stops: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Stops in visit order with only the fields the collection keeps."""
    return [
        {
            "place_id": stop["place_id"],
            "order": stop["order"],
            "planned_start_time": stop.get("planned_start_time"),
            "planned_end_time": stop.get("planned_end_time"),
            "conflict_warning": stop.get("conflict_warning"),
        }
        for stop in sorted(stops, key=lambda stop: stop["order"])
    ]


def _now() -> datetime:
    # BSON dates keep milliseconds; truncate so returned documents match reads.
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)
