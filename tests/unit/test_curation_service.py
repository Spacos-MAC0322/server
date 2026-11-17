"""Regression tests for independence and geographic diversity in discovery."""

from app.services.curation import rank_places


def test_rank_places_prioritizes_independence_among_similar_places():
    similar_place = {"region_zone": "Centro", "categories": ["Coletivo"]}
    places = [
        {**similar_place, "_id": "institutional", "is_independent": False},
        {**similar_place, "_id": "unclassified"},
        {**similar_place, "_id": "independent", "is_independent": True},
    ]

    independent, institutional, unclassified = rank_places(places)

    assert independent["_id"] == "independent"
    assert independent["curation_score"] > institutional["curation_score"]
    assert independent["curation_score"] > unclassified["curation_score"]


def test_rank_places_keeps_zone_diversity_across_rounds_with_uneven_supply():
    zone_counts = {"Centro": 5, "Leste": 2, "Sul": 3}
    places = [
        {
            "_id": f"{zone}-{index}",
            "region_zone": zone,
            "categories": ["Museu de Pequeno Porte"],
            "is_independent": zone == "Centro",
        }
        for zone, count in zone_counts.items()
        for index in range(count)
    ]

    ranked = rank_places(places)

    zones = [place["region_zone"] for place in ranked]
    assert set(zones[:3]) == set(zones[3:6]) == {"Centro", "Leste", "Sul"}
    assert set(zones[6:8]) == {"Centro", "Sul"}
    assert zones[8:] == ["Centro", "Centro"]
    assert sorted(place["_id"] for place in ranked) == sorted(
        place["_id"] for place in places
    )
