from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings


def build_limiter() -> Limiter:
    settings = get_settings()
    # Prefer Redis for distributed rate limiting, but fall back to in-memory
    # storage when Redis is unavailable (e.g., lightweight local tests).
    try:
        import redis

        redis_client = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=2
        )
        redis_client.ping()
        storage_uri = settings.redis_url
    except Exception:
        storage_uri = "memory://"

    return Limiter(
        key_func=get_remote_address,
        storage_uri=storage_uri,
        default_limits=[],
        strategy="fixed-window",
    )


limiter = build_limiter()
