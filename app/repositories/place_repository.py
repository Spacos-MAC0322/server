"""Persistence and map-area queries for normalized places."""

import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase


class PlaceRepository:
    def __init__(self, database: AsyncDatabase):
        # Connections, validators and indexes belong to core.database.
        self.collection = database.places
        self.curations = database.place_curations

    async def find_curations(self, place_ids: Iterable[str]) -> dict[str, dict]:
        """Read badge flags in one batch; full curation evidence stays in MongoDB."""
        identities = list(dict.fromkeys(place_ids))
        if not identities:
            return {}
        rows = await self.curations.find(
            {"place_id": {"$in": identities}},
            {
                "_id": 0,
                "place_id": 1,
                "pet_friendly": 1,
                "family_friendly": 1,
                "lgbtqia_inclusion": 1,
                "hidden_gem": 1,
            },
        ).to_list()
        return {row["place_id"]: row for row in rows}

    async def upsert_many(
        self,
        places: Iterable[Mapping[str, Any]],
        *,
        on_progress: Callable[[int], None] | None = None,
    ) -> int:
        """Insert/update in order, returning the number successfully processed.

        Input is normalized storage documents, not frontend response models.
        Reimports preserve identity, publication status, other sources and curated
        fields. Writes are sequential, not a transaction. On failure, the original
        exception propagates; on_progress reports completed writes for sync_runs.
        """
        imported = 0
        for place in places:
            identity = place["_id"]
            keys = place["source_keys"]
            incoming_sources = place["sources"]
            if not isinstance(identity, str) or not identity.strip():
                raise ValueError("Place _id must be a nonblank string")
            if (
                not isinstance(keys, list)
                or not keys
                or any(not isinstance(k, str) or not k.strip() for k in keys)
            ):
                raise ValueError("Place source_keys must be a nonempty list of strings")
            if (
                not isinstance(incoming_sources, list)
                or not incoming_sources
                or any(
                    not isinstance(s, Mapping)
                    or not isinstance(s.get("provider"), str)
                    or not isinstance(s.get("external_id"), str)
                    for s in incoming_sources
                )
            ):
                raise ValueError("Place sources must identify provider and external_id")
            matches = (
                await self.collection.find(
                    {
                        "$or": [
                            {"_id": identity},
                            {"source_keys": {"$in": keys}},
                        ]
                    }
                )
                .limit(2)
                .to_list()
            )
            if len(matches) > 1:
                raise ValueError(
                    "Source keys identify multiple places; manual merge required"
                )
            existing = matches[0] if matches else None
            values = {
                k: v
                for k, v in place.items()
                if k
                not in {
                    "_id",
                    "source_keys",
                    "sources",
                    "publication_status",
                }
            }
            sources = {
                (s["provider"], s["external_id"]): dict(s)
                for s in [
                    *(existing.get("sources", []) if existing else []),
                    *incoming_sources,
                ]
            }
            if (
                existing
                and existing.get("data_quality", {}).get("detail_level") == "curated"
            ):
                values = {}
            values["sources"] = list(sources.values())
            await self.collection.update_one(
                {"_id": existing["_id"] if existing else identity},
                {
                    "$set": values,
                    "$setOnInsert": {"_id": identity, "publication_status": "review"},
                    "$addToSet": {"source_keys": {"$each": keys}},
                },
                upsert=True,
            )
            imported += 1
            if on_progress is not None:
                on_progress(imported)
        return imported

    async def find_all(self) -> list[dict]:
        """Return storage documents of every status, ordered by stable identity."""
        return await self.collection.find({}).sort("_id", 1).to_list()

    async def find_in_bounds(
        self,
        sw_lat: float,
        sw_lng: float,
        ne_lat: float,
        ne_lng: float,
    ) -> list[dict]:
        """Return places within a local GeoJSON polygon, not ordered by distance.

        Arguments use latitude/longitude; MongoDB coordinates use longitude first.
        Bounds must describe a nonempty local area without crossing the date line.
        Missing/null locations are naturally excluded. Publication filtering and
        response schemas belong to the calling service, as with find_all().
        """
        for name, value, limit in (
            ("sw_lat", sw_lat, 90),
            ("ne_lat", ne_lat, 90),
            ("sw_lng", sw_lng, 180),
            ("ne_lng", ne_lng, 180),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not -limit <= value <= limit
            ):
                raise ValueError(
                    f"{name} must be finite and within [-{limit}, {limit}]"
                )
        if sw_lat >= ne_lat or sw_lng >= ne_lng:
            raise ValueError("Southwest bounds must be below northeast bounds")
        if ne_lng - sw_lng >= 180:
            raise ValueError("Local bounds must span less than 180 degrees longitude")
        ring = [
            [sw_lng, sw_lat],
            [ne_lng, sw_lat],
            [ne_lng, ne_lat],
            [sw_lng, ne_lat],
            [sw_lng, sw_lat],
        ]
        return (
            await self.collection.find(
                {
                    "location": {
                        "$geoWithin": {
                            "$geometry": {"type": "Polygon", "coordinates": [ring]},
                        }
                    }
                }
            )
            .sort("_id", 1)
            .to_list()
        )
