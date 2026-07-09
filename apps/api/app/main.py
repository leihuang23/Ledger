import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.accounts.router import router as accounts_router
from app.agent.router import router as agent_router
from app.agents.router import router as agents_router
from app.approvals.router import approvals_router, mock_actions_router
from app.core.config import get_settings
from app.core.errors import error_response
from app.core.limiter import limiter
from app.evals.router import router as evals_router
from app.health.router import router as health_router
from app.incidents.router import router as incidents_router
from app.knowledge.router import router as knowledge_router
from app.logging_config import configure_logging, get_logger, request_id_context
from app.metrics.router import router as metrics_router
from app.runs.router import router as runs_router
from app.support.router import router as support_router

logger = get_logger("app.main")

# Environments where the error envelope may include exception detail to help
# local debugging. Production-like envs get a generic message only.
# NOTE: "demo" is intentionally excluded -- demo instances may be publicly
# accessible and must not leak internal exception detail.
_DETAIL_ENVS = frozenset({"local", "test", "development"})


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.state.limiter = limiter

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.backend_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.add_middleware(SlowAPIMiddleware)

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        retry_after = getattr(exc, "retry_after", None)
        headers = {"Retry-After": str(retry_after)} if retry_after else {}
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "rate_limited",
                    "message": "Rate limit exceeded",
                    "request_id": request_id_context.get("-"),
                }
            },
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        request_id = request_id_context.get("-")
        logger.error(
            "Unhandled exception in %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=True,
            extra={"request_id": request_id},
        )
        env = get_settings().app_env
        message = (
            str(exc)
            if env in _DETAIL_ENVS
            else "An internal error occurred."
        )
        return error_response("internal_error", message, 500)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        raw_request_id = request.headers.get("X-Request-ID", "")
        # Sanitize: limit length and strip control characters to prevent log
        # injection and header smuggling.
        sanitized = "".join(
            ch for ch in raw_request_id[:64] if ch >= " " and ch != "\x7f"
        ).strip()
        request_id = sanitized or str(uuid.uuid4())
        token = request_id_context.set(request_id)
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        request_id_context.reset(token)
        return response

    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(accounts_router)
    app.include_router(support_router)
    app.include_router(incidents_router)
    app.include_router(knowledge_router)
    app.include_router(agent_router)
    app.include_router(agents_router)
    app.include_router(mock_actions_router)
    app.include_router(approvals_router)
    app.include_router(evals_router)
    app.include_router(runs_router)
    return app


app = create_app()
