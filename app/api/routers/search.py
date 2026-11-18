"""Public discovery of curated cultural spaces and address search."""

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import PyMongoError

from app.api.routers.places import get_place_service
from app.core.config import Settings, get_settings
from app.core.http_client import get_http_client
from app.integrations.nominatim import NominatimClient
from app.schemas.geocoding import GeocodeResultSchema
from app.schemas.place import PlaceCardSchema
from app.services.geocoding import GeocodingService
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


def get_geocoding_service(
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GeocodingService:
    return GeocodingService(NominatimClient(http, settings))


@router.get(
    "/geocode",
    response_model=list[GeocodeResultSchema],
    summary="Buscar um endereço ou lugar de referência em São Paulo",
    description=(
        "Converte o texto digitado (endereço, bairro ou lugar conhecido) em "
        "coordenadas, usando o Nominatim (OpenStreetMap). Retorna até 5 lugares "
        "distintos, do mais relevante ao menos; mais de um resultado indica que o "
        "usuário deve escolher. Retorna [] quando nada é encontrado. Consulte só "
        "quando o usuário enviar a busca: o serviço não permite autocompletar."
    ),
    responses={
        502: {"description": "Serviço de busca de endereços indisponível"},
        503: {"description": "Busca de endereços desativada no servidor"},
        504: {"description": "Serviço de busca de endereços não respondeu"},
    },
)
async def geocode_address(
    q: Annotated[str, Query(min_length=3, max_length=200)],
    service: Annotated[GeocodingService, Depends(get_geocoding_service)],
) -> list[GeocodeResultSchema]:
    if not service.enabled:
        raise HTTPException(
            status_code=503,
            detail="A busca por endereço está desativada no servidor.",
        )
    if not q.strip():
        raise HTTPException(status_code=422, detail="Informe um endereço.")
    return await service.search_address(q)
