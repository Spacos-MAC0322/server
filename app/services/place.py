"""Public map listings assembled from persisted places and curation flags."""

from pymongo.asynchronous.database import AsyncDatabase

from app.repositories.place_repository import PlaceRepository
from app.schemas.place import PlaceListQuery, PlaceSummarySchema


class PlaceService:
    def __init__(self, database: AsyncDatabase):
        self.repository = PlaceRepository(database)

    async def list_places(
        self,
        query: PlaceListQuery | None = None,
    ) -> list[PlaceSummarySchema]:
        query = query or PlaceListQuery()
        bounds = query.bounds
        if bounds is None:
            documents = await self.repository.find_all()
        else:
            documents = await self.repository.find_in_bounds(*bounds)
        visible = [
            doc
            for doc in documents
            if doc.get("publication_status") == "published"
            and doc.get("location") is not None
            and doc.get("data_quality", {}).get("location_valid") is not False
        ]
        curations = await self.repository.find_curations(doc["_id"] for doc in visible)
        return [
            PlaceSummarySchema.from_mongo(doc, curation=curations.get(doc["_id"]))
            for doc in visible
        ]
