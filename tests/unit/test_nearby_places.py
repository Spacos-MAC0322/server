"""Test the distance query and preserve its ordering through the place service."""

from unittest.mock import AsyncMock, MagicMock

from app.repositories.place_repository import PlaceRepository
from app.services.place import PlaceService


async def test_nearby_repository_limits_distance_from_a_geojson_point():
    database = MagicMock()
    cursor = database.places.find.return_value
    cursor.to_list = AsyncMock(
        return_value=[{"_id": "z-nearest"}, {"_id": "a-farther"}]
    )

    places = await PlaceRepository(database).find_nearby(-23.55, -46.63, 500)

    database.places.find.assert_called_once_with(
        {
            "location": {
                "$nearSphere": {
                    "$geometry": {"type": "Point", "coordinates": [-46.63, -23.55]},
                    "$maxDistance": 500,
                }
            }
        }
    )
    assert [place["_id"] for place in places] == ["z-nearest", "a-farther"]


async def test_nearby_service_converts_radius_and_preserves_distance_order():
    service = PlaceService(MagicMock())
    repository = MagicMock(spec=PlaceRepository)
    service.repository = repository
    repository.find_nearby.return_value = [
        {
            "_id": name,
            "name": name,
            "publication_status": status,
            "location": {"type": "Point", "coordinates": [lng, -23.55]},
        }
        for name, status, lng in (
            ("z-nearest", "published", -46.6301),
            ("hidden", "hidden", -46.631),
            ("a-farther", "published", -46.633),
        )
    ]
    repository.find_curations.return_value = {
        "a-farther": {"place_id": "a-farther", "family_friendly": True}
    }

    places = await service.list_nearby_places(lat=-23.55, lng=-46.63, radius_km=0.5)

    repository.find_nearby.assert_awaited_once_with(-23.55, -46.63, 500)
    assert [place.id for place in places] == ["z-nearest", "a-farther"]
    assert places[1].badges.family_friendly is True
