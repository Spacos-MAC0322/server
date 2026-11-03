"""In-memory TTL cache for responses from external APIs."""

import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode

from cachetools import TTLCache


def make_cache_key(url: str, params: Mapping[str, Any] | None = None) -> str:
    """Identify a request regardless of the order of its query parameters."""
    query = urlencode(sorted((params or {}).items()), doseq=True)
    return f"{url}?{query}" if query else url


class ResponseCache:
    """Keep raw response bodies for `ttl` seconds, up to `maxsize` entries.

    Bodies are stored as bytes, so every caller parses a private copy and cannot
    change what later callers receive. Create one cache per kind of data, because
    each one has a single TTL (DA05: hours for weather, a short time for transit).
    """

    def __init__(
        self,
        ttl: float,
        maxsize: int = 256,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._entries: TTLCache[str, bytes] = TTLCache(maxsize, ttl, timer=timer)

    def get(self, key: str) -> bytes | None:
        return self._entries.get(key)

    def set(self, key: str, body: bytes) -> None:
        self._entries[key] = body

    def clear(self) -> None:
        self._entries.clear()
