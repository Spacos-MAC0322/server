"""Itinerary queries with optional São Paulo weather enrichment."""

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
from app.repositories.itinerary_repository import ItineraryRepository
from app.schemas.itinerary import ItineraryResponse, WeatherResponse
from app.services.itinerary_planner import ItineraryPlanner

router = APIRouter(prefix="/api/itineraries", tags=["itineraries"])


def get_itinerary_planner(
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ItineraryPlanner:
    return ItineraryPlanner(OpenMeteoClient(http, settings))


def get_itinerary_repository(
    database: Annotated[AsyncDatabase, Depends(get_database)],
) -> ItineraryRepository:
    return ItineraryRepository(database)


# Keep the static route before /{itinerary_id}, so "weather" is never an ID.
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
