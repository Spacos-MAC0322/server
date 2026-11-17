"""Public discovery of curated cultural spaces."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import PyMongoError

from app.api.routers.places import get_place_service
from app.schemas.place import PlaceCardSchema
from app.services.place import PlaceService

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get(
    "/discover",
    response_model=list[PlaceCardSchema],
    summary="Descobrir espaços culturais fora do óbvio",
    description=(
        "Lista espaços publicados e geolocalizados com pontuação de curadoria "
        "positiva, alternando entre as zonas disponíveis. "
        "Retorna [] quando não há resultados."
    ),
    responses={503: {"description": "Banco de dados temporariamente indisponível"}},
)
async def discover_places(
    service: Annotated[PlaceService, Depends(get_place_service)],
) -> list[PlaceCardSchema]:
    try:
        return await service.discover_places()
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível consultar a seleção de espaços no momento.",
        ) from None
