"""Persist normalized spaces and record observable synchronization outcomes."""

from datetime import UTC, datetime
from uuid import uuid4

from app.integrations.mapas_culturais import MapasCulturaisClient


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
        for place in spaces:
            key = place["source_keys"][0]
            existing = await database.places.find_one({"source_keys": key})
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
            sources = list(existing.get("sources", [])) if existing else []
            incoming = place["sources"][0]
            sources = [
                s
                for s in sources
                if not (
                    s.get("provider") == incoming["provider"]
                    and s.get("external_id") == incoming["external_id"]
                )
            ] + [incoming]
            # Curated fields are owned by editors, not subsequent source imports.
            if (
                existing
                and existing.get("data_quality", {}).get("detail_level") == "curated"
            ):
                values = {}
            values["sources"] = sources
            await database.places.update_one(
                {"_id": existing["_id"] if existing else place["_id"]},
                {
                    "$set": values,
                    "$setOnInsert": {
                        "_id": place["_id"],
                        "publication_status": "review",
                    },
                    "$addToSet": {"source_keys": key},
                },
                upsert=True,
            )
            run["imported"] += 1
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
