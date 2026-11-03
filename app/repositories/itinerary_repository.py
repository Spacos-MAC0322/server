"""Read persisted itineraries without changing their stored weather or stops."""

from pymongo.asynchronous.database import AsyncDatabase


class ItineraryRepository:
    def __init__(self, database: AsyncDatabase):
        self.collection = database.itineraries

    async def find_by_id(self, itinerary_id: str) -> dict | None:
        return await self.collection.find_one({"_id": itinerary_id})
