from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.error_handlers import register_error_handlers
from .api.routers import itineraries, places
from .core.config import get_cors_origins
from .core.database import connect_database
from .core.http_client import create_http_client, register_exception_handlers
from .routers import health, template


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["Content-Type", "Authorization"],
)

register_error_handlers(app)

app.include_router(health.router)
app.include_router(template.router)
app.include_router(places.router)
app.include_router(itineraries.router)
