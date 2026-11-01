"""ExternalAPIClient.get_json: parsing and the TTL cache for external responses."""

import httpx
import pytest

from app.core.cache import ResponseCache
from app.core.http_client import (
    ExternalAPIResponseError,
    ExternalAPIStatusError,
    RetryPolicy,
)

from .http_support import Upstream

NO_RETRY = RetryPolicy(max_attempts=1)
FORECAST = {"hourly": {"temperature": [21.5]}}


async def test_get_json_sends_params_and_parses_the_body(connect):
    upstream = Upstream(httpx.Response(200, json=FORECAST))

    data = await connect(upstream).get_json("/forecast", params={"latitude": -23.55})

    assert data == FORECAST
    assert upstream.requests[0].url.params["latitude"] == "-23.55"


async def test_cache_hit_skips_the_network(connect):
    upstream = Upstream(httpx.Response(200, json=FORECAST))
    client, cache = connect(upstream), ResponseCache(ttl=60)

    first = await client.get_json("/forecast", params={"a": 1}, cache=cache)
    second = await client.get_json("/forecast", params={"a": 1}, cache=cache)

    assert first == second == FORECAST
    assert len(upstream.requests) == 1


async def test_different_params_do_not_share_an_entry(connect):
    upstream = Upstream(httpx.Response(200, json=FORECAST))
    client, cache = connect(upstream), ResponseCache(ttl=60)

    await client.get_json("/forecast", params={"a": 1}, cache=cache)
    await client.get_json("/forecast", params={"a": 2}, cache=cache)

    assert len(upstream.requests) == 2


async def test_without_a_cache_every_call_reaches_the_api(connect):
    upstream = Upstream(httpx.Response(200, json=FORECAST))
    client = connect(upstream)

    await client.get_json("/forecast")
    await client.get_json("/forecast")

    assert len(upstream.requests) == 2


async def test_expired_entry_is_fetched_again(connect, clock):
    upstream = Upstream(httpx.Response(200, json=FORECAST))
    client, cache = connect(upstream), ResponseCache(ttl=60, timer=clock)

    await client.get_json("/forecast", cache=cache)
    clock.now = 61
    await client.get_json("/forecast", cache=cache)

    assert len(upstream.requests) == 2


async def test_mutating_a_result_does_not_change_the_cache(connect):
    upstream = Upstream(httpx.Response(200, json=FORECAST))
    client, cache = connect(upstream), ResponseCache(ttl=60)

    for _ in range(3):
        data = await client.get_json("/forecast", cache=cache)
        assert data == FORECAST
        data["hourly"]["temperature"].clear()

    assert len(upstream.requests) == 1


async def test_parameter_order_does_not_split_the_cache(connect):
    upstream = Upstream(httpx.Response(200, json=FORECAST))
    client, cache = connect(upstream), ResponseCache(ttl=60)

    await client.get_json("/forecast", params={"a": 1, "b": 2}, cache=cache)
    await client.get_json("/forecast", params={"b": 2, "a": 1}, cache=cache)

    assert len(upstream.requests) == 1


async def test_failures_are_not_cached(connect):
    upstream = Upstream(httpx.Response(503), httpx.Response(200, json=FORECAST))
    client, cache = connect(upstream, retry=NO_RETRY), ResponseCache(ttl=60)

    with pytest.raises(ExternalAPIStatusError):
        await client.get_json("/forecast", cache=cache)

    assert await client.get_json("/forecast", cache=cache) == FORECAST


async def test_invalid_json_is_rejected_and_not_cached(connect):
    upstream = Upstream(
        httpx.Response(200, content=b"<html>"), httpx.Response(200, json=FORECAST)
    )
    client, cache = connect(upstream), ResponseCache(ttl=60)

    with pytest.raises(ExternalAPIResponseError) as caught:
        await client.get_json("/forecast?token=s3cret", cache=cache)

    assert "s3cret" not in str(caught.value)
    assert await client.get_json("/forecast?token=s3cret", cache=cache) == FORECAST
