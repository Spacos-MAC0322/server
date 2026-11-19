"""Query contract of GET /api/search (#58) and the interest-group counts."""

from sys import float_info
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.place import PlaceListQuery

Zone = Literal["Centro", "Norte", "Sul", "Leste", "Oeste"]


class SearchFilterParams(PlaceListQuery):
    """Every filter given must hold; values within one list are alternatives.

    The optional area is either the four map bounds (inherited from the map
    pins query) or a point with radius_km, never both.
    """

    q: str | None = Field(
        default=None,
        max_length=100,
        description="Texto livre: nome, bairro, categoria, subprefeitura ou zona",
    )
    categories: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Ids de grupos de interesse (GET /api/search/filters)",
    )
    regions: list[Zone] = Field(default_factory=list, description="Zonas da cidade")
    is_free: bool | None = Field(
        default=None, description="true: só espaços com entrada gratuita"
    )
    max_price: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="Preço máximo em reais; espaços gratuitos também entram",
    )
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lng: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    radius_km: float | None = Field(
        default=None, ge=0, le=float_info.max / 1000, allow_inf_nan=False
    )

    @field_validator("q")
    @classmethod
    def blank_text_means_no_filter(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_near(self) -> Self:
        present = sum(v is not None for v in (self.lat, self.lng, self.radius_km))
        if present not in (0, 3):
            raise ValueError("Informe lat, lng e radius_km juntos")
        if present == 3 and self.bounds is not None:
            raise ValueError("Use os limites do mapa ou um raio, não os dois")
        return self

    @property
    def near(self) -> tuple[float, float, float] | None:
        if self.lat is None or self.lng is None or self.radius_km is None:
            return None
        return self.lat, self.lng, self.radius_km


class InterestGroupCountSchema(BaseModel):
    id: str
    label: str
    count: int


class SearchFilterOptionsSchema(BaseModel):
    """Options for the filter panel (GET /api/search/filters)."""

    groups: list[InterestGroupCountSchema]
    priced_places: int = Field(
        description="Espaços com gratuidade ou preço conhecidos; com 0, os filtros "
        "de preço não teriam resultado"
    )
