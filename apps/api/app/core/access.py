from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from app.core.config import get_settings

DEMO_DATA_ENVIRONMENTS = {"local", "test", "development", "demo"}


def require_demo_data_access() -> None:
    settings = get_settings()
    if settings.app_env not in DEMO_DATA_ENVIRONMENTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Demo data endpoints are only available in local, test, "
                "development, or demo environments."
            ),
        )


def require_demo_operator_access(
    demo_operator_token: str | None = Header(default=None, alias="X-Demo-Operator-Token"),
) -> None:
    require_demo_data_access()
    settings = get_settings()
    if settings.demo_operator_token is None:
        if settings.app_env == "demo":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Demo mutation API is disabled. Set DEMO_OPERATOR_TOKEN "
                    "and pass X-Demo-Operator-Token for public demo writes."
                ),
            )
        return

    if demo_operator_token is None or not secrets.compare_digest(
        demo_operator_token, settings.demo_operator_token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid demo operator token.",
        )
