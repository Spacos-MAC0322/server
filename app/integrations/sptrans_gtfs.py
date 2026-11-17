"""Local bus topology and estimated direct journeys from an official GTFS ZIP.

Load once and reuse. No files are extracted; realtime credentials are not needed.
Walking uses straight-line distance with a 1.3 detour factor, at 4.8 km/h.
Frequency services use half a headway as expected waiting time, not a promise.
"""

import csv
import io
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zipfile import ZipFile
from zoneinfo import ZoneInfo

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def validate_coordinates(lat: float, lng: float) -> None:
    for value, limit in ((lat, 90), (lng, 180)):
        if (
            type(value) not in (int, float)
            or not -limit <= value <= limit
            or not math.isfinite(value)
        ):
            raise ValueError("Expected finite latitude/longitude within valid bounds")


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return 6371000 * 2 * math.asin(math.sqrt(min(1, a)))


def _seconds(value: str) -> int:
    hours, minutes, seconds = map(int, value.split(":"))
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError("Invalid GTFS time")
    return hours * 3600 + minutes * 60 + seconds


class GTFSNetwork:
    """Bus-only, direct connections; does not plan transfers or pedestrian paths."""

    @classmethod
    def from_zip(cls, path: str | Path) -> "GTFSNetwork":
        network = cls()
        with ZipFile(path) as archive:

            def rows(name, *, optional=False):
                if optional and name not in archive.namelist():
                    return
                with (
                    archive.open(name) as raw,
                    io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as source,
                ):
                    yield from csv.DictReader(source)

            network.routes = {
                row["route_id"]: row
                for row in rows("routes.txt")
                if row["route_type"] == "3"
            }
            network.trips = {
                row["trip_id"]: row
                for row in rows("trips.txt")
                if row["route_id"] in network.routes
            }
            network.stop_times = defaultdict(list)
            for row in rows("stop_times.txt"):
                if row["trip_id"] in network.trips:
                    network.stop_times[row["trip_id"]].append(
                        {
                            **row,
                            "sequence": int(row["stop_sequence"]),
                            "arrival": _seconds(row["arrival_time"])
                            if row["arrival_time"]
                            else None,
                            "departure": _seconds(row["departure_time"])
                            if row["departure_time"]
                            else None,
                        }
                    )
            for trip in network.stop_times.values():
                trip.sort(key=lambda stop: stop["sequence"])
                if len({stop["sequence"] for stop in trip}) != len(trip):
                    raise ValueError("Duplicate GTFS stop_sequence")
            bus_stops = {
                row["stop_id"] for trip in network.stop_times.values() for row in trip
            }
            network.stops = {}
            for row in rows("stops.txt"):
                if row["stop_id"] not in bus_stops:
                    continue
                lat, lng = float(row["stop_lat"]), float(row["stop_lon"])
                validate_coordinates(lat, lng)
                network.stops[row["stop_id"]] = {
                    "stop_id": row["stop_id"],
                    "name": row["stop_name"],
                    "latitude": lat,
                    "longitude": lng,
                }
            network.calendar = list(rows("calendar.txt", optional=True))
            network.exceptions = list(rows("calendar_dates.txt", optional=True))
            if not network.calendar and not network.exceptions:
                raise ValueError("GTFS requires calendar.txt or calendar_dates.txt")
            network.frequencies = defaultdict(list)
            for row in rows("frequencies.txt", optional=True):
                if row["trip_id"] not in network.trips:
                    continue
                start, end = _seconds(row["start_time"]), _seconds(row["end_time"])
                headway = int(row["headway_secs"])
                if (
                    headway <= 0
                    or end <= start
                    or row.get("exact_times", "0") not in ("", "0", "1")
                ):
                    raise ValueError("Invalid GTFS frequency")
                network.frequencies[row["trip_id"]].append(
                    (start, end, headway, row.get("exact_times") == "1")
                )
        return network

    def active_services(self, day: date) -> set[str]:
        stamp = day.strftime("%Y%m%d")
        services = {
            row["service_id"]
            for row in self.calendar
            if row["start_date"] <= stamp <= row["end_date"]
            and row[WEEKDAYS[day.weekday()]] == "1"
        }
        for row in self.exceptions:
            if row["date"] == stamp:
                if row["exception_type"] == "1":
                    services.add(row["service_id"])
                elif row["exception_type"] == "2":
                    services.discard(row["service_id"])
        return services

    def find_nearby_stops(
        self, lat: float, lng: float, *, radius_m: float = 500
    ) -> list[dict]:
        validate_coordinates(lat, lng)
        if type(radius_m) not in (int, float) or not 0 < radius_m <= 5000:
            raise ValueError("radius_m must be between 0 and 5000 metres")
        result = []
        for stop in self.stops.values():
            distance = distance_m(lat, lng, stop["latitude"], stop["longitude"])
            if distance <= radius_m:
                result.append({**stop, "distance_m": round(distance, 1)})
        return sorted(result, key=lambda stop: (stop["distance_m"], stop["stop_id"]))

    def find_connections(
        self,
        origin_lat: float,
        origin_lng: float,
        destination_lat: float,
        destination_lng: float,
        *,
        departure_at: datetime | None = None,
        radius_m: float = 500,
        limit: int = 3,
    ) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        departure_at = departure_at or datetime.now(SAO_PAULO)
        if departure_at.tzinfo is None or departure_at.utcoffset() is None:
            raise ValueError("departure_at must include a timezone")
        departure_at = departure_at.astimezone(SAO_PAULO)
        origin = {
            row["stop_id"]: row
            for row in self.find_nearby_stops(origin_lat, origin_lng, radius_m=radius_m)
        }
        destination = {
            row["stop_id"]: row
            for row in self.find_nearby_stops(
                destination_lat, destination_lng, radius_m=radius_m
            )
        }
        best = {}
        # Yesterday's service may continue beyond 24:00 into today's early hours.
        for day in (departure_at.date(), departure_at.date() - timedelta(days=1)):
            midnight = datetime.combine(day, time(), tzinfo=SAO_PAULO)
            now_seconds = (departure_at - midnight).total_seconds()
            active = self.active_services(day)
            for trip_id, trip in self.trips.items():
                if trip["service_id"] not in active:
                    continue
                stops = self.stop_times[trip_id]
                for index, board in enumerate(stops):
                    if (
                        board["stop_id"] not in origin
                        or board.get("pickup_type", "0") not in ("", "0")
                        or board["departure"] is None
                    ):
                        continue
                    origin_stop = origin[board["stop_id"]]
                    walk_start = origin_stop["distance_m"] * 1.3 / 80
                    ready = now_seconds + walk_start * 60
                    leave = self._next_departure(trip_id, stops, board, ready)
                    if leave is None:
                        continue
                    for alight in stops[index + 1 :]:
                        if (
                            alight["stop_id"] not in destination
                            or alight.get("drop_off_type", "0") not in ("", "0")
                            or alight["arrival"] is None
                        ):
                            continue
                        ride = (alight["arrival"] - board["departure"]) / 60
                        if ride <= 0:
                            continue
                        destination_stop = destination[alight["stop_id"]]
                        walk_end = destination_stop["distance_m"] * 1.3 / 80
                        wait = (leave - ready) / 60
                        total = walk_start + wait + ride + walk_end
                        route = self.routes[trip["route_id"]]
                        connection = {
                            "line": route["route_short_name"],
                            "route_id": trip["route_id"],
                            "headsign": trip.get("trip_headsign", ""),
                            "direction_id": trip.get("direction_id"),
                            "origin_stop": origin_stop,
                            "destination_stop": destination_stop,
                            "walking_minutes": round(walk_start + walk_end, 1),
                            "waiting_minutes": round(wait, 1),
                            "ride_minutes": round(ride, 1),
                            "estimated_duration_minutes": round(total, 1),
                            "transfers": 0,
                            "estimated_boarding_at": (
                                midnight + timedelta(seconds=leave)
                            ).isoformat(),
                            "estimated_arrival_at": (
                                departure_at + timedelta(minutes=total)
                            ).isoformat(),
                            "estimate_source": "gtfs_frequency"
                            if self.frequencies[trip_id]
                            else "gtfs_schedule",
                            "warnings": [
                                "Estimativa do GTFS; caminhada aproximada "
                                "e sem trânsito em tempo real."
                            ],
                        }
                        key = (
                            trip["route_id"],
                            trip.get("direction_id"),
                            board["stop_id"],
                            alight["stop_id"],
                        )
                        if key not in best or total < best[key][0]:
                            best[key] = (total, connection)
        return [
            item[1] for item in sorted(best.values(), key=lambda item: item[0])[:limit]
        ]

    def _next_departure(self, trip_id, stops, board, ready):
        frequencies = self.frequencies[trip_id]
        if not frequencies:
            return board["departure"] if board["departure"] >= ready else None
        if not stops or stops[0]["departure"] is None:
            return None
        offset = board["departure"] - stops[0]["departure"]
        candidates = []
        for start, end, headway, exact in frequencies:
            start, end = start + offset, end + offset
            if exact:
                leave = start + max(0, math.ceil((ready - start) / headway)) * headway
            else:
                leave = max(ready, start) + headway / 2
            if leave < end:
                candidates.append(leave)
        return min(candidates) if candidates else None
