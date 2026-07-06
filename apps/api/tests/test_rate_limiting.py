from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.core.limiter import build_limiter
from app.main import create_app


def test_rate_limit_exceeded_returns_429() -> None:
    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri="memory://",
        default_limits=[],
    )
    app = FastAPI()
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> object:
        from fastapi import Response

        return Response(
            content='{"detail":"Rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
        )

    @app.get("/limited")
    @limiter.limit("1/minute")
    def limited(request: Request) -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)
    assert client.get("/limited").status_code == 200
    response = client.get("/limited")
    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded"


def test_app_includes_rate_limit_middleware() -> None:
    app = create_app()
    middleware_classes = {m.cls.__name__ for m in app.user_middleware}
    assert "SlowAPIMiddleware" in middleware_classes


def test_build_limiter_falls_back_to_memory_when_redis_is_unavailable(
    monkeypatch,
) -> None:
    class UnavailableRedis:
        @classmethod
        def from_url(cls, *args, **kwargs):
            return cls()

        def ping(self) -> None:
            raise OSError("redis unavailable")

    import redis

    monkeypatch.setattr(redis.Redis, "from_url", UnavailableRedis.from_url)

    limiter = build_limiter()

    assert limiter._storage.__class__.__name__ == "MemoryStorage"
