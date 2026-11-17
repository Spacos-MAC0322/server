"""Check how GTFS suggestions and Olho Vivo responses work together (RNF5)."""

from copy import deepcopy

import httpx
import pytest

from app.integrations.sptrans_gtfs import GTFSNetwork

from . import test_sptrans
from .test_sptrans import PREDICTION, departure

adapter = test_sptrans.adapter
feed = test_sptrans.feed

ORIGIN = (-23.55, -46.63)
DESTINATION = (-23.56, -46.63)
FALLBACK_WARNING = "Previsões em tempo real indisponíveis; estimativa baseada no GTFS."


async def test_suggests_connected_line_and_filters_unrelated_arrivals(adapter, feed):
    prediction = deepcopy(PREDICTION)
    other_line = {**prediction["p"]["l"][0], "cl": 99, "c": "9999-10"}
    prediction["p"]["l"].insert(0, other_line)

    def handle_request(request):
        if request.method == "POST":
            assert request.url.path == "/v2.1/Login/Autenticar"
            return httpx.Response(200, json=True)
        assert request.url.path == "/v2.1/Previsao/Parada"
        assert request.url.params["codigoParada"] == "1"
        return httpx.Response(200, json=prediction)

    client = adapter(handle_request, network=GTFSNetwork.from_zip(feed()))
    connections = await client.find_connections(
        *ORIGIN, *DESTINATION, departure_at=departure()
    )

    (connection,) = connections
    assert connection["line"] == "8000-10"
    assert connection["origin_stop"]["stop_id"] == "1"
    assert connection["destination_stop"]["stop_id"] == "2"
    assert connection["estimated_duration_minutes"] == 25
    (line,) = connection["arrival_predictions"]["lines"]
    assert line["code"] == "8000-10"
    assert line["vehicles"][0]["expected_arrival"] == "08:05"
    assert FALLBACK_WARNING not in connection["warnings"]


@pytest.mark.parametrize("failure", ["503", "timeout"], ids=["http-503", "timeout"])
async def test_arrival_failure_after_login_preserves_connection_with_friendly_warning(
    adapter, feed, failure
):
    def handle_request(request):
        if request.method == "POST":
            return httpx.Response(200, json=True)
        assert request.url.path == "/v2.1/Previsao/Parada"
        if failure == "timeout":
            raise httpx.ReadTimeout("internal provider detail", request=request)
        return httpx.Response(503, text="internal provider detail")

    client = adapter(handle_request, network=GTFSNetwork.from_zip(feed()))
    connections = await client.find_connections(
        *ORIGIN, *DESTINATION, departure_at=departure()
    )

    (connection,) = connections
    assert connection["line"] == "8000-10"
    assert connection["estimated_duration_minutes"] == 25
    assert connection["arrival_predictions"] is None
    assert FALLBACK_WARNING in connection["warnings"]
    assert "internal provider detail" not in " ".join(connection["warnings"])
