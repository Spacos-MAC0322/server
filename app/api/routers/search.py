"""Place search with filters, curated discovery and address search."""

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.api.routers.places import get_place_service
from app.core.config import Settings, get_settings
from app.core.database import get_database
from app.core.http_client import get_http_client
from app.integrations.nominatim import NominatimClient
from app.schemas.geocoding import GeocodeResultSchema
from app.schemas.place import PlaceCardSchema
from app.schemas.search import SearchFilterOptionsSchema, SearchFilterParams
from app.services.geocoding import GeocodingService
from app.services.place import PlaceService
from app.services.search import SearchService, UnknownInterestGroup

router = APIRouter(prefix="/api/search", tags=["search"])


def get_search_service(
    database: Annotated[AsyncDatabase, Depends(get_database)],
) -> SearchService:
    return SearchService(database)


@router.get(
    "",
    response_model=list[PlaceCardSchema],
    summary="Buscar espaços culturais com filtros",
    description=(
        "Lista espaços publicados e geolocalizados que atendem a todos os filtros "
        "informados; numa mesma lista (categories, regions), basta um valor. "
        "q busca, sem diferenciar acentos, no nome, bairro, categoria, "
        "subprefeitura e zona. categories usa os ids de /api/search/filters. "
        "max_price inclui espaços gratuitos e deixa de fora os de preço "
        "desconhecido. Para a área do mapa, informe sw_lat, sw_lng, ne_lat e "
        "ne_lng; para um raio, lat, lng e radius_km (ordem por distância). Sem "
        "área, a ordem é alfabética. Retorna [] quando não há resultados."
    ),
    responses={503: {"description": "Banco de dados temporariamente indisponível"}},
)
async def search_places(
    query: Annotated[SearchFilterParams, Query()],
    service: Annotated[SearchService, Depends(get_search_service)],
) -> list[PlaceCardSchema]:
    try:
        return await service.search(query)
    except UnknownInterestGroup as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível buscar os espaços no momento.",
        ) from None


@router.get(
    "/filters",
    response_model=SearchFilterOptionsSchema,
    summary="Opções do painel de filtros",
    description=(
        "groups: grupos de interesse, na ordem de exibição, com o total de espaços "
        "publicados e geolocalizados de cada um (inclusive zero); um espaço pode "
        "estar em mais de um grupo. priced_places: quantos desses espaços têm "
        "gratuidade ou preço conhecidos; com 0, o frontend esconde os filtros de "
        "preço, que não teriam resultado."
    ),
    responses={503: {"description": "Banco de dados temporariamente indisponível"}},
)
async def get_filter_options(
    service: Annotated[SearchService, Depends(get_search_service)],
) -> SearchFilterOptionsSchema:
    try:
        return await service.filter_options()
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível consultar as opções de filtro no momento.",
        ) from None


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
