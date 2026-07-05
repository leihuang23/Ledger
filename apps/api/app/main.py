import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.accounts.router import router as accounts_router
from app.agent.router import router as agent_router
from app.approvals.router import approvals_router, mock_actions_router
from app.core.config import get_settings
from app.evals.router import router as evals_router
from app.health.router import router as health_router
from app.incidents.router import router as incidents_router
from app.knowledge.router import router as knowledge_router
from app.logging_config import configure_logging
from app.metrics.router import router as metrics_router
from app.support.router import router as support_router

request_id_context: ContextVar[str] = ContextVar("request_id")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title=settings.app_name, version=settings.app_version)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.backend_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
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
    app.include_router(mock_actions_router)
    app.include_router(approvals_router)
    app.include_router(evals_router)
    return app


app = create_app()
