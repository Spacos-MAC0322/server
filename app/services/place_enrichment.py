"""Enrichment stage of the sync pipeline (DA05): what open data leaves empty.

Zone and subprefeitura come from GeoSampa boundaries; prices come from the
source's own free text. Values are only added: stored data, including curated
fields (DA06), is never overwritten by the backfill.
"""

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.core.config import Settings, get_settings
from app.core.geo import geometry_bounds, point_in_geometry
from app.core.http_client import ExternalAPIError
from app.core.price_text import parse_price_text
from app.integrations.geosampa import (
    SERVICE,
    SUBPREFECTURE_LAYER,
    Subprefecture,
    fetch_subprefectures,
)
from app.repositories.territory_repository import TerritoryRepository

logger = logging.getLogger(__name__)
PRICE_FIELDS = ("is_free", "price_min", "price_max")


class TerritoryLocator:
    """Find the subprefeitura containing a point; bounding boxes skip most tests."""

    def __init__(self, subprefectures: Iterable[Subprefecture]):
        self._areas = [(geometry_bounds(s["geometry"]), s) for s in subprefectures]

    def locate(self, lat: float, lng: float) -> Subprefecture | None:
        for (min_lat, min_lng, max_lat, max_lng), area in self._areas:
            if (
                min_lat <= lat <= max_lat
                and min_lng <= lng <= max_lng
                and point_in_geometry(lat, lng, area["geometry"])
            ):
                return area
        return None


def _point(place: Mapping[str, Any]) -> tuple[float, float] | None:
    location = place.get("location")
    if not isinstance(location, Mapping):
        return None
    try:
        lng, lat = location["coordinates"]
    except (TypeError, KeyError, ValueError):
        return None
    if type(lat) not in (int, float) or type(lng) not in (int, float):
        return None
    return lat, lng


def _subprefecture_source(area: Subprefecture) -> dict:
    return {
        "provider": SERVICE,
        "external_id": f"subprefeitura:{area['code']}",
        "layer": SUBPREFECTURE_LAYER,
    }


def enrich_place(place: dict, locator: TerritoryLocator | None) -> dict:
    """A copy with zone, subprefeitura and their GeoSampa source, when located."""
    point = _point(place)
    area = locator.locate(*point) if locator and point else None
    if area is None:
        return place
    territory = {
        **(place.get("territory") or {}),
        "subprefecture": area["name"],
        "region": area["zone"],
    }
    sources = [
        source
        for source in place.get("sources", [])
        if not (
            source.get("provider") == SERVICE
            and source.get("layer") == SUBPREFECTURE_LAYER
        )
    ]
    return {
        **place,
        "region_zone": area["zone"],
        "territory": territory,
        "sources": [*sources, _subprefecture_source(area)],
    }


def backfill_update(place: Mapping[str, Any], locator: TerritoryLocator | None):
    """MongoDB update that fills only empty zone and price fields, or None."""
    fields: dict[str, Any] = {}
    add: dict[str, Any] = {}
    point = _point(place)
    area = locator.locate(*point) if locator and point else None
    if area is not None and place.get("region_zone") is None:
        territory = place.get("territory") or {}
        fields["region_zone"] = area["zone"]
        if not territory.get("subprefecture"):
            fields["territory.subprefecture"] = area["name"]
        if not territory.get("region"):
            fields["territory.region"] = area["zone"]
        add["sources"] = _subprefecture_source(area)
    admission = place.get("admission") or {}
    if all(admission.get(key) is None for key in PRICE_FIELDS):
        price = parse_price_text(admission.get("price_text"))
        fields.update(
            {
                f"admission.{key}": value
                for key, value in price.items()
                if value is not None
            }
        )
    if not fields:
        return None
    return {"$set": fields, **({"$addToSet": add} if add else {})}


Fetch = Callable[..., Awaitable[list[Subprefecture]]]


async def load_territory_locator(
    database: AsyncDatabase,
    *,
    settings: Settings | None = None,
    fetch: Fetch | None = None,
) -> tuple[TerritoryLocator | None, str]:
    """Current GeoSampa boundaries, else the last stored ones, plus their origin.

    Status: "geosampa", "stored" (GeoSampa disabled or unavailable), "disabled"
    or "unavailable" (no boundaries at all). Failures never stop the sync.
    """
    settings = settings or get_settings()
    repository = TerritoryRepository(database)
    if settings.geosampa_enabled:
        try:
            subprefectures = await (fetch or fetch_subprefectures)(settings=settings)
        except (ExternalAPIError, TimeoutError, OSError) as error:
            logger.warning("GeoSampa unavailable: %s", type(error).__name__)
        else:
            try:
                await repository.save_subprefectures(subprefectures)
            except PyMongoError as error:
                logger.warning("Boundaries not stored: %s", type(error).__name__)
            return TerritoryLocator(subprefectures), "geosampa"
    stored = await repository.find_subprefectures()
    if stored:
        return TerritoryLocator(stored), "stored"
    return None, "unavailable" if settings.geosampa_enabled else "disabled"
