"""Read place details from real MongoDB with a simulated cultural agenda."""

from dataclasses import replace
from datetime import datetime

import httpx
import pytest

from app.core.cache import ResponseCache
from app.core.config import Settings
from app.integrations import mapas_culturais
from app.integrations.mapas_culturais import MapasCulturaisClient
from app.services import place as place_service

from . import test_places_api

pytestmark = pytest.mark.integration
places_client = test_places_api.places_client
PLACE_ID = "mapas_saas:space:41"
DETAIL_URL = f"/api/places/{PLACE_ID}"


@pytest.fixture
async def published_place(places_client, real_db):
    await real_db.places.insert_one(
        {
            "_id": PLACE_ID,
            "name": "Museu do bairro",
            "publication_status": "published",
            "source_keys": [PLACE_ID],
            "sources": [{"provider": "mapas_saas", "external_id": "41"}],
            "location": {"type": "Point", "coordinates": [-46.63, -23.55]},
            "opening_hours": {
                "text": "Terça a domingo, das 10h às 18h",
                "verified": True,
            },
        }
    )
    await real_db.place_curations.insert_one(
        {"_id": "museum-curation", "place_id": PLACE_ID, "family_friendly": True}
    )


@pytest.fixture
def mock_agenda(monkeypatch, clock):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 15, 12, tzinfo=tz)

    monkeypatch.setattr(mapas_culturais, "datetime", FixedDateTime)
    monkeypatch.setattr(
        mapas_culturais, "_events_cache", ResponseCache(300, timer=clock)
    )
    monkeypatch.setattr(
        mapas_culturais, "_events_fallback", ResponseCache(3600, timer=clock)
    )
    monkeypatch.setattr(
        mapas_culturais,
        "EVENTS_RETRY",
        replace(mapas_culturais.EVENTS_RETRY, base_delay=0),
    )

    def configure(handler):
        adapter = MapasCulturaisClient(
            Settings(
                _env_file=None,
                mapas_culturais_enabled=True,
                mapas_culturais_base_url="https://saas.mapas.cultura.gov.br",
            ),
            transport=httpx.MockTransport(handler),
        )
        monkeypatch.setattr(place_service, "MapasCulturaisClient", lambda: adapter)

    return configure


async def test_place_detail_keeps_cached_programming_during_outage(
    places_client, published_place, mock_agenda, clock
):
    failing = False
    failures = []

    def agenda(request):
        assert request.url.path == "/api/event/findBySpace"
        assert request.url.params["spaceId"] == "41"
        if failing:
            failures.append(request)
            return httpx.Response(503)
        if request.url.params["@page"] != "1":
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {
                    "id": 55,
                    "name": "Exposição do acervo",
                    "status": 1,
                    "classificacaoEtaria": "Livre",
                    "shortDescription": "Seleção de fotografias do bairro",
                    "occurrences": [
                        {
                            "spaceId": 41,
                            "rule": {
                                "startsOn": request.url.params["@from"],
                                "startsAt": "14:00",
                                "endsAt": "16:00",
                                "price": "Gratuito",
                            },
                        }
                    ],
                }
            ],
        )

    mock_agenda(agenda)
    response = await places_client.get(DETAIL_URL)

    assert response.status_code == 200
    detail = response.json()
    assert detail["id"] == PLACE_ID
    assert detail["name"] == "Museu do bairro"
    assert detail["location"]["coordinates"] == [-46.63, -23.55]
    assert detail["opening_hours"]["verified"] is True
    assert detail["badges"]["family_friendly"] is True
    (event,) = detail["events"]
    assert event["title"] == "Exposição do acervo"
    assert event["starts_at"].endswith("T14:00:00-03:00")
    assert event["ends_at"].endswith("T16:00:00-03:00")
    assert event["price_text"] == "Gratuito"
    assert event["age_rating"] == "Livre"
    assert event["description"]["short"] == "Seleção de fotografias do bairro"

    clock.now = 301
    failing = True
    response = await places_client.get(DETAIL_URL)

    assert failures
    assert response.status_code == 200
    assert response.json() == detail


async def test_place_detail_is_available_when_agenda_times_out_without_cache(
    places_client, published_place, mock_agenda
):
    requests = []

    def unavailable(request):
        requests.append(request)
        raise httpx.ReadTimeout("Agenda indisponível", request=request)

    mock_agenda(unavailable)
    response = await places_client.get(DETAIL_URL)

    assert requests
    assert response.status_code == 200
    detail = response.json()
    assert detail["id"] == PLACE_ID
    assert detail["name"] == "Museu do bairro"
    assert detail["curation"]["family_friendly"] is True
    assert detail["events"] == []
