from __future__ import annotations

import json
import time
from fnmatch import fnmatch
from functools import wraps
from typing import Callable, ParamSpec, TypeVar

import redis

from app.core.config import get_settings

P = ParamSpec("P")
R = TypeVar("R")

DEFAULT_REDIS_URL = "redis://localhost:6379/0"

_LOCAL_CACHE_VALUES: dict[str, tuple[float, str]] = {}


class _LocalMemoryCache:
    """Small Redis-like fallback for local tests and Redis outages."""

    def get(self, key: str) -> str | None:
        item = _LOCAL_CACHE_VALUES.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= time.monotonic():
            _LOCAL_CACHE_VALUES.pop(key, None)
            return None
        return value

    def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        _LOCAL_CACHE_VALUES[key] = (time.monotonic() + ttl_seconds, value)

    def delete(self, key: str) -> int:
        existed = key in _LOCAL_CACHE_VALUES
        _LOCAL_CACHE_VALUES.pop(key, None)
        return int(existed)

    def scan_iter(self, match: str):
        now = time.monotonic()
        for key, (expires_at, _) in list(_LOCAL_CACHE_VALUES.items()):
            if expires_at <= now:
                _LOCAL_CACHE_VALUES.pop(key, None)
                continue
            if fnmatch(key, match):
                yield key


class Cache:
    """Redis-backed cache helper with JSON serialization.

    If Redis is unreachable, the cache transparently disables itself so that
    callers fall back to the underlying data source rather than failing.
    """

    def __init__(self, url: str | None = None) -> None:
        url = url or get_settings().redis_url
        try:
            self._client: redis.Redis | _LocalMemoryCache = redis.Redis.from_url(
                url, decode_responses=True, socket_connect_timeout=2
            )
            self._client.ping()
        except Exception:
            self._client = _LocalMemoryCache()

    def get(self, key: str) -> dict[str, object] | None:
        try:
            value = self._client.get(key)
        except Exception:
            return None
        if value is None:
            return None
        return json.loads(value)

    def set(
        self, key: str, value: dict[str, object], ttl_seconds: int
    ) -> None:
        try:
            self._client.setex(key, ttl_seconds, json.dumps(value))
        except Exception:
            return

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except Exception:
            return

    def delete_pattern(self, pattern: str) -> int:
        deleted = 0
        try:
            for key in self._client.scan_iter(match=pattern):
                deleted += self._client.delete(key)
        except Exception:
            return deleted
        return deleted

    def get_idempotency_value(self, key: str) -> str | None:
        try:
            return self._client.get(f"idempotency:{key}")
        except Exception:
            return None

    def set_idempotency_value(
        self, key: str, value: str, ttl_seconds: int
    ) -> None:
        try:
            self._client.setex(f"idempotency:{key}", ttl_seconds, value)
        except Exception:
            return


def cache_result(
    key: str,
    ttl_seconds: int,
    *,
    dump: Callable[[R], dict[str, object]] | None = None,
    load: Callable[[dict[str, object]], R] | None = None,
):
    """Decorator that caches a function's return value using Redis.

    ``dump`` serializes the result for caching; ``load`` reconstructs it on
    cache hits. When omitted, the value is assumed to already be a dict.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            cache = Cache()
            cached = cache.get(key)
            if cached is not None:
                if load is not None:
                    return load(cached)
                return cached  # type: ignore[return-value]
            result = func(*args, **kwargs)
            payload = dump(result) if dump is not None else result
            cache.set(key, payload, ttl_seconds)
            return result

        return wrapper

    return decorator
