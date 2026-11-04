"""SPTrans Olho Vivo authentication, bus queries and GTFS journey estimates.

Reuse one instance and the application's HTTP client. The same httpx cookie jar
is used for login and later queries. Load an official GTFS ZIP via
SPTRANS_GTFS_PATH, or inject GTFSNetwork.from_zip(path) when creating the adapter.
Only direct bus connections are planned; durations are estimates from the GTFS.
"""

import asyncio
import json
from datetime import datetime

import httpx

from app.core.cache import ResponseCache, make_cache_key
from app.core.config import Settings, get_settings
from app.core.http_client import (
    ExternalAPIClient,
    ExternalAPIError,
    ExternalAPIResponseError,
    ExternalAPIStatusError,
    RetryPolicy,
)
from app.integrations.sptrans_gtfs import GTFSNetwork, validate_coordinates

SERVICE = "sptrans"


class SPTransAuthenticationError(ExternalAPIError):
    """The provider did not accept the configured token; credentials are omitted."""


def _integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Expected a nonnegative identifier")
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected text")
    return value


def _clock(value):
    _text(value)
    if len(value) != 5:
        raise ValueError("Expected HH:MM")
    datetime.strptime(value, "%H:%M")
    return value


def _stops(data):
    if not isinstance(data, list):
        raise ValueError("Expected stops")
    result = []
    for row in data:
        validate_coordinates(row["py"], row["px"])
        result.append(
            {
                "stop_id": str(_integer(row["cp"])),
                "name": _text(row["np"]),
                "address": row.get("ed"),
                "latitude": row["py"],
                "longitude": row["px"],
            }
        )
    return result


def _lines(data):
    if not isinstance(data, list):
        raise ValueError("Expected lines")
    result = []
    for row in data:
        if type(row["sl"]) is not int or row["sl"] not in (1, 2):
            raise ValueError("Invalid direction")
        result.append(
            {
                "line_id": _integer(row["cl"]),
                "code": f"{_text(row['lt'])}-{_integer(row['tl']):02d}",
                "direction": row["sl"],
                "primary_terminal": _text(row["tp"]),
                "secondary_terminal": _text(row["ts"]),
            }
        )
    return result


def _predictions(data, stop_id):
    reference = _clock(data["hr"])
    point = data["p"]
    if point is None:
        return {"stop_id": str(stop_id), "reference_time": reference, "lines": []}
    if str(_integer(point["cp"])) != str(stop_id) or not isinstance(point["l"], list):
        raise ValueError("Unexpected stop")
    lines = []
    for row in point["l"]:
        if not isinstance(row["vs"], list):
            raise ValueError("Expected vehicles")
        vehicles = []
        for vehicle in row["vs"]:
            if type(vehicle["p"]) not in (str, int) or type(vehicle["a"]) is not bool:
                raise ValueError("Invalid vehicle")
            vehicles.append(
                {
                    "vehicle_id": str(vehicle["p"]),
                    "expected_arrival": _clock(vehicle["t"]),
                    "accessible": vehicle["a"],
                }
            )
        if type(row["sl"]) is not int or row["sl"] not in (1, 2):
            raise ValueError("Invalid direction")
        lines.append(
            {
                "line_id": _integer(row["cl"]),
                "code": _text(row["c"]),
                "direction": row["sl"],
                "vehicles": vehicles,
            }
        )
    return {"stop_id": str(stop_id), "reference_time": reference, "lines": lines}


class SPTransClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings | None = None,
        *,
        cache: ResponseCache | None = None,
        network: GTFSNetwork | None = None,
    ):
        self.settings = settings or get_settings()
        self.network = network
        self.cache = cache if cache is not None else ResponseCache(ttl=30)
        self.api = ExternalAPIClient(
            client,
            SERVICE,
            base_url=str(self.settings.sptrans_base_url or ""),
            timeout=self.settings.external_api_timeout_seconds,
            retry=RetryPolicy(max_attempts=2, total_timeout=10),
        )
        self._authenticated = False
        self._auth_lock = asyncio.Lock()
        self._network_lock = asyncio.Lock()

    def _ensure_enabled(self):
        if not self.settings.sptrans_enabled:
            raise ValueError("Enable SPTRANS_ENABLED before querying SPTrans")

    async def authenticate(self, *, force: bool = False) -> bool:
        self._ensure_enabled()
        async with self._auth_lock:
            if self._authenticated and not force:
                return True
            self._authenticated = False
            response = await self.api.request(
                "POST",
                "/Login/Autenticar",
                params={"token": self.settings.sptrans_token.get_secret_value()},
            )
            try:
                accepted = response.json()
            except ValueError:
                raise ExternalAPIResponseError(
                    SERVICE, "Invalid authentication response"
                ) from None
            if accepted is False:
                raise SPTransAuthenticationError(
                    SERVICE, "Authentication rejected", status_code=401
                )
            if accepted is not True:
                raise ExternalAPIResponseError(
                    SERVICE, "Invalid authentication response"
                )
            self._authenticated = True
            return True

    async def _get(self, path, params, parse):
        self._ensure_enabled()
        key = make_cache_key(f"{self.settings.sptrans_base_url}{path}", params)
        if (cached := self.cache.get(key)) is not None:
            return json.loads(cached)
        await self.authenticate()
        try:
            data = await self.api.get_json(path, params=params)
        except ExternalAPIStatusError as error:
            if error.status_code != 401:
                raise
            await self.authenticate(force=True)
            data = await self.api.get_json(path, params=params)
        try:
            result = parse(data)
        except (KeyError, ValueError, TypeError, OverflowError):
            raise ExternalAPIResponseError(
                SERVICE, "Invalid SPTrans response"
            ) from None
        self.cache.set(key, json.dumps(result).encode())
        return result

    async def search_lines(self, terms: str) -> list[dict]:
        return await self._get("/Linha/Buscar", {"termosBusca": _text(terms)}, _lines)

    async def search_stops(self, terms: str) -> list[dict]:
        """Olho Vivo's stop search has limited corridor coverage; use GTFS nearby."""
        return await self._get("/Parada/Buscar", {"termosBusca": _text(terms)}, _stops)

    async def get_line_stops(self, line_id: int) -> list[dict]:
        """API list membership is useful; its order is not used to plan journeys."""
        return await self._get(
            "/Parada/BuscarParadasPorLinha", {"codigoLinha": _integer(line_id)}, _stops
        )

    async def get_arrival_predictions(self, stop_id: int) -> dict:
        return await self._get(
            "/Previsao/Parada",
            {"codigoParada": _integer(stop_id)},
            lambda data: _predictions(data, stop_id),
        )

    async def _get_network(self) -> GTFSNetwork:
        async with self._network_lock:
            if self.network is None:
                path = self.settings.sptrans_gtfs_path
                if path is None:
                    raise ValueError("Set SPTRANS_GTFS_PATH to the official GTFS ZIP")
                self.network = await asyncio.to_thread(GTFSNetwork.from_zip, path)
            return self.network

    async def find_nearby_stops(
        self, lat: float, lng: float, *, radius_m: float = 500
    ) -> list[dict]:
        network = await self._get_network()
        return network.find_nearby_stops(lat, lng, radius_m=radius_m)

    async def find_connections(
        self,
        origin_lat: float,
        origin_lng: float,
        destination_lat: float,
        destination_lng: float,
        *,
        departure_at: datetime | None = None,
        radius_m: float = 500,
        limit: int = 3,
        include_live: bool = True,
    ) -> list[dict]:
        """Return direct bus connections with walking, waiting and ride estimates.

        Estimates remain usable if Olho Vivo is disabled or unavailable. Live
        predictions are supplementary, never substituted for GTFS trip duration;
        GTFS and Olho Vivo direction identifiers are not assumed interchangeable.
        """
        network = await self._get_network()
        connections = network.find_connections(
            origin_lat,
            origin_lng,
            destination_lat,
            destination_lng,
            departure_at=departure_at,
            radius_m=radius_m,
            limit=limit,
        )
        predictions = {}
        unavailable = not self.settings.sptrans_enabled
        for connection in connections:
            connection["arrival_predictions"] = None
            if not include_live:
                continue
            stop_id = connection["origin_stop"]["stop_id"]
            if not unavailable and stop_id.isdecimal():
                try:
                    if stop_id not in predictions:
                        predictions[stop_id] = await self.get_arrival_predictions(
                            int(stop_id)
                        )
                    # Preserve the provider's direction on each prediction. These
                    # are arrivals at the stop, not a matched/guaranteed itinerary.
                    connection["arrival_predictions"] = {
                        **predictions[stop_id],
                        "lines": [
                            row
                            for row in predictions[stop_id]["lines"]
                            if row["code"] == connection["line"]
                        ],
                    }
                except ExternalAPIError:
                    unavailable = True
            if connection["arrival_predictions"] is None:
                connection["warnings"].append(
                    "Previsões em tempo real indisponíveis; estimativa baseada no GTFS."
                )
        return connections
