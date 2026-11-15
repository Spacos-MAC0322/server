"""Itinerary queries, weather and transit estimates for São Paulo."""

from datetime import date as Date
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.core.config import Settings, get_settings
from app.core.database import get_database
from app.core.http_client import get_http_client
from app.integrations.open_meteo import OpenMeteoClient
from app.integrations.sptrans import SPTransClient
from app.repositories.itinerary_repository import ItineraryRepository
from app.schemas.itinerary import (
    ItineraryResponse,
    ItineraryStopSchema,
    ItineraryValidationRequest,
    TransitLegSchema,
    WeatherResponse,
)
from app.services.itinerary_planner import ItineraryPlanner

router = APIRouter(prefix="/api/itineraries", tags=["itineraries"])


def get_itinerary_planner(
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ItineraryPlanner:
    return ItineraryPlanner(
        OpenMeteoClient(http, settings), transit_client=SPTransClient(http, settings)
    )


def get_itinerary_repository(
    database: Annotated[AsyncDatabase, Depends(get_database)],
) -> ItineraryRepository:
    return ItineraryRepository(database)


# Keep static routes before /{itinerary_id}.
@router.get(
    "/weather",
    response_model=WeatherResponse,
    summary="Consultar previsão do tempo em São Paulo",
    description=(
        "Informe date no formato YYYY-MM-DD. A janela inclui hoje e os próximos "
        "15 dias, no fuso de São Paulo. Fora dela ou em caso de indisponibilidade, "
        "retorna weather_forecast=null e weather_warning, com HTTP 200."
    ),
)
async def get_weather(
    date: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    planner: Annotated[ItineraryPlanner, Depends(get_itinerary_planner)],
) -> WeatherResponse:
    try:
        day = Date.fromisoformat(date)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Data inválida; use YYYY-MM-DD."
        ) from None
    return await planner.get_weather(day)


@router.get(
    "/transit",
    response_model=TransitLegSchema,
    summary="Estimar deslocamento entre duas coordenadas",
    description=(
        "Consulta conexões diretas de ônibus pelo GTFS para o horário atual. "
        "A duração é estimada em minutos e inclui caminhada, espera e viagem. "
        "Sem conexão ou com GTFS indisponível, suggested_lines fica vazio e a "
        "duração estima caminhada. from_place_id='origin' e "
        "to_place_id='destination' identificam as coordenadas desta consulta; "
        "não são identificadores de lugares salvos. Não exige autenticação "
        "na Olho Vivo nem consulta ao banco de dados."
    ),
)
async def get_transit(
    from_lat: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
    from_lng: Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)],
    to_lat: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
    to_lng: Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)],
    planner: Annotated[ItineraryPlanner, Depends(get_itinerary_planner)],
) -> TransitLegSchema:
    legs = await planner.calculate_transit_legs(
        [
            {
                "id": "origin",
                "location": {"type": "Point", "coordinates": [from_lng, from_lat]},
            },
            {
                "id": "destination",
                "location": {"type": "Point", "coordinates": [to_lng, to_lat]},
            },
        ]
    )
    return legs[0]


@router.post(
    "/validate",
    response_model=list[ItineraryStopSchema],
    summary="Validar horários das paradas de um roteiro",
    description=(
        "Recebe a data e as paradas na ordem da visita. Devolve a mesma sequência "
        "com conflict_warning quando o local estiver fechado no horário de início. "
        "O horário é local de São Paulo. A validação consulta os horários semanais "
        "cadastrados e não salva o roteiro."
    ),
    responses={503: {"description": "Banco de dados temporariamente indisponível"}},
)
async def validate_itinerary(
    draft: ItineraryValidationRequest,
    planner: Annotated[ItineraryPlanner, Depends(get_itinerary_planner)],
    repository: Annotated[ItineraryRepository, Depends(get_itinerary_repository)],
) -> list[dict]:
    itinerary = {
        "date": draft.date.isoformat(),
        "stops": [
            stop.model_dump(exclude={"conflict_warning"}) for stop in draft.stops
        ],
    }
    try:
        checked = await planner.attach_opening_hours_conflicts(itinerary, repository)
    except PyMongoError:
        raise HTTPException(
            status_code=503, detail="Não foi possível validar os horários no momento."
        ) from None
    return checked["stops"]


@router.get(
    "/{itinerary_id}",
    response_model=ItineraryResponse,
    summary="Consultar roteiro com previsão do tempo",
    responses={
        404: {"description": "Roteiro não encontrado"},
        503: {"description": "Banco de dados temporariamente indisponível"},
    },
)
async def get_itinerary(
    itinerary_id: str,
    planner: Annotated[ItineraryPlanner, Depends(get_itinerary_planner)],
    repository: Annotated[ItineraryRepository, Depends(get_itinerary_repository)],
) -> dict:
    try:
        itinerary = await planner.get_itinerary(itinerary_id, repository)
    except PyMongoError:
        raise HTTPException(
            status_code=503, detail="Não foi possível consultar o roteiro no momento."
        ) from None
    if itinerary is None:
        raise HTTPException(status_code=404, detail="Roteiro não encontrado.")
    return itinerary
