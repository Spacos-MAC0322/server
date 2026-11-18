"""Addresses and places found by the address search."""

from pydantic import BaseModel, Field

from app.schemas.place import Latitude, Longitude


class GeocodeResultSchema(BaseModel):
    """A place in São Paulo that matches the address the user typed."""

    label: str = Field(min_length=1, examples=["Rua dos Pinheiros"])
    neighborhood: str | None = Field(default=None, examples=["Pinheiros"])
    lat: Latitude
    lng: Longitude
