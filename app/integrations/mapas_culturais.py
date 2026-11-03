"""Read public spaces without coupling the external API to MongoDB."""

import json
import math
import re
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ExternalServiceInvalidResponse,
    ExternalServiceTimeout,
    ExternalServiceUnavailable,
)

SERVICE = "mapas_culturais"
SPACE_FIELDS = (
    "id,name,type,shortDescription,longDescription,location,public,endereco,"
    "En_CEP,En_Bairro,En_Municipio,telefonePublico,site,horario,preco,"
    "acessibilidade,acessibilidade_fisica,updateTimestamp,status"
)


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
    host = urlparse(base_url).hostname
    provider = (
        "mapas_saas"
        if host == "saas.mapas.cultura.gov.br"
        else f"mapas_culturais:{host}"
    )
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
            from zoneinfo import ZoneInfo

            stamp = stamp.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
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
            "is_free": None,
            "price_min": None,
            "price_max": None,
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
