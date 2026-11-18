"""Address search in São Paulo through Nominatim (OpenStreetMap), on demand.

The public instance allows at most one request per second, from an identified
client, and forbids search-as-you-type. So searches run only when the user submits,
answers are cached for a day and upstream calls are spaced by a shared throttle.
"""

import asyncio
import json
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TypedDict

import httpx

from app.core.cache import ResponseCache, make_cache_key
from app.core.config import Settings, get_settings
from app.core.http_client import (
    DEFAULT_RETRY_POLICY,
    ExternalAPIClient,
    ExternalAPIResponseError,
)

SERVICE = "nominatim"
SEARCH_PATH = "/search"
SEARCH_CACHE_TTL = 24 * 3600
UPSTREAM_LIMIT = 8
# Região metropolitana com folga, igual aos limites do mapa no frontend.
SOUTH, NORTH, WEST, EAST = -24.2, -23.1, -47.2, -46.1
_NEIGHBORHOOD_KEYS = ("suburb", "neighbourhood", "quarter", "city_district")
_RETRY = replace(DEFAULT_RETRY_POLICY, max_attempts=2, total_timeout=8.0)


class GeocodeCandidate(TypedDict):
    label: str
    neighborhood: str | None
    lat: float
    lng: float
    importance: float


class Throttle:
    """Spaces calls by `interval` seconds, across every request of the process."""

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last: float | None = None

    async def wait(self) -> None:
        async with self._lock:
            if self._last is not None:
                remaining = self._last + self.interval - self._clock()
                if remaining > 0:
                    await self._sleep(remaining)
            self._last = self._clock()


_search_cache = ResponseCache(ttl=SEARCH_CACHE_TTL, maxsize=512)
_throttle = Throttle(1.0)


def normalize_query(query: str) -> str:
    """Same text, same cache entry: trims, collapses spaces and ignores case."""
    return re.sub(r"\s+", " ", query).strip().casefold()


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _number(value: object) -> float | None:
    """Nominatim sends coordinates as strings and importance as a number."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _label(item: dict, address: dict) -> str | None:
    name, road = _text(item.get("name")), _text(address.get("road"))
    if name and name != road:
        return name
    if road:
        number = _text(address.get("house_number"))
        return f"{road}, {number}" if number else road
    display = _text(item.get("display_name"))
    return display.split(",")[0].strip() if display else None


def _candidate(item: object) -> GeocodeCandidate | None:
    """Convert one result, or return None when it can't be placed on the map."""
    if not isinstance(item, dict):
        return None
    lat, lng = _number(item.get("lat")), _number(item.get("lon"))
    if lat is None or lng is None:
        return None
    if not (SOUTH <= lat <= NORTH and WEST <= lng <= EAST):
        return None
    raw_address = item.get("address")
    address: dict = raw_address if isinstance(raw_address, dict) else {}
    label = _label(item, address)
    if label is None:
        return None
    return {
        "label": label,
        "neighborhood": next(
            (v for key in _NEIGHBORHOOD_KEYS if (v := _text(address.get(key)))), None
        ),
        "lat": lat,
        "lng": lng,
        "importance": _number(item.get("importance")) or 0.0,
    }


def parse_candidates(data: object) -> list[GeocodeCandidate]:
    if not isinstance(data, list):
        raise ExternalAPIResponseError(SERVICE, "Search response is not a list")
    return [c for item in data if (c := _candidate(item)) is not None]


class NominatimClient:
    """Reuse the application's HTTP client; this adapter never closes it."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings | None = None,
        *,
        cache: ResponseCache | None = None,
        throttle: Throttle | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.cache = cache if cache is not None else _search_cache
        self.throttle = throttle if throttle is not None else _throttle
        self.api = ExternalAPIClient(
            client,
            service=SERVICE,
            base_url=str(self.settings.nominatim_base_url),
            timeout=self.settings.external_api_timeout_seconds,
            retry=_RETRY,
        )

    async def search(self, query: str) -> list[GeocodeCandidate]:
        """Places in São Paulo matching `query`, in Nominatim's order.

        Raises ValueError when the feature is disabled or the query is blank, and
        ExternalAPIError when Nominatim fails, so callers can degrade gracefully.
        """
        if not self.settings.nominatim_enabled:
            raise ValueError("Enable NOMINATIM_ENABLED before searching addresses")
        normalized = normalize_query(query)
        if not normalized:
            raise ValueError("query must not be blank")
        params = {
            "q": normalized,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": UPSTREAM_LIMIT,
            "countrycodes": "br",
            "accept-language": "pt-BR",
            "viewbox": f"{WEST},{NORTH},{EAST},{SOUTH}",
            "bounded": 1,
        }
        if self.settings.nominatim_email:
            params["email"] = self.settings.nominatim_email
        key = make_cache_key(f"{SERVICE}{SEARCH_PATH}", {"q": normalized})
        if (cached := self.cache.get(key)) is not None:
            return json.loads(cached)
        await self.throttle.wait()
        candidates = parse_candidates(
            await self.api.get_json(SEARCH_PATH, params=params)
        )
        # Only validated answers are cached, so a bad response can recover.
        self.cache.set(key, json.dumps(candidates).encode())
        return candidates
