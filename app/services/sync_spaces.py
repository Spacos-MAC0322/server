"""Persist normalized spaces and record observable synchronization outcomes."""

from datetime import UTC, datetime
from uuid import uuid4

from app.integrations.mapas_culturais import MapasCulturaisClient
from app.repositories.place_repository import PlaceRepository


async def sync_spaces(database, *, client=None, spaces=None, source="api") -> dict:
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
