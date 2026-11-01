from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .core.database import connect_database
from .core.http_client import create_http_client, register_exception_handlers
from .routers import template


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

app.include_router(template.router)
