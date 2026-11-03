from app.core.cache import ResponseCache, make_cache_key

URL = "https://api.test/forecast"


def test_miss_returns_none():
    assert ResponseCache(ttl=60).get("missing") is None


def test_entry_lives_until_its_ttl(clock):
    cache = ResponseCache(ttl=60, timer=clock)
    cache.set("key", b"body")

    clock.now = 59
    assert cache.get("key") == b"body"

    clock.now = 60
    assert cache.get("key") is None


def test_oldest_entry_is_evicted_at_maxsize():
    cache = ResponseCache(ttl=60, maxsize=2)

    for key in ("first", "second", "third"):
        cache.set(key, b"body")

    assert cache.get("first") is None
    assert cache.get("second") == b"body"
    assert cache.get("third") == b"body"


def test_clear_removes_everything():
    cache = ResponseCache(ttl=60)
    cache.set("key", b"body")

    cache.clear()

    assert cache.get("key") is None


def test_key_ignores_parameter_order():
    first = make_cache_key(URL, {"latitude": -23.55, "longitude": -46.63})
    second = make_cache_key(URL, {"longitude": -46.63, "latitude": -23.55})

    assert first == second


def test_key_depends_on_url_and_parameter_values():
    base = make_cache_key(URL, {"latitude": -23.55})

    assert base != make_cache_key(URL, {"latitude": -23.56})
    assert base != make_cache_key(f"{URL}/daily", {"latitude": -23.55})


def test_key_without_parameters_is_the_url():
    assert make_cache_key(URL) == URL
    assert make_cache_key(URL, {}) == URL


def test_key_cannot_be_forged_by_a_parameter_value():
    # Unescaped, both requests would produce "a=1&b=2" and share a cache entry.
    assert make_cache_key(URL, {"a": "1&b=2"}) != make_cache_key(URL, {"a": 1, "b": 2})
