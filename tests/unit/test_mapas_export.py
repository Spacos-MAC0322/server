import json
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import ExternalServiceInvalidResponse
from app.integrations.mapas_culturais import MapasCulturaisClient
from app.scripts.export_mapas_spaces import (
    checksum,
    export_spaces,
    snapshot_from_file,
)


@pytest.fixture
def captured_sample():
    path = Path(__file__).parents[1] / "fixtures/mapas_culturais/spaces_sample.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def test_real_saved_sample_replays_offline_with_unknown_fields(captured_sample):
    client = adapter(
        lambda request: httpx.Response(
            200,
            json=captured_sample if request.url.params["@page"] == "1" else [],
        )
    )
    places = await client.fetch_spaces()
    assert len(places) == len(captured_sample) == 3
    assert any(p["opening_hours"]["text"] for p in places)
    assert any(p["location"] is None for p in places)
    assert all(p["admission"]["is_free"] is None for p in places)
    assert all(p["publication_status"] == "review" for p in places)


def record(identity=1):
    return {
        "id": identity,
        "name": "Museu",
        "status": 1,
        "En_Municipio": "São Paulo",
        "type": {"name": "Museu"},
        "location": {"longitude": "-46.63", "latitude": "-23.55"},
    }


def adapter(handler):
    return MapasCulturaisClient(
        Settings(
            mapas_culturais_enabled=True,
            mapas_culturais_base_url="https://saas.mapas.cultura.gov.br",
        ),
        transport=httpx.MockTransport(handler),
    )


async def test_explicit_select_prevents_legacy_id_only_response():
    def handler(request):
        if request.url.params["@page"] != "1":
            return httpx.Response(200, json=[])
        fields = set(request.url.params.get("@select", "").split(","))
        payload = (
            record() if {"id", "name", "location", "status"} <= fields else {"id": 1}
        )
        return httpx.Response(200, json=[payload])

    result = await adapter(handler).fetch_spaces()
    assert result[0]["name"] == "Museu"
    assert result[0]["location"]["coordinates"] == [-46.63, -23.55]


async def test_missing_selected_fields_is_an_error_not_an_empty_import():
    with pytest.raises(ExternalServiceInvalidResponse):
        await adapter(lambda r: httpx.Response(200, json=[{"id": 1}])).fetch_spaces()


async def test_export_saves_raw_normalized_and_manifest_and_reuses_cache(tmp_path):
    calls = []

    def handler(request):
        page = request.url.params["@page"]
        calls.append(page)
        rows = [record(1), record(2)] if page == "1" else []
        return httpx.Response(200, json=rows, headers={"api-metadata": '{"count":2}'})

    client = adapter(handler)
    report = await export_spaces(tmp_path, client=client)
    assert report["complete"]
    assert report["normalized"] == report["with_location"] == 2
    assert report["with_opening_hours"] == report["with_price"] == 0
    assert json.loads((tmp_path / "spaces_raw.json").read_text())[0]["name"] == "Museu"
    assert (
        json.loads((tmp_path / "places_normalized.json").read_text())[0][
            "publication_status"
        ]
        == "review"
    )
    assert (tmp_path / "pages/page_0001.json").exists()
    assert calls == ["1", "2"]
    assert await export_spaces(tmp_path, client=client) == report
    assert calls == ["1", "2"]  # Second execution makes no HTTP requests.


async def test_failure_keeps_completed_pages_and_marks_partial_capture(tmp_path):
    def handler(request):
        if request.url.params["@page"] == "1":
            return httpx.Response(200, json=[record()])
        return httpx.Response(403, text="challenge")

    report = await export_spaces(tmp_path, client=adapter(handler))
    assert not report["complete"]
    assert report["stop_reason"] == "request_failed"
    assert report["errors"] == ["ExternalServiceUnavailable"]
    assert report["unique_records"] == 1
    assert json.loads((tmp_path / "spaces_raw.json").read_text())[0]["id"] == 1


async def test_cap_is_not_presented_as_complete(tmp_path):
    report = await export_spaces(
        tmp_path,
        client=adapter(lambda r: httpx.Response(200, json=[record()])),
        max_pages=1,
    )
    assert report["stop_reason"] == "page_limit"
    assert not report["complete"]


async def test_remote_count_mismatch_is_reported(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            json=[record()] if request.url.params["@page"] == "1" else [],
            headers={"api-metadata": '{"count":20}'},
        )

    report = await export_spaces(tmp_path, client=adapter(handler))
    assert not report["complete"]
    assert report["stop_reason"] == "count_mismatch"


async def test_cache_corruption_is_detected(tmp_path):
    client = adapter(lambda r: httpx.Response(200, json=[]))
    await export_spaces(tmp_path, client=client)
    (tmp_path / "spaces_raw.json").write_text("corrupted")
    with pytest.raises(ValueError, match="checksum"):
        await export_spaces(tmp_path, client=client)


async def test_nonempty_directory_and_invalid_options_are_rejected(tmp_path):
    client = adapter(None)
    with pytest.raises(ValueError):
        await export_spaces(tmp_path, client=client, page_size=0)
    (tmp_path / "keep.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        await export_spaces(tmp_path, client=client)
    assert (tmp_path / "keep.txt").read_text() == "keep"


def downloaded(tmp_path, rows, name="espacos.json"):
    """A list saved from the browser, as /api/space/find returns it."""
    path = tmp_path / name
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


async def test_manual_download_becomes_a_complete_validated_snapshot(tmp_path):
    other_city = dict(record(3), En_Municipio="Guarulhos")
    source = downloaded(tmp_path, [record(1), record(2), record(1), other_city])
    output = tmp_path / "snapshot"

    report = await snapshot_from_file(source, output)

    assert report["complete"] is True
    assert report["origin"] == report["stop_reason"] == "manual_download"
    assert report["source_file"] == "espacos.json"
    assert report["source_sha256"] == checksum(source)
    assert report["remote_count"] is None
    assert report["received"] == 4
    assert report["unique_records"] == 3
    assert report["duplicates"] == 1
    assert report["normalized"] == report["with_location"] == 2
    assert report["excluded"] == 1
    assert report["raw_sha256"] == checksum(output / "spaces_raw.json")
    assert report["query"]["En_Municipio"] == "ILIKE(São Paulo)"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == report


@pytest.mark.parametrize(
    "content",
    ['{"error": "challenge"}', "[]", '[{"name": "sem id"}]', "<html>Just a moment"],
)
async def test_manual_download_rejects_anything_but_the_space_list(tmp_path, content):
    source = tmp_path / "espacos.json"
    source.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        await snapshot_from_file(source, tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()


async def test_manual_download_never_overwrites_a_snapshot(tmp_path):
    output = tmp_path / "snapshot"
    output.mkdir()
    (output / "keep.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        await snapshot_from_file(downloaded(tmp_path, [record()]), output)
    assert (output / "keep.txt").read_text() == "keep"
