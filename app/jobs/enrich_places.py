"""Fill empty zone and price fields of places already stored in MongoDB.

New syncs enrich places on the way in; this command covers places imported
before that, without collecting them again. It only adds values: anything
already set, curated or not, stays as it is.

    python -m app.jobs.enrich_places
"""

import asyncio
import json

from app.core.database import connect_database
from app.repositories.place_repository import PlaceRepository
from app.services.place_enrichment import backfill_update, load_territory_locator


async def enrich_places(database) -> dict:
    """Return how many places were checked and updated, and where zones came from."""
    locator, territory_status = await load_territory_locator(database)
    repository = PlaceRepository(database)
    summary = {"checked": 0, "updated": 0, "zones": 0, "prices": 0}
    for place in await repository.find_all():
        summary["checked"] += 1
        update = backfill_update(place, locator)
        if update is None:
            continue
        await repository.apply_update(place["_id"], update)
        fields = update["$set"]
        summary["updated"] += 1
        summary["zones"] += "region_zone" in fields
        summary["prices"] += any(key.startswith("admission.") for key in fields)
    return {**summary, "territory": territory_status}


async def run() -> None:
    async with connect_database() as database:
        result = await enrich_places(database)
    print(json.dumps(result, ensure_ascii=False))


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
