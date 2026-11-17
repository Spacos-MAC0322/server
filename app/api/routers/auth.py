"""Account endpoints: sign-up, login and the logged-in user."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ...core.security import CurrentUser
from ...repositories.user_repository import EmailAlreadyRegisteredError
from ...schemas.user import (
    TokenSchema,
    UserCreateSchema,
    UserLoginSchema,
    UserResponseSchema,
)
from ...services.auth import AuthService, InvalidCredentialsError, get_auth_service

EMAIL_TAKEN = "Este e-mail já está cadastrado."
INVALID_CREDENTIALS = "E-mail ou senha incorretos."

router = APIRouter(prefix="/api/auth", tags=["auth"])
Service = Annotated[AuthService, Depends(get_auth_service)]


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"description": EMAIL_TAKEN}},
)
async def register(data: UserCreateSchema, service: Service) -> UserResponseSchema:
    try:
        return await service.register(data)
    except EmailAlreadyRegisteredError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN) from error


@router.post(
    "/login",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": INVALID_CREDENTIALS}},
)
async def login(data: UserLoginSchema, service: Service) -> TokenSchema:
    try:
        return await service.authenticate(data)
    except InvalidCredentialsError as error:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


@router.get("/me")
async def me(user: CurrentUser) -> UserResponseSchema:
    return UserResponseSchema.from_document(user)
