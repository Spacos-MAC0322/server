"""Read persisted itineraries and opening hours for their places."""

from collections.abc import Collection

from pymongo.asynchronous.database import AsyncDatabase


class ItineraryRepository:
    def __init__(self, database: AsyncDatabase):
        self.collection = database.itineraries
        self.places = database.places

    async def find_by_id(self, itinerary_id: str) -> dict | None:
        return await self.collection.find_one({"_id": itinerary_id})

    async def find_opening_hours(self, place_ids: Collection[str]) -> dict[str, dict]:
        places = await self.places.find(
            {"_id": {"$in": list(place_ids)}}, {"opening_hours": 1}
        ).to_list()
        return {
            place["_id"]: place["opening_hours"]
            for place in places
            if isinstance(place.get("opening_hours"), dict)
        }
