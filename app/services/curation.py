"""Rank cultural spaces for the Fora do óbvio selection."""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from itertools import zip_longest
from typing import Any

ZONES = frozenset({"centro", "sul", "leste", "norte", "oeste"})
COMMUNITY_VENUE = re.compile(
    r"\b(?:casa (?:de|da) cultura|centro (?:cultural )?comunitario)\b"
)
SMALL_MUSEUM = re.compile(r"\b(?:museu (?:de pequeno porte|pequeno)|pequeno museu)\b")


def _normalize(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def _zone(place: Mapping[str, Any]) -> str | None:
    territory = place.get("territory") or {}
    for value in (place.get("region_zone"), territory.get("region")):
        zone = _normalize(value).removeprefix("zona ")
        if zone in ZONES:
            return zone
    return None


def _score(
    place: Mapping[str, Any], zone: str | None, curation: Mapping[str, Any]
) -> float:
    labels = "\n".join(
        _normalize(label)
        for label in (
            place.get("name"),
            place.get("type"),
            place.get("source_category"),
            *(place.get("categories") or []),
        )
    )
    community_center = place.get("community_center")
    if community_center is None:
        community_center = COMMUNITY_VENUE.search(labels) is not None

    return sum(
        (
            3.0 if place.get("is_independent") is True else 0.0,
            3.0 if community_center is True and zone not in (None, "centro") else 0.0,
            2.0 if SMALL_MUSEUM.search(labels) else 0.0,
            2.0 if curation.get("hidden_gem") is True else 0.0,
        )
    )


def rank_places(
    places: Iterable[Mapping[str, Any]],
    *,
    curations: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Score eligible MongoDB place documents and return ranked copies.

    Each round includes one place per available zone, with unclassified places
    sharing a single group. Scores order places within each group and round;
    ties preserve input order. The optional editorial records are keyed by place
    ID, as returned by PlaceRepository.find_curations.
    """
    curations = curations or {}
    by_zone: dict[str | None, list[tuple[float, int, dict[str, Any]]]] = defaultdict(
        list
    )
    for index, place in enumerate(places):
        zone = _zone(place)
        curation = curations.get(place.get("_id", place.get("id")), {})
        score = _score(place, zone, curation)
        by_zone[zone].append((-score, index, dict(place, curation_score=score)))

    for group in by_zone.values():
        group.sort()

    return [
        place
        for round_members in zip_longest(*by_zone.values())
        for _, _, place in sorted(
            member for member in round_members if member is not None
        )
    ]
