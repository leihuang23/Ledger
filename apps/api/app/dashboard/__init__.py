"""Dashboard domain — read-only per-agent-version observability aggregates
(PRD US-6, FR-19, FR-20, AC-6.2)."""

from __future__ import annotations

from .router import router

__all__ = ["router"]
