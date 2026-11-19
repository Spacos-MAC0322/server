"""Persist normalized spaces and record observable synchronization outcomes."""

from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import Settings
from app.integrations.mapas_culturais import MapasCulturaisClient
from app.repositories.place_repository import PlaceRepository
from app.services.place_enrichment import enrich_place, load_territory_locator


async def sync_spaces(
    database,
    *,
    client=None,
    spaces=None,
    source="api",
    settings: Settings | None = None,
) -> dict:
    """Collect (or receive), enrich and save spaces, recording the run.

    Zones come from GeoSampa boundaries, or the last stored ones; without any,
    places are saved unenriched and sync_runs says so in enrichment.territory.
    """
    run = {
        "_id": str(uuid4()),
        "provider": "mapas_culturais",
        "started_at": datetime.now(UTC),
        "status": "running",
        "imported": 0,
        "errors": [],
        "source": source,
    }
    await database.sync_runs.insert_one(run.copy())
    try:
        if spaces is None:
            spaces = await (client or MapasCulturaisClient()).fetch_spaces()
        locator, territory_status = await load_territory_locator(
            database, settings=settings
        )
        run["enrichment"] = {"territory": territory_status}
        spaces = [enrich_place(space, locator) for space in spaces]

        def record_progress(imported: int) -> None:
            run["imported"] = imported

        run["imported"] = await PlaceRepository(database).upsert_many(
            spaces,
            on_progress=record_progress,
        )
        run["status"] = "success"
    except Exception as error:
        run["status"] = "partial" if run["imported"] else "error"
        # Error classes give diagnostics without logging URLs or credentials.
        run["errors"] = [type(error).__name__]
        raise
    finally:
        run["finished_at"] = datetime.now(UTC)
        await database.sync_runs.update_one(
            {"_id": run["_id"]},
            {"$set": {k: v for k, v in run.items() if k != "_id"}},
        )
    return run
