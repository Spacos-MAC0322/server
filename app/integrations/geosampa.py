"""Subprefecture boundaries of São Paulo from GeoSampa (WFS), for the sync job.

Each of the 32 subprefeituras carries its macro-region (Centro, Norte, Sul, Leste
or Oeste), so a place's coordinates give its zone. The layer changes rarely and is
read once per sync run; nothing is fetched on API requests.
"""

import math
from dataclasses import replace
from typing import Any, TypedDict

import httpx

from app.core.config import Settings, get_settings
from app.core.http_client import (
    DEFAULT_RETRY_POLICY,
    ExternalAPIClient,
    ExternalAPIResponseError,
    create_http_client,
)

SERVICE = "geosampa"
WFS_PATH = "/geoserver/geoportal/wfs"
SUBPREFECTURE_LAYER = "geoportal:subprefeitura"
ZONES = frozenset({"Centro", "Norte", "Sul", "Leste", "Oeste"})
# The whole layer is ~2 MB; a batch job can wait longer than on-demand adapters.
LAYER_TIMEOUT_SECONDS = 30.0
_RETRY = replace(DEFAULT_RETRY_POLICY, max_attempts=3, total_timeout=90.0)
# GeoSampa names are upper case without accents ("BUTANTA", "M BOI MIRIM").
# Official spellings, by GeoSampa abbreviation, where title case is not enough.
DISPLAY_NAMES = {
    "SE": "Sé",
    "BT": "Butantã",
    "MP": "São Miguel",
    "SM": "São Mateus",
    "MB": "M'Boi Mirim",
    "JT": "Jaçanã-Tremembé",
    "PJ": "Pirituba-Jaraguá",
    "FO": "Freguesia-Brasilândia",
    "AF": "Aricanduva-Formosa-Carrão",
    "CS": "Capela do Socorro",
}


class Subprefecture(TypedDict):
    code: str
    name: str
    zone: str
    region: str | None
    geometry: dict


def _point(value: Any) -> list[float]:
    lng, lat = value[0], value[1]
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (lng, lat)):
        raise ValueError("Coordinates must be finite numbers")
    return [float(lng), float(lat)]


def _geometry(value: Any) -> dict:
    kind = value["type"]
    polygons = (
        [value["coordinates"]] if kind == "Polygon" else list(value["coordinates"])
    )
    if kind not in ("Polygon", "MultiPolygon") or not polygons:
        raise ValueError("Expected a Polygon or MultiPolygon")
    clean = [[[_point(p) for p in ring] for ring in polygon] for polygon in polygons]
    if any(not polygon or len(ring) < 4 for polygon in clean for ring in polygon):
        raise ValueError("Polygon rings need at least four positions")
    if kind == "Polygon":
        return {"type": kind, "coordinates": clean[0]}
    return {"type": kind, "coordinates": clean}


def _subprefecture(feature: Any) -> Subprefecture:
    properties = feature["properties"]
    abbreviation = str(properties["sg_subprefeitura"]).strip()
    raw_name = str(properties["nm_subprefeitura"]).strip()
    zone = properties["nm_regiao_05"]
    if zone not in ZONES or not raw_name:
        raise ValueError("Unknown zone or blank name")
    region = properties.get("nm_regiao_08")
    return {
        "code": str(properties["cd_subprefeitura"]).strip(),
        "name": DISPLAY_NAMES.get(abbreviation, raw_name.title()),
        "zone": zone,
        "region": region.strip() if isinstance(region, str) and region else None,
        "geometry": _geometry(feature["geometry"]),
    }


def parse_subprefectures(data: Any) -> list[Subprefecture]:
    """Validate the whole layer; one bad feature makes the answer unusable."""
    try:
        features = data["features"]
        if not isinstance(features, list) or not features:
            raise ValueError("Expected a nonempty feature list")
        return [_subprefecture(feature) for feature in features]
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
        raise ExternalAPIResponseError(
            SERVICE, "Subprefecture layer is missing valid boundaries"
        ) from error


class GeoSampaClient:
    """Reuse the caller's HTTP client; this adapter never closes it."""

    def __init__(
        self, client: httpx.AsyncClient, settings: Settings | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.api = ExternalAPIClient(
            client,
            service=SERVICE,
            base_url=str(self.settings.geosampa_base_url),
            timeout=LAYER_TIMEOUT_SECONDS,
            retry=_RETRY,
        )

    async def fetch_subprefectures(self) -> list[Subprefecture]:
        """All subprefeituras with zone and boundary in [lng, lat] (EPSG:4326).

        Raises ValueError when GeoSampa is disabled, and ExternalAPIError when the
        service fails or answers with an unusable layer.
        """
        if not self.settings.geosampa_enabled:
            raise ValueError("Enable GEOSAMPA_ENABLED before fetching boundaries")
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": SUBPREFECTURE_LAYER,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
        }
        return parse_subprefectures(await self.api.get_json(WFS_PATH, params=params))


async def fetch_subprefectures(
    *, client: httpx.AsyncClient | None = None, settings: Settings | None = None
) -> list[Subprefecture]:
    """Convenience entry point for jobs; an optional client stays caller-owned."""
    if client is not None:
        return await GeoSampaClient(client, settings).fetch_subprefectures()
    async with create_http_client() as http:
        return await GeoSampaClient(http, settings).fetch_subprefectures()
