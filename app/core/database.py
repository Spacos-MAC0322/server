"""Async MongoDB connection, validators and indexes shared by API and CLI."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files

from fastapi import Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure

from .config import get_database_settings

COLLECTION_DEFINITIONS = json.loads(
    files("app.core").joinpath("mongodb_schema.json").read_text(encoding="utf-8")
)["collections"]


async def ensure_collections(database: AsyncDatabase) -> None:
    """Create collections or apply their validators without replacing existing data."""
    existing = set(await database.list_collection_names())
    for name, definition in COLLECTION_DEFINITIONS.items():
        options = {key: value for key, value in definition.items() if key != "indexes"}
        if name not in existing:
            try:
                await database.create_collection(name, check_exists=False, **options)
            except OperationFailure as error:
                # Another application worker may have created it after our initial list.
                if error.code != 48:  # NamespaceExists
                    raise
            else:
                continue
        await database.command("collMod", name, **options)


async def ensure_indexes(database: AsyncDatabase) -> None:
    """Ensure every index from the shared model, including email and geospatial ones."""
    for name, definition in COLLECTION_DEFINITIONS.items():
        for index in definition["indexes"]:
            keys = [(field, direction) for field, direction in index["key"]]
            options = {key: value for key, value in index.items() if key != "key"}
            await database[name].create_index(keys, **options)


async def initialize_database(database: AsyncDatabase) -> None:
    """Install the complete model before exposing the database to the application."""
    await ensure_collections(database)
    await ensure_indexes(database)


@asynccontextmanager
async def connect_database() -> AsyncIterator[AsyncDatabase]:
    """Open one client per application lifespan and close it even if startup fails."""
    settings = get_database_settings()
    client = AsyncMongoClient(
        settings.mongo_uri.get_secret_value(),
        serverSelectionTimeoutMS=5000,
    )
    try:
        database = client[settings.mongodb_db_name]
        await database.command("ping")
        await initialize_database(database)
        yield database
    finally:
        await client.close()


def get_database(request: Request) -> AsyncDatabase:
    """FastAPI dependency for the database initialized during application startup."""
    return request.app.state.database
