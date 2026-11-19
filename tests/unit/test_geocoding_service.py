"""Ranking and de-duplication of address search matches."""

from unittest.mock import AsyncMock, MagicMock

from app.core.config import Settings
from app.services.geocoding import GeocodingService, rank_candidates


def candidate(label, lat, lng, importance=0.0534, neighborhood=None):
    return {
        "label": label,
        "neighborhood": neighborhood,
        "lat": lat,
        "lng": lng,
        "importance": importance,
    }


# Real answer for "Rua dos Pinheiros, 500": same importance, unstable order.
PINHEIROS = [
    candidate("Rua dos Pinheiros", -23.4408, -46.5758, neighborhood="Jardim Fontalis"),
    candidate("Rua dos Pinheiros", -23.7952, -46.7417, neighborhood="Parelheiros"),
    candidate("Rua dos Pinheiros", -23.5653, -46.6822, neighborhood="Jardim Europa"),
    candidate("Rua dos Pinheiros", -23.5677, -46.6930, neighborhood="Vila Madalena"),
    candidate("Rua dos Pinheiros", -23.3918, -46.6408, neighborhood="Roseira"),
]


def test_equal_importance_ties_go_to_the_most_central_match():
    ranked = rank_candidates(PINHEIROS)

    assert [c["neighborhood"] for c in ranked] == [
        "Jardim Europa",
        "Jardim Fontalis",
        "Roseira",
        "Parelheiros",
    ]


def test_matches_within_the_same_area_collapse_into_the_most_relevant():
    museum = candidate("MASP", -23.5614, -46.6559, importance=0.498)
    cafe = candidate("Café do MASP", -23.5618, -46.6558, importance=0.0001)

    assert rank_candidates([cafe, museum]) == [museum]


def test_at_most_five_distinct_places_are_returned():
    spread = [candidate(f"Rua {i}", -23.40 - i * 0.03, -46.63) for i in range(8)]
    assert len(rank_candidates(spread)) == 5


async def test_service_returns_schemas_in_ranked_order():
    client = MagicMock()
    client.settings = Settings(nominatim_enabled=True)
    client.search = AsyncMock(return_value=PINHEIROS[:3])
    service = GeocodingService(client)

    results = await service.search_address("Rua dos Pinheiros")

    client.search.assert_awaited_once_with("Rua dos Pinheiros")
    assert service.enabled is True
    assert results[0].model_dump() == {
        "label": "Rua dos Pinheiros",
        "neighborhood": "Jardim Europa",
        "lat": -23.5653,
        "lng": -46.6822,
    }
