"""Itinerary queries, weather and transit estimates for São Paulo."""

from datetime import date as Date
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.core.config import Settings, get_settings
from app.core.database import get_database
from app.core.http_client import get_http_client
from app.core.security import CurrentUser
from app.integrations.open_meteo import OpenMeteoClient
from app.integrations.sptrans import SPTransClient
from app.repositories.itinerary_repository import ItineraryRepository
from app.schemas.itinerary import (
    ItineraryCreate,
    ItineraryResponse,
    ItineraryStopSchema,
    ItinerarySummary,
    ItineraryUpdate,
    ItineraryValidationRequest,
    TransitLegSchema,
    WeatherResponse,
)
from app.services.itinerary_planner import ItineraryPlanner

ITINERARY_NOT_FOUND = "Roteiro não encontrado."
ITINERARIES_UNAVAILABLE = "Não foi possível consultar os roteiros no momento."
ITINERARY_SAVE_UNAVAILABLE = "Não foi possível salvar o roteiro no momento."
ITINERARY_DELETE_UNAVAILABLE = "Não foi possível excluir o roteiro no momento."

NOT_AUTHENTICATED_RESPONSE = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Não autenticado"}
}
UNAVAILABLE_RESPONSE = {
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "description": "Banco de dados temporariamente indisponível"
    }
}
NOT_FOUND_RESPONSE = {
    status.HTTP_404_NOT_FOUND: {"description": "Roteiro não encontrado"}
}
UNKNOWN_PLACE_RESPONSE = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "Dados inválidos ou espaço cultural inexistente"
    }
}

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


Planner = Annotated[ItineraryPlanner, Depends(get_itinerary_planner)]
Repository = Annotated[ItineraryRepository, Depends(get_itinerary_repository)]


async def _checked_stops(
    body: ItineraryCreate, planner: ItineraryPlanner, repository: ItineraryRepository
) -> list[dict]:
    """Stops ready to store: places must be published, warnings recalculated."""
    stops = [stop.model_dump(exclude={"conflict_warning"}) for stop in body.stops]
    missing = await repository.find_missing_places(stop["place_id"] for stop in stops)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Espaço não encontrado: {', '.join(sorted(missing))}.",
        )
    draft = {"date": _iso(body), "stops": stops}
    checked = await planner.attach_opening_hours_conflicts(draft, repository)
    return checked["stops"]


def _iso(body: ItineraryCreate) -> str | None:
    return body.date.isoformat() if body.date else None


def _unavailable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


@router.post(
    "",
    response_model=ItineraryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Criar roteiro do usuário logado",
    description=(
        "Salva título, data opcional e paradas. A ordem de visita vem do campo "
        "order de cada parada. Ao salvar, o servidor calcula conflict_warning "
        "com os horários de funcionamento; o valor enviado pelo cliente é ignorado."
    ),
    responses=NOT_AUTHENTICATED_RESPONSE
    | UNKNOWN_PLACE_RESPONSE
    | UNAVAILABLE_RESPONSE,
)
async def create_itinerary(
    body: ItineraryCreate, user: CurrentUser, planner: Planner, repository: Repository
) -> dict:
    try:
        stops = await _checked_stops(body, planner, repository)
        itinerary = await repository.create(
            user_id=user["_id"], title=body.title, date=_iso(body), stops=stops
        )
    except PyMongoError:
        raise _unavailable(ITINERARY_SAVE_UNAVAILABLE) from None
    return await planner.present_itinerary(itinerary)


@router.get(
    "",
    response_model=list[ItinerarySummary],
    summary="Listar roteiros do usuário logado",
    description="Resumo de cada roteiro, por data (sem data primeiro) e criação.",
    responses=NOT_AUTHENTICATED_RESPONSE | UNAVAILABLE_RESPONSE,
)
async def list_itineraries(user: CurrentUser, repository: Repository) -> list[dict]:
    try:
        return await repository.list_by_user(user["_id"])
    except PyMongoError:
        raise _unavailable(ITINERARIES_UNAVAILABLE) from None


@router.get(
    "/{itinerary_id}",
    response_model=ItineraryResponse,
    summary="Consultar roteiro do usuário logado com previsão do tempo",
    description=(
        "Somente o dono consulta o roteiro; para outros usuários, responde 404."
    ),
    responses=NOT_AUTHENTICATED_RESPONSE | NOT_FOUND_RESPONSE | UNAVAILABLE_RESPONSE,
)
async def get_itinerary(
    itinerary_id: str, user: CurrentUser, planner: Planner, repository: Repository
) -> dict:
    try:
        itinerary = await planner.get_itinerary(itinerary_id, user["_id"], repository)
    except PyMongoError:
        raise _unavailable("Não foi possível consultar o roteiro no momento.") from None
    if itinerary is None:
        raise HTTPException(status_code=404, detail=ITINERARY_NOT_FOUND)
    return itinerary


@router.put(
    "/{itinerary_id}",
    response_model=ItineraryResponse,
    summary="Substituir roteiro do usuário logado",
    description=(
        "Substitui título, data e todas as paradas; envie a lista completa, já na "
        "nova ordem. conflict_warning é recalculado ao salvar."
    ),
    responses=NOT_AUTHENTICATED_RESPONSE
    | NOT_FOUND_RESPONSE
    | UNKNOWN_PLACE_RESPONSE
    | UNAVAILABLE_RESPONSE,
)
async def replace_itinerary(
    itinerary_id: str,
    body: ItineraryUpdate,
    user: CurrentUser,
    planner: Planner,
    repository: Repository,
) -> dict:
    try:
        if await repository.find_owned(itinerary_id, user["_id"]) is None:
            raise HTTPException(status_code=404, detail=ITINERARY_NOT_FOUND)
        stops = await _checked_stops(body, planner, repository)
        itinerary = await repository.replace_owned(
            itinerary_id, user["_id"], title=body.title, date=_iso(body), stops=stops
        )
    except PyMongoError:
        raise _unavailable(ITINERARY_SAVE_UNAVAILABLE) from None
    if itinerary is None:  # deleted between the check and the update
        raise HTTPException(status_code=404, detail=ITINERARY_NOT_FOUND)
    return await planner.present_itinerary(itinerary)


@router.delete(
    "/{itinerary_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Excluir roteiro do usuário logado",
    responses=NOT_AUTHENTICATED_RESPONSE | NOT_FOUND_RESPONSE | UNAVAILABLE_RESPONSE,
)
async def delete_itinerary(
    itinerary_id: str, user: CurrentUser, repository: Repository
) -> Response:
    try:
        deleted = await repository.delete_owned(itinerary_id, user["_id"])
    except PyMongoError:
        raise _unavailable(ITINERARY_DELETE_UNAVAILABLE) from None
    if not deleted:
        raise HTTPException(status_code=404, detail=ITINERARY_NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
