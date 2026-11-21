import pytest

from app.core.geo import geometry_bounds, point_in_geometry

# [lng, lat] rings, as in GeoJSON.
OUTER = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
HOLE = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
SQUARE_WITH_HOLE = {"type": "Polygon", "coordinates": [OUTER, HOLE]}
TWO_SQUARES = {
    "type": "MultiPolygon",
    "coordinates": [[OUTER], [[[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]]]],
}


@pytest.mark.parametrize(
    ("lat", "lng", "inside"),
    [
        (2, 2, True),
        (5, 5, False),  # inside the hole
        (5, 11, False),
        (-1, 5, False),
        (9.99, 0.01, True),
    ],
)
def test_point_in_polygon_with_hole(lat, lng, inside):
    assert point_in_geometry(lat, lng, SQUARE_WITH_HOLE) is inside


def test_point_in_any_part_of_a_multipolygon():
    assert point_in_geometry(25, 25, TWO_SQUARES)
    assert point_in_geometry(5, 5, TWO_SQUARES)
    assert not point_in_geometry(15, 15, TWO_SQUARES)


def test_bounds_are_latitude_first():
    assert geometry_bounds(TWO_SQUARES) == (0, 0, 30, 30)
    assert geometry_bounds(
        {"type": "Polygon", "coordinates": [[[-46.7, -23.6], [-46.6, -23.5]]]}
    ) == (-23.6, -46.7, -23.5, -46.6)


@pytest.mark.parametrize(
    "geometry",
    [{"type": "Point", "coordinates": [0, 0]}, {"type": "Polygon", "coordinates": []}],
)
def test_unsupported_or_empty_geometry(geometry):
    with pytest.raises((ValueError, IndexError)):
        geometry_bounds(geometry)
