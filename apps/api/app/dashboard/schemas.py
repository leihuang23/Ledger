from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AgentVersionObservability(BaseModel):
    """Per-agent-version aggregate used by the observability dashboard
    (PRD FR-19, AC-6.2).

    Cost fields are always *estimates* (``cost_estimate_usd``); the no-LLM
    fallback path records a zero cost so a reviewer can see when the agent fell
    back to deterministic diagnosis. Latency is derived from
    ``completed_at - started_at`` (FR-19) and is ``None`` when the version has
    no completed runs. On Postgres, avg/p95 latency are full-population
    SQL-side aggregates (``percentile_cont``); on other dialects (SQLite in
    tests) they are computed over a bounded sample of the most recent 10,000
    runs per version.
    """

    agent_id: str
    agent_version_id: str
    agent_name: str
    # AgentVersion.semantic_version is nullable (drafts set it to None); coalesce
    # so a run against a draft-less version cannot raise a ValidationError -> 500.
    semantic_version: str | None = None
    model: str
    total_runs: int
    successful_runs: int
    success_rate: float
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    avg_cost_estimate_usd: float
    total_cost_estimate_usd: float
    last_run_at: datetime | None


class AgentObservabilitySummary(BaseModel):
    """Per-agent rollup collapsing all versions into one summary row
    (PRD §10: ``GET /dashboard/agents`` — per-agent summary).

    Cost fields are always *estimates*. Latency aggregates are computed across
    every run of every version for the agent; ``None`` when the agent has no
    completed runs. On Postgres they are full-population SQL-side aggregates;
    on other dialects (SQLite in tests) they derive from a bounded sample of
    the most recent 10,000 runs per version.
    """

    agent_id: str
    agent_name: str
    version_count: int
    total_runs: int
    successful_runs: int
    success_rate: float
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    avg_cost_estimate_usd: float
    total_cost_estimate_usd: float
    last_run_at: datetime | None
