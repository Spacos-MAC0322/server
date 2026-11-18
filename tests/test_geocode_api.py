"""GET /api/search/geocode: contract, validation and graceful failures."""

from dataclasses import replace

import httpx
import pytest

from app.core.cache import ResponseCache
from app.core.config import Settings, get_settings
from app.core.http_client import get_http_client
from app.integrations import nominatim
from app.main import app

ENABLED = Settings(nominatim_enabled=True)
MASP = {
    "lat": "-23.5614",
    "lon": "-46.6559",
    "name": "Museu de Arte de São Paulo",
    "importance": 0.498,
    "address": {"road": "Avenida Paulista", "suburb": "Bela Vista"},
}


@pytest.fixture
async def upstream(client, monkeypatch):
    """Replace Nominatim with a scripted handler; returns the recorded requests."""
    requests = []
    state = {"reply": httpx.Response(200, json=[MASP])}

    def handler(request):
        requests.append(request)
        reply = state["reply"]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(nominatim, "_search_cache", ResponseCache(ttl=60))
    monkeypatch.setattr(nominatim, "_throttle", nominatim.Throttle(0))
    monkeypatch.setattr(nominatim, "_RETRY", replace(nominatim._RETRY, max_attempts=1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app.dependency_overrides[get_http_client] = lambda: http
        app.dependency_overrides[get_settings] = lambda: ENABLED
        yield requests, state


async def test_geocode_returns_matches(client, upstream):
    response = await client.get("/api/search/geocode", params={"q": "MASP"})

    assert response.status_code == 200
    assert response.json() == [
        {
            "label": "Museu de Arte de São Paulo",
            "neighborhood": "Bela Vista",
            "lat": -23.5614,
            "lng": -46.6559,
        }
    ]


async def test_geocode_returns_empty_list_when_nothing_matches(client, upstream):
    _, state = upstream
    state["reply"] = httpx.Response(200, json=[])

    response = await client.get("/api/search/geocode", params={"q": "asdfgh"})

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("params", [{}, {"q": "ab"}, {"q": "x" * 201}, {"q": "    "}])
async def test_geocode_rejects_invalid_queries_before_calling_nominatim(
    client, upstream, params
):
    requests, _ = upstream

    response = await client.get("/api/search/geocode", params=params)

    assert response.status_code == 422
    assert requests == []


async def test_geocode_is_unavailable_when_disabled(client, upstream):
    requests, _ = upstream
    app.dependency_overrides[get_settings] = lambda: Settings()

    response = await client.get("/api/search/geocode", params={"q": "MASP"})

    assert response.status_code == 503
    assert "desativada" in response.json()["detail"]
    assert requests == []


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        (httpx.Response(500), 502),
        (httpx.ConnectError("offline"), 502),
        (httpx.ReadTimeout("slow"), 504),
    ],
)
async def test_geocode_reports_nominatim_failures(client, upstream, reply, status):
    _, state = upstream
    state["reply"] = reply

    response = await client.get("/api/search/geocode", params={"q": "MASP"})

    assert response.status_code == status
    assert response.json()["service"] == "nominatim"
