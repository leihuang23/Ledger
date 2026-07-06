from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from app.cache import Cache, cache_result, _reset_redis_client


@pytest.fixture(autouse=True)
def _isolate_cache_singleton() -> Iterator[None]:
    """Ensure each test starts with a fresh Redis singleton so monkeypatches
    on ``redis.Redis.from_url`` take effect deterministically."""
    _reset_redis_client()
    yield
    _reset_redis_client()


@pytest.fixture
def cache() -> Cache:
    return Cache()


def test_cache_round_trip(cache: Cache) -> None:
    key = f"test:round_trip:{uuid.uuid4()}"
    assert cache.get(key) is None
    cache.set(key, {"value": 42}, ttl_seconds=10)
    assert cache.get(key) == {"value": 42}


def test_cache_delete(cache: Cache) -> None:
    key = f"test:delete:{uuid.uuid4()}"
    cache.set(key, {"value": 42}, ttl_seconds=10)
    cache.delete(key)
    assert cache.get(key) is None


def test_cache_delete_pattern(cache: Cache) -> None:
    prefix = f"test:pattern:{uuid.uuid4()}"
    cache.set(f"{prefix}:a", {"value": 1}, ttl_seconds=10)
    cache.set(f"{prefix}:b", {"value": 2}, ttl_seconds=10)
    cache.set(f"{prefix}:c", {"value": 3}, ttl_seconds=10)
    deleted = cache.delete_pattern(f"{prefix}:*")
    assert deleted == 3
    assert cache.get(f"{prefix}:a") is None


def test_cache_idempotency_round_trip(cache: Cache) -> None:
    key = f"idempotency-test-{uuid.uuid4()}"
    assert cache.get_idempotency_value(key) is None
    cache.set_idempotency_value(key, "run_123", ttl_seconds=10)
    assert cache.get_idempotency_value(key) == "run_123"


def test_cache_uses_shared_memory_fallback_when_redis_is_unavailable(
    monkeypatch,
) -> None:
    def raise_connection_error(*args, **kwargs):
        raise OSError("redis unavailable")

    import redis

    monkeypatch.setattr(redis.Redis, "from_url", raise_connection_error)
    key = f"test:memory-fallback:{uuid.uuid4()}"

    Cache().set(key, {"value": 99}, ttl_seconds=10)

    assert Cache().get(key) == {"value": 99}


def test_cache_result_decorator_uses_cache(cache: Cache) -> None:
    call_count = 0
    key = f"test:decorator:{uuid.uuid4()}"

    @cache_result(key, ttl_seconds=10)
    def compute() -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        return {"call": call_count}

    assert compute() == {"call": 1}
    assert compute() == {"call": 1}
    cache.delete(key)
    assert compute() == {"call": 2}


def test_cache_result_decorator_with_dump_and_load(cache: Cache) -> None:
    from pydantic import BaseModel

    class Widget(BaseModel):
        name: str
        count: int

    call_count = 0
    key = f"test:decorator:typed:{uuid.uuid4()}"

    @cache_result(
        key,
        ttl_seconds=10,
        dump=lambda widget: widget.model_dump(mode="json"),
        load=Widget.model_validate,
    )
    def build_widget() -> Widget:
        nonlocal call_count
        call_count += 1
        return Widget(name="test", count=call_count)

    first = build_widget()
    assert first.count == 1
    second = build_widget()
    assert second.count == 1
    assert call_count == 1
    cache.delete(key)


def test_cache_reuses_shared_client_across_instances() -> None:
    """Cache() must reuse a module-level Redis client rather than constructing
    (and pinging) a new one on every call (audit P1 #3)."""
    from app.cache import _reset_redis_client

    _reset_redis_client()
    try:
        cache_a = Cache()
        cache_b = Cache()
        assert cache_a._client is cache_b._client
    finally:
        _reset_redis_client()
