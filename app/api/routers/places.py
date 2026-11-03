"""Read-only endpoints for map pins; external APIs are never called here."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.core.database import get_database
from app.schemas.place import PlaceListQuery, PlaceSummarySchema
from app.services.place import PlaceService

router = APIRouter(prefix="/api/places", tags=["places"])


def get_place_service(
    database: Annotated[AsyncDatabase, Depends(get_database)],
) -> PlaceService:
    return PlaceService(database)


@router.get(
    "",
    response_model=list[PlaceSummarySchema],
    summary="Listar pinos de espaços culturais",
    description=(
        "Lista espaços publicados com coordenadas. Para filtrar a área do mapa, "
        "informe sw_lat, sw_lng, ne_lat e ne_lng juntos. Sem limites, lista todos "
        "os espaços disponíveis para pinos. Retorna [] quando não há resultados."
    ),
    responses={503: {"description": "Banco de dados temporariamente indisponível"}},
)
async def get_places(
    query: Annotated[PlaceListQuery, Query()],
    service: Annotated[PlaceService, Depends(get_place_service)],
) -> list[PlaceSummarySchema]:
    try:
        return await service.list_places(query)
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível consultar os espaços no momento.",
        ) from None
