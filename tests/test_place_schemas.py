from copy import deepcopy
from datetime import UTC, datetime

import pytest
from bson.decimal128 import Decimal128
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.integrations.mapas_culturais import normalize_space
from app.schemas.place import GeoPoint, PlaceDetailSchema, PlaceSummarySchema


@pytest.fixture
def document():
    return {
        "_id": "mapas_saas:space:1",
        "name": "Museu",
        "categories": ["Museu", "Centro cultural"],
        "type": "Equipamento",
        "source_category": "Museu da fonte",
        "location": {"type": "Point", "coordinates": [-46.63, -23.55]},
        "admission": {
            "is_free": False,
            "price_min": Decimal128("12.50"),
            "price_max": 25,
            "price_text": "R$ 12,50–25",
            "currency": "BRL",
        },
        "opening_hours": {"text": "10h–18h", "verified": False},
        "accessibility": {
            "declared": True,
            "wheelchair": None,
            "physical_features": ["Rampa"],
        },
        "sources": [
            {
                "provider": "mapas_saas",
                "external_id": "1",
                "updated_at": datetime(2026, 10, 30, tzinfo=UTC),
            }
        ],
        "source_keys": ["mapas_saas:space:1"],
        "source_public_flag": True,
        "publication_status": "review",
        "source_status": 1,
        "data_quality": {
            "stale_source": True,
            "needs_review": True,
            "location_valid": True,
            "detail_level": "full",
        },
    }


def test_summary_projects_document_without_leaking_storage_fields(document):
    original = deepcopy(document)
    summary = PlaceSummarySchema.model_validate(document)
    data = summary.model_dump(mode="json", by_alias=True)
    assert set(data) == {"id", "name", "primary_category", "location", "badges"}
    assert data["id"] == document["_id"]
    assert data["primary_category"] == "Museu"
    assert data["location"] == {"type": "Point", "coordinates": [-46.63, -23.55]}
    assert data["badges"]["is_free"] is False
    assert data["badges"]["wheelchair"] is None  # declared != wheelchair accessible
    assert data["badges"]["pet_friendly"] is None
    assert document == original


def test_detail_serializes_bson_and_nested_information(document):
    detail = PlaceDetailSchema.from_mongo(document)
    data = detail.model_dump(mode="json", by_alias=True)
    assert data["admission"]["price_min"] == 12.5
    assert data["opening_hours"] == {"text": "10h–18h", "verified": False}
    assert data["sources"][0]["updated_at"] == "2026-10-30T00:00:00Z"
    assert data["data_quality"]["stale_source"] is True
    for internal in (
        "_id",
        "source_keys",
        "source_public_flag",
        "source_status",
        "publication_status",
    ):
        assert internal not in data
    assert "detail_level" not in data["data_quality"]
    assert PlaceDetailSchema.model_validate_json(detail.model_dump_json()) == detail


def test_optional_curation_is_joined_explicitly_and_retains_unknowns(document):
    curation = {
        "_id": "curation-1",
        "place_id": document["_id"],
        "pet_friendly": True,
        "family_friendly": False,
        "lgbtqia_inclusion": None,
        "hidden_gem": True,
        "accessibility_notes": "Consultar equipe",
        "evidence": [{"internal": "omit"}],
    }
    original = deepcopy(curation)
    for cls in (PlaceSummarySchema, PlaceDetailSchema):
        schema = cls.from_mongo(document, curation=curation)
        assert schema.badges.pet_friendly is True
        assert schema.badges.family_friendly is False
        assert schema.badges.lgbtqia_inclusion is None
        assert schema.badges.hidden_gem is True
    data = schema.model_dump(mode="json")
    assert data["curation"]["accessibility_notes"] == "Consultar equipe"
    assert "evidence" not in data["curation"]
    assert "place_id" not in data["curation"]
    assert "curation" not in document
    assert curation == original


def test_curation_cannot_be_attached_to_another_place(document):
    with pytest.raises(ValueError, match="does not belong"):
        PlaceDetailSchema.from_mongo(document, curation={"place_id": "another"})


@pytest.mark.parametrize(
    "patch,expected",
    [
        ({"categories": [], "type": "Biblioteca"}, "Biblioteca"),
        ({"categories": [], "type": None}, "Museu da fonte"),
        ({"categories": [" ", "Galeria"]}, "Galeria"),
        ({"categories": [], "type": " ", "source_category": None}, None),
    ],
)
def test_category_precedence(document, patch, expected):
    document.update(patch)
    assert PlaceSummarySchema.from_mongo(document).primary_category == expected


def test_minimal_persisted_place_keeps_unknown_fields_unknown():
    detail = PlaceDetailSchema.model_validate({"_id": "minimal", "name": "Local"})
    assert detail.location is None
    assert detail.primary_category is None
    assert detail.badges.model_dump() == dict.fromkeys(type(detail.badges).model_fields)
    assert detail.opening_hours.verified is None
    assert detail.admission.currency is None
    assert detail.curation is None
    # Mutable defaults are isolated across responses.
    detail.categories.append("Museu")
    other = PlaceDetailSchema(id="other", name="Outro")
    assert other.categories == []


@pytest.mark.parametrize(
    "coordinates",
    [
        [-181, 0],
        [181, 0],
        [0, -91],
        [0, 91],
        [0],
        [0, 0, 0],
        [float("nan"), 0],
        [0, float("inf")],
        [True, 0],
        ["-46.63", "-23.55"],
    ],
)
def test_invalid_geojson_coordinates(coordinates):
    with pytest.raises(ValidationError):
        GeoPoint(coordinates=coordinates)


def test_geojson_type_and_decimal_coordinates():
    point = GeoPoint(coordinates=[Decimal128("-46.63"), Decimal128("-23.55")])
    assert point.model_dump(mode="json")["coordinates"] == [-46.63, -23.55]
    with pytest.raises(ValidationError):
        GeoPoint(type="Polygon", coordinates=[0, 0])


@pytest.mark.parametrize(
    "patch", [{"name": " "}, {"_id": ""}, {"_id": 123}, {"badges": {"is_free": "yes"}}]
)
def test_invalid_response_fields(document, patch):
    document.update(patch)
    with pytest.raises(ValidationError):
        PlaceSummarySchema.from_mongo(document)


def test_imported_place_is_compatible_with_both_read_models():
    place = normalize_space(
        {
            "id": 20,
            "name": "Centro",
            "status": 1,
            "En_Municipio": "São Paulo",
            "location": {"longitude": "-46.63", "latitude": "-23.55"},
        },
        "https://saas.mapas.cultura.gov.br",
    )
    assert PlaceSummarySchema.from_mongo(place).id == place["_id"]
    assert PlaceDetailSchema.from_mongo(place).admission.is_free is None


async def test_fastapi_response_uses_public_id_and_serialization_schema(document):
    app = FastAPI()

    @app.get("/places/example", response_model=PlaceSummarySchema)
    async def example():
        return document

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/places/example")
        assert response.status_code == 200
        assert response.json()["id"] == document["_id"]
        assert "_id" not in response.json()
    schema = app.openapi()["components"]["schemas"]["PlaceSummarySchema"]
    assert "id" in schema["properties"]
    assert "_id" not in schema["properties"]
