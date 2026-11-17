"""Cover the scoring rules and the distribution of places by zone."""

import pytest

from app.services.curation import rank_places


@pytest.mark.parametrize(
    ("place", "curation", "expected"),
    [
        ({"categories": ["Coletivo"], "is_independent": True}, {}, 3.0),
        ({"name": "Casa de Cultura", "region_zone": "Leste"}, {}, 3.0),
        (
            {"categories": ["Centro Cultural Comunitário"], "region_zone": "Sul"},
            {},
            3.0,
        ),
        ({"community_center": True, "region_zone": "Centro"}, {}, 0.0),
        ({"community_center": True}, {}, 0.0),
        (
            {
                "name": "Casa de Cultura",
                "community_center": False,
                "region_zone": "Leste",
            },
            {},
            0.0,
        ),
        ({"categories": ["Museu de Pequeno Porte"]}, {}, 2.0),
        ({"categories": ["Museu"]}, {"hidden_gem": True}, 2.0),
        ({"categories": ["Museu"]}, {}, 0.0),
    ],
)
def test_rank_places_scores_only_available_signals(place, curation, expected):
    document = {"_id": "place", **place}

    ranked = rank_places([document], curations={"place": curation})

    assert ranked == [{**document, "curation_score": expected}]
    assert "curation_score" not in document


def test_rank_places_reaches_other_zones_before_repeating_a_zone():
    places = [
        {"_id": "popular", "region_zone": "Centro"},
        {"_id": "center-independent", "region_zone": "Centro", "is_independent": True},
        {"_id": "east-independent", "region_zone": "Leste", "is_independent": True},
        {
            "_id": "east-community",
            "territory": {"region": "Zona Leste"},
            "community_center": True,
            "is_independent": True,
        },
        {"_id": "south", "region_zone": "Sul"},
        {"_id": "north", "region_zone": "Norte"},
        {"_id": "west", "region_zone": "Oeste"},
    ]

    assert [place["_id"] for place in rank_places(places)] == [
        "east-community",
        "center-independent",
        "south",
        "north",
        "west",
        "east-independent",
        "popular",
    ]


def test_rank_places_groups_unknown_zones_and_recomputes_stored_scores():
    places = [
        {"_id": "unknown", "curation_score": 9.0},
        {"_id": "west", "region_zone": "Oeste"},
        {"_id": "independent", "is_independent": True},
    ]

    ranked = rank_places(places)

    assert [(place["_id"], place["curation_score"]) for place in ranked] == [
        ("independent", 3.0),
        ("west", 0.0),
        ("unknown", 0.0),
    ]
    assert places[0]["curation_score"] == 9.0
    assert rank_places([]) == []
