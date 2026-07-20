from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Literal, NamedTuple

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.models import Agent, AgentRun, AgentVersion

from .schemas import AgentObservabilitySummary, AgentVersionObservability

# Latency sampling is bounded so the observability dashboard never reads the
# full agent_runs table into Python. On SQLite (tests) avg/p95 are computed in
# Python over a bounded sample: p95 is based on the most recent N runs per
# version. On Postgres both aggregates are computed SQL-side over the full
# population.
_LATENCY_SAMPLE_LIMIT = 10_000


class _LatencyStats(NamedTuple):
    avg_ms: float | None
    p95_ms: float | None


_EMPTY_LATENCY_STATS = _LatencyStats(avg_ms=None, p95_ms=None)


def _p95_nearest_rank(values: list[int]) -> float | None:
    """Nearest-rank p95. ``rank = ceil(0.95 * N)`` (1-indexed), then take the
    value at that rank in the sorted-ascending list. Used by the non-Postgres
    fallback path (``_latency_stats_in_memory``); Postgres computes p95
    SQL-side with ``percentile_cont`` instead.

    With N=0 -> None. With N=1 -> that single value. With N=4 -> rank=4 -> the
    max, which is the conventional nearest-rank p95 for small samples.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return float(ordered[rank - 1])


def _build_entry(
    *,
    agent_version_id: str,
    agent_id: str,
    agent_name: str,
    semantic_version: str | None,
    model: str,
    total_runs: int,
    successful_runs: int,
    total_cost: float,
    latency: _LatencyStats,
    last_run_at: datetime | None,
) -> AgentVersionObservability:
    success_rate = round(successful_runs / total_runs, 4) if total_runs else 0.0
    avg_cost = round(total_cost / total_runs, 4) if total_runs else 0.0
    return AgentVersionObservability(
        agent_id=agent_id,
        agent_version_id=agent_version_id,
        agent_name=agent_name,
        semantic_version=semantic_version,
        model=model,
        total_runs=total_runs,
        successful_runs=successful_runs,
        success_rate=success_rate,
        avg_latency_ms=latency.avg_ms,
        p95_latency_ms=latency.p95_ms,
        avg_cost_estimate_usd=avg_cost,
        total_cost_estimate_usd=round(total_cost, 4),
        last_run_at=last_run_at,
    )


def _version_aggregate_rows(
    session: Session, *, agent_id_filter: str | None
) -> list:
    """Per-version counts + cost sums + last-run timestamp, joined to the
    agent/version for labels. Portable SQL; latency is computed separately
    (datetime subtraction is not portable across Postgres/SQLite)."""
    query = (
        select(
            AgentRun.agent_version_id.label("agent_version_id"),
            AgentVersion.agent_id.label("agent_id"),
            Agent.name.label("agent_name"),
            AgentVersion.semantic_version.label("semantic_version"),
            AgentVersion.model.label("model"),
            func.count().label("total_runs"),
            func.count(
                case((AgentRun.status == "succeeded", 1), else_=None)
            ).label("successful_runs"),
            func.coalesce(
                func.sum(AgentRun.cost_estimate_usd), 0.0
            ).label("total_cost"),
            func.max(AgentRun.created_at).label("last_run_at"),
        )
        .select_from(AgentRun)
        .join(AgentVersion, AgentVersion.id == AgentRun.agent_version_id)
        .join(Agent, Agent.id == AgentVersion.agent_id)
        .group_by(
            AgentRun.agent_version_id,
            AgentVersion.agent_id,
            Agent.name,
            AgentVersion.semantic_version,
            AgentVersion.model,
            AgentVersion.version_number,
        )
        .order_by(
            Agent.name,
            AgentVersion.version_number.is_(None),
            AgentVersion.version_number,
            AgentRun.agent_version_id,
        )
    )
    if agent_id_filter is not None:
        query = query.where(AgentVersion.agent_id == agent_id_filter)
    return session.execute(query).all()


def _latency_stats(
    session: Session,
    *,
    group_by: Literal["version", "agent"],
    agent_id_filter: str | None,
) -> dict[str, _LatencyStats]:
    """Per-version (or per-agent) avg/p95 latency in ms for runs that have
    both timestamps (FR-19: p95 from ``completed_at - started_at``). Branches
    by dialect like ``knowledge.search``: Postgres aggregates SQL-side with
    ``percentile_cont``; other dialects fall back to a bounded in-memory
    sample."""
    if session.get_bind().dialect.name == "postgresql":
        return _latency_stats_postgres(
            session, group_by=group_by, agent_id_filter=agent_id_filter
        )
    return _latency_stats_in_memory(
        session, group_by=group_by, agent_id_filter=agent_id_filter
    )


def _latency_stats_postgres(
    session: Session,
    *,
    group_by: Literal["version", "agent"],
    agent_id_filter: str | None,
) -> dict[str, _LatencyStats]:
    """Full-population latency aggregates computed in SQL. ``percentile_cont``
    is Postgres-only, so this path is selected by dialect; it returns one row
    per group instead of streaming every run into Python."""
    group_column = (
        "ar.agent_version_id" if group_by == "version" else "av.agent_id"
    )
    filter_clause = "AND av.agent_id = :agent_id" if agent_id_filter else ""
    params = {"agent_id": agent_id_filter} if agent_id_filter else {}
    rows = session.execute(
        text(
            f"""
            SELECT
                {group_column} AS group_key,
                AVG(EXTRACT(EPOCH FROM (ar.completed_at - ar.started_at)) * 1000)
                    AS avg_ms,
                percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (ar.completed_at - ar.started_at)) * 1000
                ) AS p95_ms
            FROM agent_runs ar
            JOIN agent_versions av ON av.id = ar.agent_version_id
            WHERE ar.started_at IS NOT NULL AND ar.completed_at IS NOT NULL
            {filter_clause}
            GROUP BY {group_column}
            """
        ),
        params,
    ).mappings()
    return {
        str(row["group_key"]): _LatencyStats(
            avg_ms=(
                round(float(row["avg_ms"]), 2)
                if row["avg_ms"] is not None
                else None
            ),
            p95_ms=float(row["p95_ms"]) if row["p95_ms"] is not None else None,
        )
        for row in rows
    }


def _latency_stats_in_memory(
    session: Session,
    *,
    group_by: Literal["version", "agent"],
    agent_id_filter: str | None,
) -> dict[str, _LatencyStats]:
    """Portable fallback for dialects without ``percentile_cont`` (SQLite in
    tests). Datetime arithmetic stays in Python, but the sample is bounded:
    p95 is based on the most recent ``_LATENCY_SAMPLE_LIMIT`` runs per version
    (ordered by created_at DESC), ranked with a window function so the
    dashboard never scans the full table."""
    recency_rank = (
        func.row_number()
        .over(
            partition_by=AgentRun.agent_version_id,
            order_by=AgentRun.created_at.desc(),
        )
        .label("recency_rank")
    )
    ranked = (
        select(
            AgentRun.agent_version_id,
            AgentVersion.agent_id,
            AgentRun.started_at,
            AgentRun.completed_at,
            recency_rank,
        )
        .join(AgentVersion, AgentVersion.id == AgentRun.agent_version_id)
        .where(
            AgentRun.started_at.is_not(None),
            AgentRun.completed_at.is_not(None),
        )
    )
    if agent_id_filter is not None:
        ranked = ranked.where(AgentVersion.agent_id == agent_id_filter)
    sample = ranked.subquery()
    group_column = (
        sample.c.agent_version_id if group_by == "version" else sample.c.agent_id
    )
    rows = session.execute(
        select(group_column, sample.c.started_at, sample.c.completed_at).where(
            sample.c.recency_rank <= _LATENCY_SAMPLE_LIMIT
        )
    ).all()

    latencies: dict[str, list[int]] = defaultdict(list)
    for group_key, started_at, completed_at in rows:
        delta_ms = int((completed_at - started_at).total_seconds() * 1000)
        latencies[group_key].append(delta_ms)
    return {
        group_key: _LatencyStats(
            avg_ms=round(sum(values) / len(values), 2),
            p95_ms=_p95_nearest_rank(values),
        )
        for group_key, values in latencies.items()
    }


def get_agent_version_dashboard(
    session: Session, *, agent_id: str | None = None
) -> list[AgentVersionObservability]:
    """Per-agent-version observability aggregates (PRD FR-19).

    When ``agent_id`` is None, every version that has runs is returned. When
    scoped to one agent, only that agent's versions are returned. An agent (or
    unknown agent id) with no runs yields an empty list — the dashboard is a
    run-driven aggregate, and absence of runs is a valid empty result.
    """
    rows = _version_aggregate_rows(session, agent_id_filter=agent_id)
    latency_by_version = _latency_stats(
        session, group_by="version", agent_id_filter=agent_id
    )
    return [
        _build_entry(
            agent_version_id=row.agent_version_id,
            agent_id=row.agent_id,
            agent_name=row.agent_name,
            semantic_version=row.semantic_version,
            model=row.model,
            total_runs=int(row.total_runs or 0),
            successful_runs=int(row.successful_runs or 0),
            total_cost=float(row.total_cost or 0.0),
            latency=latency_by_version.get(
                row.agent_version_id, _EMPTY_LATENCY_STATS
            ),
            last_run_at=row.last_run_at,
        )
        for row in rows
    ]


def get_agent_dashboard(session: Session) -> list[AgentObservabilitySummary]:
    """Per-agent rollup across all versions (PRD §10: per-agent summary).

    Collapses every version's runs into one summary row per agent. Agents with
    no runs are not included (the dashboard is a run-driven aggregate). The
    per-version breakdown is available via ``get_agent_version_dashboard``.
    """
    version_rows = _version_aggregate_rows(session, agent_id_filter=None)
    latency_by_agent = _latency_stats(
        session, group_by="agent", agent_id_filter=None
    )

    # Group per-version rows by agent_id, preserving the first-seen agent_name.
    agents: dict[str, dict[str, object]] = {}
    for row in version_rows:
        agent_bucket = agents.setdefault(
            row.agent_id,
            {
                "agent_id": row.agent_id,
                "agent_name": row.agent_name,
                "version_ids": set(),
                "total_runs": 0,
                "successful_runs": 0,
                "total_cost": 0.0,
                "last_run_at": None,
            },
        )
        agent_bucket["version_ids"].add(row.agent_version_id)
        agent_bucket["total_runs"] += int(row.total_runs or 0)
        agent_bucket["successful_runs"] += int(row.successful_runs or 0)
        agent_bucket["total_cost"] += float(row.total_cost or 0.0)
        row_last = row.last_run_at
        if row_last is not None:
            current_last = agent_bucket["last_run_at"]
            if current_last is None or row_last > current_last:
                agent_bucket["last_run_at"] = row_last

    summaries: list[AgentObservabilitySummary] = []
    for agent_data in agents.values():
        total_runs = int(agent_data["total_runs"])
        successful_runs = int(agent_data["successful_runs"])
        total_cost = float(agent_data["total_cost"])
        latency = latency_by_agent.get(
            agent_data["agent_id"], _EMPTY_LATENCY_STATS  # type: ignore[arg-type]
        )
        success_rate = round(successful_runs / total_runs, 4) if total_runs else 0.0
        avg_cost = round(total_cost / total_runs, 4) if total_runs else 0.0
        summaries.append(
            AgentObservabilitySummary(
                agent_id=agent_data["agent_id"],  # type: ignore[arg-type]
                agent_name=agent_data["agent_name"],  # type: ignore[arg-type]
                version_count=len(agent_data["version_ids"]),  # type: ignore[arg-type]
                total_runs=total_runs,
                successful_runs=successful_runs,
                success_rate=success_rate,
                avg_latency_ms=latency.avg_ms,
                p95_latency_ms=latency.p95_ms,
                avg_cost_estimate_usd=avg_cost,
                total_cost_estimate_usd=round(total_cost, 4),
                last_run_at=agent_data["last_run_at"],  # type: ignore[arg-type]
            )
        )
    summaries.sort(key=lambda s: (s.agent_name, s.agent_id))
    return summaries
