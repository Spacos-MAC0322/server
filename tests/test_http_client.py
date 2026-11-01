"""ExternalAPIClient retries, timing and errors: no network and no real waiting."""

import asyncio
import logging

import httpx
import pytest

from app.core.http_client import (
    ExternalAPIConnectionError,
    ExternalAPIError,
    ExternalAPIStatusError,
    ExternalAPITimeoutError,
    RetryPolicy,
    create_http_client,
)

from .http_support import BASE_URL, SERVICE, Upstream


async def hang(_request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(60)
    return httpx.Response(200)


# Retries


async def test_transient_failure_is_retried_until_success(connect):
    upstream = Upstream(httpx.Response(503), httpx.Response(200))

    response = await connect(upstream).request("GET", "/lines")

    assert response.status_code == 200
    assert len(upstream.requests) == 2


async def test_exhausted_retries_raise_the_last_status(connect):
    upstream = Upstream(httpx.Response(503))

    with pytest.raises(ExternalAPIStatusError) as caught:
        await connect(upstream).request("GET", "/lines")

    assert caught.value.status_code == 503
    assert caught.value.service == SERVICE
    assert len(upstream.requests) == 3


@pytest.mark.parametrize(
    ("status", "calls"),
    [(429, 3), (502, 3), (503, 3), (504, 3), (500, 1), (404, 1)],
)
async def test_only_transient_statuses_are_retried(connect, status, calls):
    upstream = Upstream(httpx.Response(status))

    with pytest.raises(ExternalAPIStatusError):
        await connect(upstream).request("GET", "/lines")

    assert len(upstream.requests) == calls


@pytest.mark.parametrize(
    ("retry", "calls"),
    [(RetryPolicy(), 1), (RetryPolicy(methods=frozenset({"POST"})), 3)],
)
async def test_post_is_retried_only_when_the_policy_opts_in(connect, retry, calls):
    upstream = Upstream(httpx.Response(503))

    with pytest.raises(ExternalAPIStatusError):
        await connect(upstream, retry=retry).request("POST", "/Login/Autenticar")

    assert len(upstream.requests) == calls


async def test_method_names_are_case_insensitive(connect):
    upstream = Upstream(httpx.Response(503), httpx.Response(200))

    await connect(upstream).request("get", "/lines")

    assert len(upstream.requests) == 2


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ConnectError("refused"), ExternalAPIConnectionError),
        (httpx.ReadTimeout("slow"), ExternalAPITimeoutError),
    ],
)
async def test_network_failures_retry_then_become_our_errors(
    connect, failure, expected
):
    upstream = Upstream(failure)

    with pytest.raises(expected):
        await connect(upstream).request("GET", "/lines")

    assert len(upstream.requests) == 3


# Timing


async def test_backoff_grows_and_never_exceeds_max_delay(connect, delays):
    retry = RetryPolicy(max_attempts=6, base_delay=1.0, max_delay=4.0)

    with pytest.raises(ExternalAPIStatusError):
        await connect(Upstream(httpx.Response(503)), retry=retry).request("GET", "/x")

    assert 1.0 <= delays[0] <= 2.0
    assert 2.0 <= delays[1] <= 3.0
    assert delays[2:] == [4.0, 4.0, 4.0]


async def test_retry_after_is_honoured_and_capped(connect, delays):
    upstream = Upstream(
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(429, headers={"Retry-After": "3600"}),
        httpx.Response(200),
    )

    await connect(upstream).request("GET", "/x")

    assert delays == [2.0, 8.0]


@pytest.mark.parametrize("value", ["Wed, 21 Oct 2026 07:28:00 GMT", "nan", "-1"])
async def test_unusable_retry_after_falls_back_to_backoff(connect, delays, value):
    upstream = Upstream(
        httpx.Response(429, headers={"Retry-After": value}), httpx.Response(200)
    )

    await connect(upstream).request("GET", "/x")

    assert 0.5 <= delays[0] <= 1.0


async def test_total_timeout_limits_the_whole_call(connect):
    client = connect(hang, retry=RetryPolicy(total_timeout=0.01))

    with pytest.raises(ExternalAPITimeoutError) as caught:
        await client.request("GET", "/x?token=s3cret")

    assert "s3cret" not in str(caught.value)


async def test_api_timeout_overrides_the_client_default(connect):
    upstream = Upstream(httpx.Response(200))

    await connect(upstream, timeout=1.5).request("GET", "/x")

    assert upstream.requests[0].extensions["timeout"]["read"] == 1.5


async def test_default_client_configuration():
    async with create_http_client() as http:
        assert http.timeout == httpx.Timeout(10.0, connect=5.0)
        assert http.follow_redirects is True
        assert http.headers["User-Agent"].startswith("spacos")


# URLs


@pytest.mark.parametrize("path", ["/Linha/Buscar", "Linha/Buscar"])
async def test_paths_keep_the_base_url_prefix(connect, path):
    upstream = Upstream(httpx.Response(200))

    await connect(upstream).request("GET", path)

    assert str(upstream.requests[0].url) == f"{BASE_URL}/Linha/Buscar"


async def test_absolute_urls_work_without_a_base_url(connect):
    upstream = Upstream(httpx.Response(200))

    await connect(upstream, base_url="").request("GET", "https://other.test/x")

    assert str(upstream.requests[0].url) == "https://other.test/x"


# Secrets


@pytest.mark.parametrize(
    "step",
    [
        httpx.Response(500),
        httpx.Response(503),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_errors_and_logs_never_expose_the_query_string(connect, caplog, step):
    with caplog.at_level(logging.WARNING), pytest.raises(ExternalAPIError) as caught:
        await connect(Upstream(step)).request("GET", "/Login/Autenticar?token=s3cret")

    assert "/Login/Autenticar" in str(caught.value)
    assert "s3cret" not in str(caught.value)
    assert "s3cret" not in caplog.text


async def test_httpx_does_not_log_urls_when_the_app_logs_at_info(connect, caplog):
    upstream = Upstream(httpx.Response(200))

    with caplog.at_level(logging.INFO):
        await connect(upstream).request("GET", "/x?token=s3cret")

    assert "s3cret" not in caplog.text
