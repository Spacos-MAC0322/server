"""Small geodesic and GeoJSON helpers shared by services and integrations."""

import math

EARTH_RADIUS_M = 6371000


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle (haversine) distance in meters between two coordinates."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return EARTH_RADIUS_M * 2 * math.asin(math.sqrt(min(1, a)))


def _rings(geometry: dict) -> list[list[list]]:
    """Polygons of a GeoJSON Polygon or MultiPolygon, each as [outer, *holes]."""
    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    if kind == "Polygon":
        return [coordinates]
    if kind == "MultiPolygon":
        return list(coordinates)
    raise ValueError("Only Polygon and MultiPolygon geometries are supported")


def geometry_bounds(geometry: dict) -> tuple[float, float, float, float]:
    """(min_lat, min_lng, max_lat, max_lng) of a GeoJSON [lng, lat] geometry."""
    points = [point for polygon in _rings(geometry) for point in polygon[0]]
    if not points:
        raise ValueError("Geometry has no coordinates")
    lngs, lats = [p[0] for p in points], [p[1] for p in points]
    return min(lats), min(lngs), max(lats), max(lngs)


def _inside_ring(lat: float, lng: float, ring: list) -> bool:
    """Ray casting: a horizontal ray from the point crosses the ring an odd number
    of times exactly when the point is inside it."""
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y1 > lat) != (y2 > lat):
            crossing = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lng < crossing:
                inside = not inside
    return inside


def point_in_geometry(lat: float, lng: float, geometry: dict) -> bool:
    """Whether a point lies in a GeoJSON Polygon/MultiPolygon, honoring holes.

    Planar test on [lng, lat] coordinates, accurate for city-sized areas.
    """
    return any(
        _inside_ring(lat, lng, outer)
        and not any(_inside_ring(lat, lng, hole) for hole in holes)
        for outer, *holes in _rings(geometry)
    )
