"""Subprefecture boundaries kept in MongoDB, so the sync works if GeoSampa is down."""

from collections.abc import Iterable

from pymongo.asynchronous.database import AsyncDatabase

from app.integrations.geosampa import SERVICE, SUBPREFECTURE_LAYER, Subprefecture

KIND = "subprefecture"


def territory_id(code: str) -> str:
    return f"{SERVICE}:subprefeitura:{code}"


class TerritoryRepository:
    def __init__(self, database: AsyncDatabase):
        self.collection = database.territories

    async def save_subprefectures(self, subprefectures: Iterable[Subprefecture]) -> int:
        """Upsert each boundary by GeoSampa code; returns how many were written."""
        saved = 0
        for item in subprefectures:
            await self.collection.update_one(
                {"_id": territory_id(item["code"])},
                {
                    "$set": {
                        "kind": KIND,
                        "code": item["code"],
                        "name": item["name"],
                        "region": item["zone"],
                        "region_08": item["region"],
                        "geometry": item["geometry"],
                        "sources": [
                            {
                                "provider": SERVICE,
                                "external_id": f"subprefeitura:{item['code']}",
                                "layer": SUBPREFECTURE_LAYER,
                            }
                        ],
                    }
                },
                upsert=True,
            )
            saved += 1
        return saved

    async def find_subprefectures(self) -> list[Subprefecture]:
        documents = await self.collection.find({"kind": KIND}).sort("_id", 1).to_list()
        return [
            {
                "code": document["code"],
                "name": document["name"],
                "zone": document["region"],
                "region": document.get("region_08"),
                "geometry": document["geometry"],
            }
            for document in documents
        ]
