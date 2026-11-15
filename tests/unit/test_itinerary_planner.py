from unittest.mock import Mock

import pytest

from app.integrations.open_meteo import OpenMeteoClient
from app.repositories.itinerary_repository import ItineraryRepository
from app.services.itinerary_planner import ItineraryPlanner

CLOSED_WARNING = "O local estará fechado no horário planejado."


@pytest.mark.parametrize(
    ("date", "start_time", "expected_warning"),
    [
        pytest.param("2026-11-16", "12:00", CLOSED_WARNING, id="closed-on-monday"),
        pytest.param("2026-11-17", "18:00", CLOSED_WARNING, id="at-closing-time"),
        pytest.param("2026-11-17", "12:00", None, id="within-opening-hours"),
    ],
)
async def test_museum_opening_hours_warning(date, start_time, expected_warning):
    repository = Mock(spec=ItineraryRepository)
    repository.find_opening_hours.return_value = {
        "museum": {
            "text": "Terça a domingo, das 10h às 18h. Fechado às segundas-feiras."
        }
    }
    planner = ItineraryPlanner(Mock(spec=OpenMeteoClient))
    itinerary = {
        "date": date,
        "stops": [{"place_id": "museum", "order": 1, "planned_start_time": start_time}],
    }

    checked = await planner.attach_opening_hours_conflicts(itinerary, repository)

    (stop,) = checked["stops"]
    assert stop.get("conflict_warning") == expected_warning
