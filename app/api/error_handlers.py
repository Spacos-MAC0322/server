"""Translate unhandled integration failures into stable HTTP responses."""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    ExternalIntegrationError,
    ExternalServiceInvalidResponse,
    ExternalServiceTimeout,
    ExternalServiceUnavailable,
)

logger = logging.getLogger(__name__)


async def external_integration_error_handler(
    request: Request, exc: ExternalIntegrationError
) -> JSONResponse:
    """Handle failures only after the service has exhausted its fallback options."""
    if isinstance(exc, ExternalServiceTimeout):
        status_code = status.HTTP_504_GATEWAY_TIMEOUT
        code = "EXTERNAL_SERVICE_TIMEOUT"
        message = "O serviço externo excedeu o tempo de resposta."
    elif isinstance(exc, ExternalServiceUnavailable):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        code = "EXTERNAL_SERVICE_UNAVAILABLE"
        message = "O serviço externo está temporariamente indisponível."
    elif isinstance(exc, ExternalServiceInvalidResponse):
        status_code = status.HTTP_502_BAD_GATEWAY
        code = "EXTERNAL_SERVICE_INVALID_RESPONSE"
        message = "O serviço externo retornou uma resposta inválida."
    else:
        status_code = status.HTTP_502_BAD_GATEWAY
        code = "EXTERNAL_INTEGRATION_ERROR"
        message = "Não foi possível concluir a comunicação com o serviço externo."

    # Do not log raw responses, request URLs or exception causes with credentials.
    logger.warning(
        "External integration failure: service=%s code=%s",
        exc.service,
        code,
    )
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register the integration exception family without overriding other errors."""
    app.add_exception_handler(
        ExternalIntegrationError, external_integration_error_handler
    )
