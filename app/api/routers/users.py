"""Endpoints for the logged-in user's favorite places."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pymongo.errors import PyMongoError

from ...core.security import CurrentUser
from ...repositories.user_repository import (
    PlaceNotFoundError,
    UserRepository,
    get_user_repository,
)
from ...schemas.place import PlaceSummarySchema

PLACE_NOT_FOUND = "Espaço não encontrado."
FAVORITES_UNAVAILABLE = "Não foi possível consultar os favoritos no momento."
FAVORITE_SAVE_UNAVAILABLE = (
    "Não foi possível salvar este espaço nos favoritos no momento."
)
FAVORITE_REMOVE_UNAVAILABLE = (
    "Não foi possível remover este espaço dos favoritos no momento."
)

router = APIRouter(prefix="/api/users", tags=["users"])
Repository = Annotated[UserRepository, Depends(get_user_repository)]


@router.get(
    "/me/favorites",
    response_model=list[PlaceSummarySchema],
    summary="Listar espaços culturais favoritos do usuário logado",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Não autenticado"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Banco de dados temporariamente indisponível"
        },
    },
)
async def list_favorites(
    user: CurrentUser, repository: Repository
) -> list[PlaceSummarySchema]:
    try:
        return await repository.list_favorites(user["_id"])
    except PyMongoError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=FAVORITES_UNAVAILABLE,
        ) from None


@router.post(
    "/me/favorites/{place_id}",
    response_model=PlaceSummarySchema,
    status_code=status.HTTP_201_CREATED,
    summary="Adicionar espaço cultural aos favoritos do usuário logado",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Não autenticado"},
        status.HTTP_404_NOT_FOUND: {"description": PLACE_NOT_FOUND},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Banco de dados temporariamente indisponível"
        },
    },
)
async def add_favorite(
    place_id: str, user: CurrentUser, repository: Repository
) -> PlaceSummarySchema:
    try:
        return await repository.add_favorite(user["_id"], place_id)
    except PlaceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=PLACE_NOT_FOUND
        ) from error
    except PyMongoError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=FAVORITE_SAVE_UNAVAILABLE,
        ) from None


@router.delete(
    "/me/favorites/{place_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remover espaço cultural dos favoritos do usuário logado",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Não autenticado"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Banco de dados temporariamente indisponível"
        },
    },
)
async def remove_favorite(
    place_id: str, user: CurrentUser, repository: Repository
) -> Response:
    try:
        await repository.remove_favorite(user["_id"], place_id)
    except PyMongoError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=FAVORITE_REMOVE_UNAVAILABLE,
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
