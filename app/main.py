import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routers import auth, template
from .core.database import connect_database
from .core.http_client import create_http_client, register_exception_handlers
from .core.security import validate_security_settings

# The Vite dev server; deployments list their frontend origins in CORS_ORIGINS.
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


def cors_origins() -> list[str]:
    value = os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in value.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    validate_security_settings()
    async with connect_database() as database, create_http_client() as http_client:
        app.state.database = database
        app.state.http_client = http_client
        try:
            yield
        finally:
            del app.state.database
            del app.state.http_client


app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)
# The token travels in the Authorization header, not in cookies: no credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(template.router)
app.include_router(auth.router)
