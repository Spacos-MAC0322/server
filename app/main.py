from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

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

app.include_router(template.router)
