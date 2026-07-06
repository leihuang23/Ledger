from __future__ import annotations

from fastapi.responses import JSONResponse

from app.logging_config import request_id_context


def error_response(code: str, message: str, status_code: int) -> JSONResponse:
    """Build the structured error envelope with the current request_id.

    Used by the global ``Exception`` handler and by route-layer try/except
    blocks that map known failures to specific HTTP status codes.
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id_context.get("-"),
            }
        },
    )
