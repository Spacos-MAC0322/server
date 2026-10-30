from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.config import get_cors_origins
from .core.database import connect_database
from .routers import template


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with connect_database() as database:
        app.state.database = database
        try:
            yield
        finally:
            del app.state.database


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(template.router)
