from __future__ import annotations

from fastapi import HTTPException, status

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
