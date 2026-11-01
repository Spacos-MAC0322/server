"""How the HTTP client plugs into the FastAPI application."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI, Request

from app import main
from app.core.http_client import (
    ExternalAPIConnectionError,
    ExternalAPIError,
    ExternalAPIResponseError,
    ExternalAPIStatusError,
    ExternalAPITimeoutError,
    get_http_client,
    register_exception_handlers,
)

from .http_support import SERVICE


@asynccontextmanager
async def stub_database() -> AsyncGenerator[object, None]:
    yield object()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ExternalAPITimeoutError(SERVICE, "GET https://api.test/x timed out"), 504),
        (ExternalAPIConnectionError(SERVICE, "GET https://api.test/x failed"), 502),
        (ExternalAPIStatusError(SERVICE, "GET https://api.test/x returned 503"), 502),
        (ExternalAPIResponseError(SERVICE, "GET https://api.test/x invalid"), 502),
    ],
)
async def test_unhandled_failures_become_clean_api_errors(error, status):
    api = FastAPI()
    register_exception_handlers(api)

    @api.get("/boom")
    async def boom():
        raise error

    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/boom")

    assert response.status_code == status
    assert response.json() == {
        "detail": "External service unavailable",
        "service": SERVICE,
    }


def test_application_registers_the_handler():
    assert ExternalAPIError in main.app.exception_handlers


async def test_lifespan_opens_and_closes_the_http_client(monkeypatch):
    monkeypatch.setattr(main, "connect_database", stub_database)
    request = Request({"type": "http", "app": main.app})

    async with main.app.router.lifespan_context(main.app):
        http = get_http_client(request)
        assert not http.is_closed

    assert http.is_closed
    assert not hasattr(main.app.state, "http_client")


async def test_lifespan_closes_the_http_client_if_the_application_raises(monkeypatch):
    monkeypatch.setattr(main, "connect_database", stub_database)
    opened: list[httpx.AsyncClient] = []

    with pytest.raises(RuntimeError, match="request failed"):
        async with main.app.router.lifespan_context(main.app):
            opened.append(main.app.state.http_client)
            raise RuntimeError("request failed")

    assert opened[0].is_closed
    assert not hasattr(main.app.state, "http_client")
