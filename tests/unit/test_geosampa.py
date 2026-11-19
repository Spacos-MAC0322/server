from copy import deepcopy
from dataclasses import replace

import httpx
import pytest

from app.core.config import Settings
from app.core.http_client import ExternalAPIResponseError, ExternalAPIStatusError
from app.integrations.geosampa import (
    GeoSampaClient,
    fetch_subprefectures,
    parse_subprefectures,
)

BASE = "https://geo.example.com"
SETTINGS = Settings(geosampa_enabled=True, geosampa_base_url=BASE)


def square(lng, lat, size=0.1):
    return [
        [lng, lat],
        [lng + size, lat],
        [lng + size, lat + size],
        [lng, lat + size],
        [lng, lat],
    ]


def feature(code, abbreviation, name, zone, region, geometry):
    return {
        "type": "Feature",
        "properties": {
            "cd_subprefeitura": code,
            "sg_subprefeitura": abbreviation,
            "nm_subprefeitura": name,
            "nm_regiao_05": zone,
            "nm_regiao_08": region,
        },
        "geometry": geometry,
    }


LAYER = {
    "type": "FeatureCollection",
    "features": [
        feature(
            "09", "SE", "SE", "Centro", "Centro",
            {"type": "Polygon", "coordinates": [square(-46.7, -23.6)]},
        ),
        feature(
            "25", "MO", "MOOCA", "Leste", "Leste 1",
            {"type": "MultiPolygon", "coordinates": [[square(-46.6, -23.6)]]},
        ),
    ],
}  # fmt: skip


def test_parse_keeps_zone_region_and_official_names():
    centro, mooca = parse_subprefectures(LAYER)
    assert centro == {
        "code": "09",
        "name": "Sé",
        "zone": "Centro",
        "region": "Centro",
        "geometry": {"type": "Polygon", "coordinates": [square(-46.7, -23.6)]},
    }
    assert mooca["name"] == "Mooca"
    assert mooca["zone"] == "Leste"
    assert mooca["region"] == "Leste 1"
    assert mooca["geometry"]["type"] == "MultiPolygon"


@pytest.mark.parametrize(
    "patch",
    [
        lambda layer: layer.update(features=[]),
        lambda layer: layer.pop("features"),
        lambda layer: layer["features"][0]["properties"].update(nm_regiao_05="Z"),
        lambda layer: layer["features"][0]["properties"].update(nm_subprefeitura=""),
        lambda layer: layer["features"][0].update(geometry={"type": "Point"}),
        lambda layer: layer["features"][0]["geometry"].update(
            coordinates=[[[-46.7, -23.6], [-46.6, -23.6], [-46.7, -23.6]]]
        ),
        lambda layer: layer["features"][0]["geometry"].update(
            coordinates=[[[float("nan"), -23.6]] * 4]
        ),
    ],
)
def test_parse_rejects_unusable_layers(patch):
    layer = deepcopy(LAYER)
    patch(layer)
    with pytest.raises(ExternalAPIResponseError):
        parse_subprefectures(layer)


async def test_fetch_requests_the_layer_in_wgs84():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=LAYER)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await fetch_subprefectures(client=http, settings=SETTINGS)

    assert [item["code"] for item in result] == ["09", "25"]
    (request,) = requests
    assert str(request.url.copy_with(query=None)) == f"{BASE}/geoserver/geoportal/wfs"
    assert request.url.params["typeNames"] == "geoportal:subprefeitura"
    assert request.url.params["srsName"] == "EPSG:4326"
    assert request.url.params["outputFormat"] == "application/json"


async def test_upstream_failure_raises_external_api_error():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    ) as http:
        client = GeoSampaClient(http, SETTINGS)
        client.api._retry = replace(client.api._retry, max_attempts=1)
        with pytest.raises(ExternalAPIStatusError):
            await client.fetch_subprefectures()


async def test_disabled_client_does_not_call_upstream():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("called"))
    ) as http:
        with pytest.raises(ValueError, match="GEOSAMPA_ENABLED"):
            await GeoSampaClient(http, Settings()).fetch_subprefectures()


def test_default_base_url_points_to_geosampa():
    assert str(Settings().geosampa_base_url).startswith(
        "https://wfs.geosampa.prefeitura.sp.gov.br"
    )
