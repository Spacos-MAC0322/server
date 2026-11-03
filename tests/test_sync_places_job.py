import json
from argparse import Namespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import ExternalServiceUnavailable
from app.integrations.mapas_culturais import MapasCulturaisClient
from app.jobs import sync_places as job
from app.scripts import sync_spaces as legacy

BASE = "https://saas.mapas.cultura.gov.br"
RAW = {
    "id": 17,
    "name": " Museu ",
    "status": 1,
    "En_Municipio": "São Paulo",
    "location": {"longitude": "-46.63", "latitude": "-23.55"},
    "type": {"name": "Museu"},
    "horario": "10h–18h",
    "preco": "R$ 20",
}


@pytest.fixture
def connection(monkeypatch, db):
    state = {"opened": 0, "closed": 0}

    @asynccontextmanager
    async def connect():
        state["opened"] += 1
        try:
            yield db
        finally:
            state["closed"] += 1

    monkeypatch.setattr(job, "connect_database", connect)
    return state


def adapter(handler):
    return MapasCulturaisClient(
        Settings(mapas_culturais_enabled=True, mapas_culturais_base_url=BASE),
        transport=httpx.MockTransport(handler),
    )


async def test_api_pipeline_normalizes_and_reimports_without_duplicates(db, connection):
    def handler(request):
        rows = [RAW, dict(RAW, id=18, En_Municipio="Guarulhos")]
        return httpx.Response(
            200, json=rows if request.url.params["@page"] == "1" else []
        )

    for _ in range(2):
        result = await job.sync_places(client=adapter(handler))
        assert result["status"] == "success"
        assert result["imported"] == 1
        assert result["source"] == "api"
    assert await db.places.count_documents({}) == 1
    place = await db.places.find_one({})
    assert place["name"] == "Museu"
    assert place["location"]["coordinates"] == [-46.63, -23.55]
    assert place["opening_hours"]["text"] == "10h–18h"
    assert place["admission"]["price_text"] == "R$ 20"
    assert place["publication_status"] == "review"
    assert connection == {"opened": 2, "closed": 2}


async def test_snapshot_uses_same_normalizer(tmp_path, db, connection):
    path = tmp_path / "spaces.json"
    path.write_text(json.dumps([RAW, dict(RAW, id=18, name=" ")]), encoding="utf-8")
    result = await job.sync_places(input_path=path, source_url=BASE)
    assert result["source"] == "snapshot"
    assert result["imported"] == 1
    assert (await db.places.find_one({}))["source_keys"] == ["mapas_saas:space:17"]


@pytest.mark.parametrize("content", ["{}", "[null]", "invalid json"])
async def test_bad_snapshot_does_not_connect(tmp_path, connection, content):
    path = tmp_path / "spaces.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        await job.sync_places(input_path=path, source_url=BASE)
    assert connection["opened"] == 0


async def test_snapshot_options_require_unambiguous_source(tmp_path, connection):
    path = tmp_path / "spaces.json"
    for options in (
        {"input_path": path},
        {"input_path": path, "source_url": "not a URL"},
        {"source_url": BASE},
        {"input_path": path, "source_url": BASE, "client": AsyncMock()},
    ):
        with pytest.raises(ValueError):
            await job.sync_places(**options)
    assert connection["opened"] == 0


async def test_failed_later_page_records_error_and_closes_connection(db, connection):
    def handler(request):
        if request.url.params["@page"] == "1":
            return httpx.Response(200, json=[RAW])
        return httpx.Response(503)

    await db.places.insert_one({"_id": "existing", "name": "Preservar"})
    with pytest.raises(ExternalServiceUnavailable):
        await job.sync_places(client=adapter(handler))
    assert await db.places.count_documents({}) == 1
    result = await db.sync_runs.find_one({})
    assert result["status"] == "error"
    assert result["imported"] == 0
    assert connection == {"opened": 1, "closed": 1}


async def test_default_cli_and_legacy_share_the_job(monkeypatch, capsys):
    mocked = AsyncMock(return_value={"status": "success", "imported": 3})
    monkeypatch.setattr(job, "sync_places", mocked)
    await legacy.run(Namespace(input=None, source_url=None))
    mocked.assert_awaited_once_with(input_path=None, source_url=None)
    assert json.loads(capsys.readouterr().out)["imported"] == 3
    assert legacy.main is job.main


def test_cli_parses_snapshot_options(monkeypatch):
    mocked = AsyncMock()
    monkeypatch.setattr(job, "run", mocked)
    monkeypatch.setattr(
        "sys.argv", ["sync_places", "--input", "sample.json", "--source-url", BASE]
    )
    job.main()
    args = mocked.await_args.args[0]
    assert str(args.input) == "sample.json"
    assert args.source_url == BASE
