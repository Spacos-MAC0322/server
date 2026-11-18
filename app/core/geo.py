"""Small geodesic helpers shared by services and integrations."""

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
