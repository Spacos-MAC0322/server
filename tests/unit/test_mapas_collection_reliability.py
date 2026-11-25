"""Transient retries and safe diagnostics for the offline snapshot collector."""

import json

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import ExternalServiceUnavailable
from app.integrations import mapas_culturais as integration
from app.scripts.export_mapas_spaces import export_spaces


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(integration, "SPACE_RETRY_DELAY_SECONDS", 0)


def client(handler):
    return integration.MapasCulturaisClient(
        Settings(
            mapas_culturais_enabled=True,
            mapas_culturais_base_url="https://saas.mapas.cultura.gov.br",
        ),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
async def test_transient_error_retries_the_same_page_without_losing_rows(status):
    pages = []

    def handler(request):
        page = int(request.url.params["@page"])
        pages.append(page)
        if len(pages) <= 2:
            return httpx.Response(status, text="secret upstream body")
        return httpx.Response(
            200,
            json=[]
            if page == 2
            else [
                {
                    "id": 1,
                    "name": "Museu",
                    "status": 1,
                    "En_Municipio": "São Paulo",
                    "location": {"longitude": -46.63, "latitude": -23.55},
                }
            ],
        )

    places = await client(handler).fetch_spaces()
    assert len(places) == 1
    assert pages == [1, 1, 1, 2]


async def test_challenge_is_not_retried_and_manifest_explains_the_failure(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            403,
            headers={"cf-mitigated": "challenge"},
            text="<html>secret challenge contents</html>",
        )

    report = await export_spaces(tmp_path, client=client(handler))
    assert len(requests) == 1
    assert report["complete"] is False
    assert report["error_details"] == [
        {
            "http_status": 403,
            "reason": "http_challenge",
            "page": 1,
            "attempts": 1,
        }
    ]
    assert "secret" not in json.dumps(report)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_nontransient_status_is_not_retried(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status)

    with pytest.raises(ExternalServiceUnavailable) as failure:
        await client(handler).fetch_spaces()
    assert len(requests) == 1
    assert failure.value.status_code == status
    assert failure.value.reason == "http_status"
    assert failure.value.page == failure.value.attempts == 1


async def test_exhausted_retry_records_http_status_and_attempt_count(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503, text="secret upstream details")

    report = await export_spaces(tmp_path, client=client(handler))
    assert len(requests) == 3
    assert report["stop_reason"] == "request_failed"
    assert report["errors"] == ["ExternalServiceUnavailable"]
    assert report["error_details"] == [
        {
            "http_status": 503,
            "reason": "http_status",
            "page": 1,
            "attempts": 3,
        }
    ]
    assert "secret" not in json.dumps(report)


@pytest.mark.parametrize(
    ("failure", "reason", "name"),
    [
        (httpx.ReadTimeout, "timeout", "ExternalServiceTimeout"),
        (httpx.ConnectError, "connection", "ExternalServiceUnavailable"),
    ],
)
async def test_connection_failures_are_retried_with_safe_diagnostics(
    tmp_path, failure, reason, name
):
    requests = []

    def handler(request):
        requests.append(request)
        raise failure("secret connection details")

    report = await export_spaces(tmp_path, client=client(handler))
    assert len(requests) == 3
    assert report["errors"] == [name]
    assert report["error_details"] == [{"reason": reason, "page": 1, "attempts": 3}]
    assert "secret" not in json.dumps(report)
