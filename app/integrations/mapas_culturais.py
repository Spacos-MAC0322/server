"""Read public spaces and events without coupling the API to MongoDB."""

import asyncio
import json
import logging
import math
import re
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from app.core.cache import ResponseCache, make_cache_key
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ExternalServiceInvalidResponse,
    ExternalServiceTimeout,
    ExternalServiceUnavailable,
)
from app.core.http_client import (
    ExternalAPIClient,
    ExternalAPIError,
    ExternalAPIResponseError,
    RetryPolicy,
    create_http_client,
)
from app.core.price_text import parse_price_text

logger = logging.getLogger(__name__)
SERVICE = "mapas_culturais"
SPACE_FIELDS = (
    "id,name,type,shortDescription,longDescription,location,public,endereco,"
    "En_CEP,En_Bairro,En_Municipio,telefonePublico,site,horario,preco,"
    "acessibilidade,acessibilidade_fisica,updateTimestamp,status"
)
SAO_PAULO = ZoneInfo("America/Sao_Paulo")
EVENTS_PATH = "/api/event/findBySpace"
EVENTS_LOOKAHEAD_DAYS = 30
EVENTS_TIMEOUT_SECONDS = 10
EVENTS_RETRY = RetryPolicy(
    max_attempts=2,
    total_timeout=EVENTS_TIMEOUT_SECONDS,
    statuses=RetryPolicy().statuses | {408, 500},
)
_events_cache = ResponseCache(ttl=300)
_events_fallback = ResponseCache(ttl=3600)


@dataclass(frozen=True)
class SpacePage:
    number: int
    records: list[dict]
    metadata: dict


def text(value):
    return value.strip() or None if isinstance(value, str) else None


def fold(value):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(c)
    ).casefold()


def provider_for_url(base_url: str) -> str:
    host = urlparse(base_url).hostname
    return (
        "mapas_saas"
        if host == "saas.mapas.cultura.gov.br"
        else f"mapas_culturais:{host}"
    )


def normalize_space(record: dict, base_url: str) -> dict | None:
    """Keep city-confirmed records; unknown visit details are never inferred."""
    identifier, name = record.get("id"), text(record.get("name"))
    if (
        isinstance(identifier, bool)
        or not isinstance(identifier, (int, str))
        or not str(identifier).strip()
        or not name
        or record.get("status") != 1
    ):
        return None
    address = text(record.get("endereco"))
    city = text(record.get("En_Municipio"))
    if not city and address:
        match = re.search(r",\s*([^,]+),\s*SP\s*$", address, re.I)
        city = match.group(1).strip() if match else None
    if fold(city) != "sao paulo":
        return None
    point = None
    raw = record.get("location")
    if isinstance(raw, dict):
        try:
            lon, lat = float(raw["longitude"]), float(raw["latitude"])
            if math.isfinite(lon) and math.isfinite(lat):
                # Sanity check only; the municipality above is the city evidence.
                if -47.2 < lon < -46.1 and -24.1 < lat < -23.1:
                    point = {"type": "Point", "coordinates": [lon, lat]}
        except (KeyError, ValueError, TypeError, OverflowError):
            pass
    provider = provider_for_url(base_url)
    identity = f"{provider}:space:{identifier}"
    category = record.get("type")
    category = (
        text(category.get("name")) if isinstance(category, dict) else text(category)
    )
    updated = record.get("updateTimestamp")
    updated = updated.get("date") if isinstance(updated, dict) else updated
    stamp = None
    try:
        stamp = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=SAO_PAULO)
    except (AttributeError, ValueError, TypeError):
        pass
    price = record.get("preco")
    price = str(price).strip() if isinstance(price, (str, int, float)) else None
    return {
        "_id": identity,
        "name": name,
        "type": category,
        "categories": [category] if category else [],
        "source_category": category,
        "description": {
            "short": text(record.get("shortDescription")),
            "long": text(record.get("longDescription")),
        },
        "address": {
            "full": address,
            "municipality": "São Paulo",
            "postal_code": text(record.get("En_CEP")),
            "neighborhood": text(record.get("En_Bairro")),
        },
        "location": point,
        "opening_hours": {"text": text(record.get("horario")), "verified": False},
        "admission": {
            **parse_price_text(price),
            "price_text": price or None,
            "currency": "BRL",
        },
        "public_access": None,
        "source_public_flag": (
            record.get("public") if isinstance(record.get("public"), bool) else None
        ),
        "source_status": 1,
        "publication_status": "review",
        "source_keys": [identity],
        "sources": [
            {
                "provider": provider,
                "external_id": str(identifier),
                "updated_at": stamp,
                "record_url": f"{base_url.rstrip('/')}/espaco/{identifier}/",
            }
        ],
        "data_quality": {
            "location_valid": point is not None,
            "location_method": "source_point" if point else None,
            "municipality_check": "confirmed_address",
            "needs_review": True,
            "detail_level": "full",
            "stale_source": stamp is None or (datetime.now(UTC) - stamp).days > 730,
        },
    }


class MapasCulturaisClient:
    def __init__(self, settings: Settings | None = None, *, transport=None):
        self.settings = settings or get_settings()
        self.transport = transport

    async def fetch_events_by_space(self, space_id: int | str) -> list[dict]:
        """Return public events with occurrences from today through the next 30 days.

        Use the numeric ID from the configured Mapas Culturais instance. Today
        is calculated in São Paulo. On failure, use the last complete response
        for the same instance, space and interval (up to one hour), or return [].
        """
        if not isinstance(space_id, (int, str)) or not re.fullmatch(
            r"[1-9][0-9]*", str(space_id)
        ):
            raise ValueError("space_id must be a positive Mapas Culturais ID")
        if not self.settings.mapas_culturais_enabled:
            return []

        today = datetime.now(SAO_PAULO).date()
        base = str(self.settings.mapas_culturais_base_url).rstrip("/")
        params = {
            "spaceId": str(space_id),
            "@from": today.isoformat(),
            "@to": (today + timedelta(days=EVENTS_LOOKAHEAD_DAYS)).isoformat(),
            "@select": (
                "id,name,shortDescription,longDescription,classificacaoEtaria,"
                "terms,singleUrl,status"
            ),
            "@type": "json",
            "status": "EQ(1)",
        }
        key = make_cache_key(f"{base}{EVENTS_PATH}", params)
        if (cached := _events_cache.get(key)) is not None:
            return json.loads(cached)

        try:
            async with (
                asyncio.timeout(EVENTS_TIMEOUT_SECONDS),
                create_http_client(
                    transport=self.transport,
                    headers={"Accept": "application/json", "User-Agent": "Spacos/0.1"},
                ) as client,
            ):
                api = ExternalAPIClient(
                    client,
                    SERVICE,
                    base_url=base,
                    timeout=self.settings.external_api_timeout_seconds,
                    retry=EVENTS_RETRY,
                )
                events = await _event_pages(api, params)
        except (ExternalAPIError, TimeoutError) as error:
            fallback = _events_fallback.get(key)
            logger.warning(
                "Mapas Culturais agenda unavailable for space %s (%s, cached=%s)",
                space_id,
                type(error).__name__,
                fallback is not None,
            )
            return json.loads(fallback) if fallback is not None else []

        body = json.dumps(events).encode()
        _events_cache.set(key, body)
        _events_fallback.set(key, body)
        return events

    async def iter_space_pages(
        self, *, page_size: int = 100
    ) -> AsyncIterator[SpacePage]:
        """Read explicit fields; this legacy API returns only id without @select."""
        if not self.settings.mapas_culturais_enabled:
            raise ValueError("Enable MAPAS_CULTURAIS_ENABLED before fetching spaces")
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        base = str(self.settings.mapas_culturais_base_url).rstrip("/")
        seen = set()
        async with httpx.AsyncClient(
            timeout=self.settings.external_api_timeout_seconds,
            transport=self.transport,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "Spacos/0.1"},
        ) as client:
            page = 1
            while True:
                try:
                    response = await client.get(
                        f"{base}/api/space/find",
                        params={
                            "@select": SPACE_FIELDS,
                            "@type": "json",
                            "@limit": page_size,
                            "@page": page,
                            "@order": "id ASC",
                            "status": "EQ(1)",
                            # LIKE matches São Paulo / SÃO PAULO on the source DB.
                            "En_Municipio": "ILIKE(São Paulo)",
                        },
                    )
                    response.raise_for_status()
                except httpx.TimeoutException:
                    raise ExternalServiceTimeout(SERVICE) from None
                except httpx.HTTPError:
                    raise ExternalServiceUnavailable(SERVICE) from None
                try:
                    rows = response.json()
                except ValueError:
                    raise ExternalServiceInvalidResponse(SERVICE) from None
                if not isinstance(rows, list) or any(
                    not isinstance(r, dict) for r in rows
                ):
                    raise ExternalServiceInvalidResponse(SERVICE)
                if not rows:
                    break
                if any(not {"id", "name", "status"} <= r.keys() for r in rows):
                    raise ExternalServiceInvalidResponse(SERVICE)
                ids = {str(r.get("id")) for r in rows}
                if not ids - seen:
                    raise ExternalServiceInvalidResponse(SERVICE)
                seen.update(ids)
                try:
                    metadata = json.loads(response.headers.get("api-metadata", "{}"))
                except ValueError:
                    metadata = {}
                yield SpacePage(
                    page, rows, metadata if isinstance(metadata, dict) else {}
                )
                # An empty next page is authoritative even if the server caps @limit.
                page += 1

    async def fetch_spaces(self, *, page_size: int = 100) -> list[dict]:
        """Fetch and normalize every page before any database writes."""
        spaces = {}
        base = str(self.settings.mapas_culturais_base_url).rstrip("/")
        async for page in self.iter_space_pages(page_size=page_size):
            for row in page.records:
                place = normalize_space(row, base)
                if place:
                    spaces[place["_id"]] = place
        return list(spaces.values())


async def fetch_spaces(**kwargs) -> list[dict]:
    """Convenience entry point using the existing environment settings."""
    return await MapasCulturaisClient().fetch_spaces(**kwargs)


async def _event_pages(api: ExternalAPIClient, params: dict[str, str]) -> list[dict]:
    events = []
    seen = set()
    page = 1
    while True:
        rows = await api.get_json(
            EVENTS_PATH,
            params={**params, "@limit": 100, "@page": page, "@order": "id ASC"},
        )
        if not isinstance(rows, list):
            raise ExternalAPIResponseError(SERVICE, "Expected a list of events")
        if not rows:
            return events

        previous_count = len(seen)
        for event in rows:
            if (
                not isinstance(event, dict)
                or type(event.get("id")) is not int
                or type(event.get("status")) is not int
                or not text(event.get("name"))
            ):
                raise ExternalAPIResponseError(SERVICE, "Invalid event record")
            if event["id"] in seen:
                continue
            seen.add(event["id"])
            if event["status"] != 1:
                continue
            occurrences = event.get("occurrences")
            # PHP can serialize filtered arrays with sparse keys as JSON objects.
            if isinstance(occurrences, dict):
                occurrences = list(occurrences.values())
            if not isinstance(occurrences, list) or not all(
                isinstance(item, dict) for item in occurrences
            ):
                raise ExternalAPIResponseError(SERVICE, "Invalid event occurrences")
            if occurrences:
                events.append({**event, "occurrences": occurrences})
        if len(seen) == previous_count:
            raise ExternalAPIResponseError(SERVICE, "Repeated event page")
        page += 1
