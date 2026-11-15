"""Read-only endpoints for cultural places and their programming."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.core.database import get_database
from app.schemas.place import PlaceDetailSchema, PlaceListQuery, PlaceSummarySchema
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


@router.get(
    "/{place_id}",
    response_model=PlaceDetailSchema,
    summary="Consultar espaço cultural e sua programação",
    description=(
        "Retorna o espaço publicado, com a curadoria e a programação de hoje "
        "até os próximos 30 dias quando disponível."
    ),
    responses={
        404: {"description": "Espaço não encontrado"},
        503: {"description": "Banco de dados temporariamente indisponível"},
    },
)
async def get_place(
    place_id: str,
    service: Annotated[PlaceService, Depends(get_place_service)],
) -> PlaceDetailSchema:
    try:
        place = await service.get_place(place_id)
    except PyMongoError:
        raise HTTPException(
            status_code=503, detail="Não foi possível consultar o espaço no momento."
        ) from None
    if place is None:
        raise HTTPException(status_code=404, detail="Espaço não encontrado.")
    return place
