import pytest
from pydantic import ValidationError

from app.repositories.place_repository import PlaceRepository
from app.schemas.search import SearchFilterParams
from app.services.interest_groups import GROUPS_BY_ID, INTEREST_GROUPS
from app.services.search import (
    VISIBLE,
    SearchService,
    UnknownInterestGroup,
    build_search_conditions,
)


def place(identity, name, **fields):
    return {
        "_id": identity,
        "name": name,
        "publication_status": "published",
        "location": {"type": "Point", "coordinates": [-46.63, -23.55]},
        **fields,
    }


def test_defaults_keep_only_public_located_places():
    assert build_search_conditions(SearchFilterParams()) == [
        *VISIBLE,
        {"location": {"$ne": None}},
    ]


def test_an_area_already_requires_a_location():
    params = SearchFilterParams(lat=-23.5, lng=-46.6, radius_km=2)
    assert build_search_conditions(params) == list(VISIBLE)


def test_text_terms_must_all_match_some_field():
    conditions = build_search_conditions(SearchFilterParams(q="  Casa   São "))
    terms = conditions[len(VISIBLE) + 1 :]
    assert len(terms) == 2
    fields = [next(iter(option)) for option in terms[1]["$or"]]
    assert fields == [
        "name",
        "address.neighborhood",
        "categories",
        "territory.district",
        "territory.subprefecture",
        "region_zone",
    ]
    assert terms[1]["$or"][0]["name"]["$options"] == "i"


def test_price_and_zone_conditions():
    conditions = build_search_conditions(
        SearchFilterParams(regions=["Sul", "Sul", "Leste"], is_free=True, max_price=20)
    )
    assert {"region_zone": {"$in": ["Sul", "Leste"]}} in conditions
    assert {"admission.is_free": True} in conditions
    assert {
        "$or": [
            {"admission.is_free": True},
            {"admission.price_min": {"$lte": 20}},
        ]
    } in conditions


def test_unknown_interest_group_is_rejected():
    with pytest.raises(UnknownInterestGroup, match="danca, xyz"):
        build_search_conditions(SearchFilterParams(categories=["arte", "xyz", "danca"]))


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"lat": -23.5, "lng": -46.6}, "radius_km juntos"),
        (
            {
                "lat": -23.5,
                "lng": -46.6,
                "radius_km": 1,
                "sw_lat": -24,
                "sw_lng": -47,
                "ne_lat": -23,
                "ne_lng": -46,
            },
            "não os dois",
        ),
        ({"regions": ["Zona Sul"]}, "regions"),
        ({"max_price": -1}, "max_price"),
        ({"q": "x" * 101}, "q"),
    ],
)
def test_invalid_queries(values, message):
    with pytest.raises(ValidationError, match=message):
        SearchFilterParams(**values)


def test_blank_text_is_no_filter():
    assert SearchFilterParams(q="   ").q is None
    assert SearchFilterParams(lat=-23.5, lng=-46.6, radius_km=0).near == (
        -23.5,
        -46.6,
        0,
    )


def test_every_group_has_a_unique_id():
    assert len(GROUPS_BY_ID) == len(INTEREST_GROUPS)


async def test_repository_rejects_bounds_with_radius(db):
    with pytest.raises(ValueError, match="bounds or near"):
        await PlaceRepository(db).find_matching(
            [], bounds=(-24, -47, -23, -46), near=(-23.5, -46.6, 1)
        )


async def test_search_sorts_by_name_ignoring_accents(db):
    await db.places.insert_many(
        [
            place("1", "Zona Leste Viva"),
            place("2", "Ágora Cultural"),
            place("3", "biblioteca Mário"),
            place("4", "Escondido", publication_status="review"),
        ]
    )
    results = await SearchService(db).search(SearchFilterParams())
    assert [result.name for result in results] == [
        "Ágora Cultural",
        "biblioteca Mário",
        "Zona Leste Viva",
    ]


async def test_filter_options_count_groups_and_known_prices(db):
    await db.places.insert_many(
        [
            place("1", "A", categories=["Museu Público"], admission={"is_free": True}),
            place("2", "B", type="Teatro Público", categories=[]),
            place(
                "3",
                "C",
                categories=["Ponto de Cultura", "Sala de cinema"],
                admission={"is_free": False, "price_min": 10},
            ),
            place("4", "D", categories=["Museu"], publication_status="review"),
            place("5", "E", categories=["Museu"], location=None),
            place("6", "F", categories=["Biblioteca"], admission={"price_text": "?"}),
        ]
    )
    options = await SearchService(db).filter_options()
    assert [group.id for group in options.groups] == [g.id for g in INTEREST_GROUPS]
    by_id = {group.id: group.count for group in options.groups}
    assert by_id["arte"] == 2
    assert by_id["teatro"] == 1
    assert by_id["cinema"] == 1
    assert by_id["gastronomia"] == 0
    assert options.groups[0].label == "Arte e Cultura"
    assert options.priced_places == 2


YB_MUSIC = {
    "name": "YB Music",
    "categories": ["Estúdio"],
    "description": {
        "short": "Gravadora e editora musical, estúdio de gravação, produtora de "
        "filmes para difusão pela televisão, mixagem sonora de filmes "
        "cinematográficos, vídeos e programas de televisão."
    },
}


@pytest.mark.parametrize(
    ("document", "group_id", "expected"),
    [
        ({"name": "Escola de Samba Águia de Ouro"}, "musica", True),
        ({"description": {"short": "Cineclube no bairro"}}, "cinema", True),
        ({"description": {"short": "Oficinas de hip-hop"}}, "musica", True),
        ({"type": "Pontos de Memória"}, "patrimonio", True),
        ({"name": "Memorial da Resistência"}, "patrimonio", True),
        ({"description": {"short": "Contação de histórias"}}, "patrimonio", False),
        ({"name": "Arquivo Histórico Municipal"}, "patrimonio", True),
        ({"name": "Jardim Miriam Arte Clube"}, "parques", False),
        ({"name": "Jardim Botânico"}, "parques", True),
        ({"description": {"long": "Escola de samba e orquestra"}}, "musica", False),
        # YB Music: a record label that also produces films is not a cinema.
        (YB_MUSIC, "cinema", False),
        (YB_MUSIC, "musica", True),
        ({"name": "Biblioteca Pública Cora Coralina"}, "musica", False),
        ({"name": "Quadra da Escola de Samba Em Cima da Hora"}, "formacao", False),
        ({"name": "ESCOLA DE CHORO DE SAO PAULO"}, "formacao", True),
        ({"name": "Biblioteca Pública CEU  das Artes Vila Rubi"}, "arte", False),
        ({"name": "Casa das Artes"}, "arte", True),
        ({"name": "Museu de História Natural"}, "patrimonio", False),
        ({"type": "Documentação", "name": "Curta Saraus"}, "patrimonio", False),
        ({"description": {"short": "Trabalhos escolares"}}, "formacao", False),
        ({"description": {"short": "Banco de peças teatrais"}}, "teatro", False),
        ({"description": {"short": "Sessões de cinema aos sábados"}}, "cinema", True),
        ({"description": {"short": "Locação para filmes"}}, "cinema", False),
        ({"description": {"short": "Aulas de dança afro"}}, "teatro", True),
        ({"name": "Bosque da Leitura Parque do Carmo"}, "parques", True),
        ({"name": "Biblioteca CEU Parque São Carlos"}, "parques", False),
    ],
)
def test_groups_read_type_name_and_short_description(document, group_id, expected):
    assert bool(GROUPS_BY_ID[group_id].matches(document)) is expected
