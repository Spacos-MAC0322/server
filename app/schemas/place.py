"""Read models for places: MongoDB documents in, explicit frontend JSON out.

The write contract remains app/core/mongodb_schema.json. These models neither
query MongoDB nor change publication visibility: callers must filter documents
and explicitly supply the matching place_curations document when available.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal, Self

from bson.decimal128 import Decimal128
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

Longitude = Annotated[float, Field(strict=True, ge=-180, le=180, allow_inf_nan=False)]
Latitude = Annotated[float, Field(strict=True, ge=-90, le=90, allow_inf_nan=False)]
Price = Annotated[float, Field(allow_inf_nan=False)]


class PlaceListQuery(BaseModel):
    """All four map bounds or none, using latitude/longitude query parameters."""

    sw_lat: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        allow_inf_nan=False,
        description="Latitude do canto sudoeste",
    )
    sw_lng: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        allow_inf_nan=False,
        description="Longitude do canto sudoeste",
    )
    ne_lat: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        allow_inf_nan=False,
        description="Latitude do canto nordeste",
    )
    ne_lng: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        allow_inf_nan=False,
        description="Longitude do canto nordeste",
    )

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        values = (self.sw_lat, self.sw_lng, self.ne_lat, self.ne_lng)
        present = sum(v is not None for v in values)
        if present not in (0, 4):
            raise ValueError("Informe sw_lat, sw_lng, ne_lat e ne_lng juntos")
        bounds = self.bounds
        if bounds is not None:
            sw_lat, sw_lng, ne_lat, ne_lng = bounds
            if sw_lat >= ne_lat or sw_lng >= ne_lng:
                raise ValueError("O canto sudoeste deve estar abaixo do canto nordeste")
            if ne_lng - sw_lng >= 180:
                raise ValueError("A área deve ter menos de 180 graus de longitude")
        return self

    @property
    def bounds(self) -> tuple[float, float, float, float] | None:
        if (
            self.sw_lat is not None
            and self.sw_lng is not None
            and self.ne_lat is not None
            and self.ne_lng is not None
        ):
            return self.sw_lat, self.sw_lng, self.ne_lat, self.ne_lng
        return None


class ReadSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GeoPoint(ReadSchema):
    """GeoJSON Point; coordinates always mean [longitude, latitude]."""

    type: Literal["Point"] = "Point"
    coordinates: tuple[Longitude, Latitude]

    @field_validator("coordinates", mode="before")
    @classmethod
    def decode_bson_coordinates(cls, value):
        if isinstance(value, (list, tuple)):
            return [v.to_decimal() if isinstance(v, Decimal128) else v for v in value]
        return value


class PlaceBadgesSchema(ReadSchema):
    """True/false are explicit source claims; None means no information."""

    is_free: StrictBool | None = None
    wheelchair: StrictBool | None = None
    pet_friendly: StrictBool | None = None
    family_friendly: StrictBool | None = None
    lgbtqia_inclusion: StrictBool | None = None
    hidden_gem: StrictBool | None = None


class PlaceDescriptionSchema(ReadSchema):
    short: str | None = None
    long: str | None = None


class PlaceAddressSchema(ReadSchema):
    full: str | None = None
    postal_code: str | None = None
    neighborhood: str | None = None
    municipality: str | None = None


class PlaceTerritorySchema(ReadSchema):
    district: str | None = None
    subprefecture: str | None = None
    region: str | None = None


class PlaceAdministrationSchema(ReadSchema):
    level: str | None = None


class PlaceContactsSchema(ReadSchema):
    phone: str | None = None
    website: str | None = None


class PlaceOpeningHoursSchema(ReadSchema):
    text: str | None = None
    verified: StrictBool | None = None


class PlaceAdmissionSchema(ReadSchema):
    is_free: StrictBool | None = None
    price_min: Price | None = None
    price_max: Price | None = None
    price_text: str | None = None
    currency: str | None = None

    @field_validator("price_min", "price_max", mode="before")
    @classmethod
    def decode_bson_price(cls, value):
        return value.to_decimal() if isinstance(value, Decimal128) else value


class PlaceAccessibilitySchema(ReadSchema):
    declared: StrictBool | None = None
    physical_features: list[str] = Field(default_factory=list)
    wheelchair: StrictBool | None = None
    adapted_restroom: StrictBool | None = None
    notes: str | None = None


class PlaceSourceSchema(ReadSchema):
    provider: str
    external_id: str
    layer: str | None = None
    updated_at: datetime | str | None = Field(default=None, union_mode="left_to_right")
    record_url: str | None = None


class PlaceDataQualitySchema(ReadSchema):
    location_valid: StrictBool | None = None
    needs_review: StrictBool | None = None
    stale_source: StrictBool | None = None


class PlaceCurationSchema(ReadSchema):
    pet_friendly: StrictBool | None = None
    family_friendly: StrictBool | None = None
    lgbtqia_inclusion: StrictBool | None = None
    hidden_gem: StrictBool | None = None
    accessibility_notes: str | None = None
    opening_hours_notes: str | None = None
    reviewed_at: datetime | str | None = Field(default=None, union_mode="left_to_right")


class PlaceSummarySchema(ReadSchema):
    id: str = Field(validation_alias=AliasChoices("_id", "id"), min_length=1)
    name: str = Field(min_length=1)
    primary_category: str | None = None
    location: GeoPoint | None = None
    badges: PlaceBadgesSchema = Field(default_factory=PlaceBadgesSchema)

    @field_validator("id", "name")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Must not be blank")
        return value

    @model_validator(mode="before")
    @classmethod
    def project_document(cls, value):
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "primary_category" not in data:
            categories = data.get("categories") or []
            candidates = [*categories, data.get("type"), data.get("source_category")]
            data["primary_category"] = next(
                (v.strip() for v in candidates if isinstance(v, str) and v.strip()),
                None,
            )
        if "badges" not in data:
            admission = data.get("admission") or {}
            accessibility = data.get("accessibility") or {}
            curation = data.get("curation") or {}
            data["badges"] = {
                "is_free": admission.get("is_free"),
                "wheelchair": accessibility.get("wheelchair"),
                **{
                    key: curation.get(key)
                    for key in (
                        "pet_friendly",
                        "family_friendly",
                        "lgbtqia_inclusion",
                        "hidden_gem",
                    )
                },
            }
        return data

    @classmethod
    def from_mongo(cls, document: Mapping, *, curation: Mapping | None = None) -> Self:
        """Project a document plus optional joined curation without mutating either."""
        data = dict(document)
        if curation is not None:
            if curation.get("place_id") != document.get("_id", document.get("id")):
                raise ValueError("Curation does not belong to this place")
            data["curation"] = dict(curation)
            # Recompute badges from the explicitly supplied curation.
            data.pop("badges", None)
        return cls.model_validate(data)


class PlaceDetailSchema(PlaceSummarySchema):
    type: str | None = None
    categories: list[str] = Field(default_factory=list)
    source_category: str | None = None
    description: PlaceDescriptionSchema = Field(default_factory=PlaceDescriptionSchema)
    address: PlaceAddressSchema = Field(default_factory=PlaceAddressSchema)
    territory: PlaceTerritorySchema = Field(default_factory=PlaceTerritorySchema)
    administration: PlaceAdministrationSchema = Field(
        default_factory=PlaceAdministrationSchema
    )
    contacts: PlaceContactsSchema = Field(default_factory=PlaceContactsSchema)
    public_access: StrictBool | None = None
    opening_hours: PlaceOpeningHoursSchema = Field(
        default_factory=PlaceOpeningHoursSchema
    )
    admission: PlaceAdmissionSchema = Field(default_factory=PlaceAdmissionSchema)
    accessibility: PlaceAccessibilitySchema = Field(
        default_factory=PlaceAccessibilitySchema
    )
    visit_duration_minutes: int | None = None
    sources: list[PlaceSourceSchema] = Field(default_factory=list)
    data_quality: PlaceDataQualitySchema = Field(default_factory=PlaceDataQualitySchema)
    curation: PlaceCurationSchema | None = None
