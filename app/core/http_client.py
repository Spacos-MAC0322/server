"""Shared access to external APIs: timeouts, retries and uniform errors.

The application owns one `httpx.AsyncClient`, and each integration wraps it once:

    forecast_cache = ResponseCache(ttl=3 * 3600)
    open_meteo = ExternalAPIClient(
        http, service="open_meteo", base_url="https://api.open-meteo.com"
    )
    data = await open_meteo.get_json(
        "/v1/forecast", params=params, cache=forecast_cache
    )

Every failure surfaces as an `ExternalAPIError`, so callers can degrade gracefully
by catching a single type.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .cache import ResponseCache, make_cache_key

logger = logging.getLogger(__name__)

# httpx logs full URLs at INFO, and SPTrans sends its token in the query string.
logging.getLogger("httpx").setLevel(logging.WARNING)

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
USER_AGENT = "spacos-server/0.1"


class ExternalAPIError(Exception):
    """A call to an external API failed; `service` says which one.

    Messages never contain query strings, which may carry credentials.
    """

    http_status = 502

    def __init__(
        self,
        service: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"{service}: {message}")
        self.service = service
        self.status_code = status_code
        self.retry_after = retry_after


class ExternalAPITimeoutError(ExternalAPIError):
    """The API did not answer in time."""

    http_status = 504


class ExternalAPIConnectionError(ExternalAPIError):
    """The API could not be reached: DNS, refused or reset connections."""


class ExternalAPIStatusError(ExternalAPIError):
    """The API answered with a non-2xx status, kept in `status_code`."""


class ExternalAPIResponseError(ExternalAPIError):
    """The API answered 2xx, but the body could not be parsed."""


@dataclass(frozen=True)
class RetryPolicy:
    """How persistent to be; on-demand adapters should use a short `total_timeout`.

    `total_timeout` covers every attempt and wait, because httpx timeouts apply to
    each operation separately and retries multiply them. Only `methods` are retried,
    so a request with side effects is never repeated unless a policy opts in.
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    total_timeout: float = 30.0
    statuses: frozenset[int] = frozenset({429, 502, 503, 504})
    methods: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

    def should_retry(self, error: BaseException) -> bool:
        if isinstance(error, ExternalAPIStatusError):
            return error.status_code in self.statuses
        return isinstance(error, ExternalAPITimeoutError | ExternalAPIConnectionError)


DEFAULT_RETRY_POLICY = RetryPolicy()


def create_http_client(**overrides: Any) -> httpx.AsyncClient:
    """Build the application's client; `overrides` go straight to httpx."""
    options: dict[str, Any] = {
        "timeout": DEFAULT_TIMEOUT,
        "follow_redirects": True,
        "headers": {"User-Agent": USER_AGENT},
    }
    return httpx.AsyncClient(**(options | overrides))


def _retry_delay(policy: RetryPolicy) -> Callable[[RetryCallState], float]:
    """Wait as long as the server asked, within limits, or back off exponentially."""
    backoff = wait_exponential_jitter(
        multiplier=policy.base_delay, max=policy.max_delay, jitter=policy.base_delay
    )

    def delay(state: RetryCallState) -> float:
        retry_after = state.outcome.exception().retry_after
        if retry_after is not None:
            return min(retry_after, policy.max_delay)
        return backoff(state)

    return delay


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Read `Retry-After` in seconds; its HTTP-date form falls back to backoff."""
    value = response.headers.get("Retry-After", "")
    return float(value) if value.isdecimal() else None


def _describe(method: str, url: str) -> str:
    """Name a request for messages, leaving out the query string."""
    return f"{method} {url.partition('?')[0]}"


class ExternalAPIClient:
    """One external API, reached through the application's shared httpx client.

    `timeout` replaces the client's default for this API. `sleep` exists so tests
    don't really wait between attempts.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        service: str,
        *,
        base_url: str = "",
        timeout: httpx.Timeout | float | None = None,
        retry: RetryPolicy = DEFAULT_RETRY_POLICY,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._service = service
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retry = retry
        self._sleep = sleep

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a request, retrying transient failures, and return a 2xx response.

        `kwargs` go to `httpx.AsyncClient.request`. Raises `ExternalAPIError`.
        """
        method = method.upper()
        url = self._url(path)
        # Passing timeout=None to httpx would disable timeouts altogether.
        if self._timeout is not None:
            kwargs.setdefault("timeout", self._timeout)
        policy = self._retry
        attempts = policy.max_attempts if method in policy.methods else 1
        retrying = AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=_retry_delay(policy),
            retry=retry_if_exception(policy.should_retry),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            sleep=self._sleep,
            reraise=True,
        )
        try:
            async with asyncio.timeout(policy.total_timeout):
                return await retrying(self._send, method, url, **kwargs)
        except TimeoutError as error:
            message = f"{_describe(method, url)} exceeded {policy.total_timeout}s"
            raise ExternalAPITimeoutError(self._service, message) from error

    async def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        cache: ResponseCache | None = None,
    ) -> Any:
        """GET and parse JSON, reusing `cache` when given. Failures are not cached."""
        key = make_cache_key(self._url(path), params)
        if cache is not None and (body := cache.get(key)) is not None:
            return json.loads(body)
        response = await self.request("GET", path, params=params)
        try:
            data = response.json()
        except ValueError as error:
            message = f"{_describe('GET', self._url(path))} returned invalid JSON"
            raise ExternalAPIResponseError(self._service, message) from error
        if cache is not None:
            cache.set(key, response.content)
        return data

    def _url(self, path: str) -> str:
        """Append to `base_url`, unlike URL.join, to keep prefixes such as /v2.1."""
        if not self._base_url:
            return path
        return f"{self._base_url}/{path.lstrip('/')}"

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Make one attempt, turning every failure into an `ExternalAPIError`."""
        target = _describe(method, url)
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as error:
            message = f"{target} timed out"
            raise ExternalAPITimeoutError(self._service, message) from error
        except httpx.RequestError as error:
            message = f"{target} failed: {type(error).__name__}"
            raise ExternalAPIConnectionError(self._service, message) from error
        # raise_for_status() is avoided because its message includes the full URL.
        if not response.is_success:
            raise ExternalAPIStatusError(
                self._service,
                f"{target} returned {response.status_code}",
                status_code=response.status_code,
                retry_after=_parse_retry_after(response),
            )
        return response


def get_http_client(request: Request) -> httpx.AsyncClient:
    """FastAPI dependency for the client opened during application startup."""
    return request.app.state.http_client


async def _handle_external_api_error(
    request: Request, error: ExternalAPIError
) -> JSONResponse:
    logger.warning("External API failure reached the API layer: %s", error)
    return JSONResponse(
        status_code=error.http_status,
        content={"detail": "External service unavailable", "service": error.service},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Answer external failures nobody handled with a clean 502/504, not a 500."""
    app.add_exception_handler(ExternalAPIError, _handle_external_api_error)
