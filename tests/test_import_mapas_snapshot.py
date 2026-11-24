"""Verified snapshots and publication scoped to their imported source keys."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.database import get_database, initialize_database
from app.integrations.mapas_culturais import normalize_space
from app.jobs import sync_places as sync_job
from app.main import app
from app.scripts import import_mapas_snapshot as command
from app.scripts.export_mapas_spaces import checksum

SOURCE = "https://saas.mapas.cultura.gov.br"


def record(identity):
    return {
        "id": identity,
        "name": f"Museu {identity}",
        "status": 1,
        "En_Municipio": "São Paulo",
        "location": {"longitude": -46.63, "latitude": -23.55},
    }


@pytest.fixture
def snapshot(tmp_path):
    raw = tmp_path / "spaces_raw.json"
    raw.write_text(json.dumps([record(i) for i in range(1, 6)]), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "complete": True,
                "raw_sha256": checksum(raw),
                "source_url": SOURCE,
            }
        ),
        encoding="utf-8",
    )
    pointer = tmp_path / "latest.json"
    pointer.write_text(
        json.dumps(
            {
                "raw_file": raw.name,
                "manifest_file": manifest.name,
                "raw_sha256": checksum(raw),
                "source_url": SOURCE,
            }
        ),
        encoding="utf-8",
    )
    return pointer, raw, manifest


@pytest.mark.parametrize(
    "problem", ["partial", "raw_changed", "pointer_hash", "source"]
)
async def test_invalid_snapshot_stops_before_import(snapshot, monkeypatch, problem):
    pointer, raw, manifest = snapshot
    importer = AsyncMock()
    monkeypatch.setattr(command, "sync_places", importer)
    if problem == "raw_changed":
        raw.write_text("[]", encoding="utf-8")
    else:
        target = manifest if problem == "partial" else pointer
        content = json.loads(target.read_text(encoding="utf-8"))
        key, value = {
            "partial": ("complete", False),
            "pointer_hash": ("raw_sha256", "incorrect"),
            "source": ("source_url", "https://example.com"),
        }[problem]
        target.write_text(json.dumps(content | {key: value}), encoding="utf-8")
    with pytest.raises(command.InvalidSnapshotError):
        await command.import_snapshot(pointer)
    importer.assert_not_awaited()


async def test_default_import_does_not_publish(snapshot, monkeypatch):
    pointer, raw, _ = snapshot
    importer = AsyncMock(return_value={"status": "success", "imported": 5})
    monkeypatch.setattr(command, "sync_places", importer)
    database = AsyncMock(side_effect=AssertionError("Publication was not requested"))
    monkeypatch.setattr(command, "connect_database", database)
    result = await command.import_snapshot(pointer)
    importer.assert_awaited_once_with(input_path=raw, source_url=SOURCE)
    database.assert_not_called()
    assert result["published"] == 0


async def test_publication_changes_only_imported_review_places_with_valid_location(
    snapshot, db, monkeypatch
):
    pointer, _, _ = snapshot
    places = [normalize_space(record(i), SOURCE) for i in range(1, 7)]
    places[0]["_id"] = "existing-identity"
    places[1]["publication_status"] = "hidden"
    places[2]["data_quality"]["location_valid"] = False
    places[3]["location"] = None
    places[4]["publication_status"] = "published"
    await db.places.insert_many(places)
    before = await db.places.find().sort("_id", 1).to_list()
    monkeypatch.setattr(
        command, "sync_places", AsyncMock(return_value={"status": "success"})
    )

    @asynccontextmanager
    async def database():
        yield db

    monkeypatch.setattr(command, "connect_database", database)
    result = await command.import_snapshot(pointer, publish_imported=True)
    assert result["published"] == 1
    before_by_id = {place["_id"]: place for place in before}
    for place in await db.places.find().to_list():
        expected = before_by_id[place["_id"]]
        if place["_id"] == "existing-identity":
            expected = expected | {"publication_status": "published"}
        assert place == expected


async def test_failed_sync_never_publishes(snapshot, monkeypatch):
    pointer, _, _ = snapshot
    monkeypatch.setattr(
        command, "sync_places", AsyncMock(side_effect=RuntimeError("failed"))
    )
    database = AsyncMock()
    monkeypatch.setattr(command, "connect_database", database)
    with pytest.raises(RuntimeError, match="failed"):
        await command.import_snapshot(pointer, publish_imported=True)
    database.assert_not_called()


@pytest.mark.integration
async def test_snapshot_to_real_database_and_public_places(
    snapshot, real_db, monkeypatch
):
    pointer, _, _ = snapshot
    await initialize_database(real_db)
    existing = normalize_space(record(1), SOURCE)
    existing["_id"] = "older-identity"
    await real_db.places.insert_many([existing, normalize_space(record(6), SOURCE)])

    @asynccontextmanager
    async def database():
        yield real_db

    monkeypatch.setenv("GEOSAMPA_ENABLED", "false")
    monkeypatch.setattr(sync_job, "connect_database", database)
    monkeypatch.setattr(command, "connect_database", database)
    first = await command.import_snapshot(pointer)
    assert first["sync"]["status"] == "success"
    assert first["published"] == 0
    assert await real_db.places.count_documents({}) == 6
    assert (
        await real_db.places.count_documents({"publication_status": "published"}) == 0
    )

    second = await command.import_snapshot(pointer, publish_imported=True)
    assert second["sync"]["imported"] == 5
    assert second["published"] == 5
    assert await real_db.places.count_documents({}) == 6
    assert (await real_db.places.find_one({"_id": "older-identity"}))[
        "publication_status"
    ] == "published"
    assert (await real_db.places.find_one({"_id": "mapas_saas:space:6"}))[
        "publication_status"
    ] == "review"

    monkeypatch.setitem(app.dependency_overrides, get_database, lambda: real_db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/places")
    assert response.status_code == 200
    assert len(response.json()) == 5
