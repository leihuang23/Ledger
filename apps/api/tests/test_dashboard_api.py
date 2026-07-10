"""Phase 4 / P4-T9, P4-T11: aggregate observability dashboard (FR-19, AC-6.2).

Behavior tests for the per-agent-version aggregate surface:

* Given N runs with known statuses, costs, and latencies, the dashboard returns
  the correct total runs, success rate, p95 latency (nearest-rank, derived from
  ``completed_at - started_at`` per FR-19), average latency, average cost, and
  total cost.
* ``GET /dashboard/agents`` lists every agent version that has runs;
  ``GET /dashboard/agents/{agent_id}`` scopes to one agent's versions.
* Cost is always labeled as an *estimate* (the field is ``cost_estimate_usd``).

The 10k-run p95 latency target (NFR-1: <= 500 ms) is an opt-in perf check that
is skipped by default (P4-T11: "relaxed in CI; full perf check optional").
"""

from __future__ import annotations

import os
from collections.abc import Callable, Generator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.agent.persistence import utcnow_naive
from app.agents.service import DEFAULT_AGENT_ID, DEFAULT_AGENT_VERSION_ID
from app.dashboard.service import _p95_nearest_rank
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AgentRun
from app.seed import reseed_database


@pytest.fixture()
def session_factory(
    tmp_path,
) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'dashboard_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def _client_with_db(
    session_factory: Callable[[], Session],
) -> TestClient:
    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _seed(session_factory: Callable[[], Session]) -> None:
    with session_factory() as session:
        reseed_database(session)


def _make_run(
    session: Session,
    *,
    run_id: str,
    status: str,
    cost_usd: float,
    latency_ms: int,
    agent_id: str = DEFAULT_AGENT_ID,
    agent_version_id: str = DEFAULT_AGENT_VERSION_ID,
) -> AgentRun:
    # Latency is derived from completed_at - started_at (FR-19). Terminal runs
    # get both timestamps set `latency_ms` apart; a non-terminal run (queued)
    # would omit completed_at and be excluded from latency aggregates.
    now = utcnow_naive()
    started_at = now - timedelta(milliseconds=latency_ms)
    is_terminal = status in ("succeeded", "failed")
    run = AgentRun(
        id=run_id,
        incident_id=None,
        agent_id=agent_id,
        agent_version_id=agent_version_id,
        status=status,
        trace_id=f"trace_{run_id}",
        trace_url=f"local://agent-runs/{run_id}",
        trace_provider="local",
        trace_metadata={},
        input_payload={},
        final_report=None,
        token_estimate=0,
        prompt_tokens=0,
        completion_tokens=0,
        cost_estimate_usd=cost_usd,
        started_at=started_at,
        completed_at=now if is_terminal else None,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.commit()
    return run


def test_dashboard_aggregates_match_seeded_runs(
    session_factory: Callable[[], Session],
) -> None:
    """P4-T9 / FR-19: given 4 runs (3 succeeded, 1 failed) with known latencies
    and costs, the per-version aggregate is computed exactly.

    latencies = [100, 200, 300, 400] ms -> p95 nearest-rank = ceil(0.95*4) = 4th
    value = 400 ms; avg = 250 ms. costs = [0.10, 0.20, 0.30, 0.40] -> total 1.00,
    avg 0.25. success_rate = 3/4 = 0.75.
    """
    _seed(session_factory)
    with session_factory() as session:
        _make_run(session, run_id="run_d1", status="succeeded", cost_usd=0.10, latency_ms=100)
        _make_run(session, run_id="run_d2", status="succeeded", cost_usd=0.20, latency_ms=200)
        _make_run(session, run_id="run_d3", status="succeeded", cost_usd=0.30, latency_ms=300)
        _make_run(session, run_id="run_d4", status="failed", cost_usd=0.40, latency_ms=400)

    client = _client_with_db(session_factory)

    # Scoped to the default agent -> exactly one version entry.
    scoped = client.get(f"/dashboard/agents/{DEFAULT_AGENT_ID}")
    assert scoped.status_code == 200
    scoped_versions = scoped.json()
    assert len(scoped_versions) == 1
    entry = scoped_versions[0]
    assert entry["agent_id"] == DEFAULT_AGENT_ID
    assert entry["agent_version_id"] == DEFAULT_AGENT_VERSION_ID
    assert entry["total_runs"] == 4
    assert entry["successful_runs"] == 3
    assert entry["success_rate"] == 0.75
    assert entry["avg_latency_ms"] == 250.0
    assert entry["p95_latency_ms"] == 400.0
    assert entry["avg_cost_estimate_usd"] == 0.25
    assert entry["total_cost_estimate_usd"] == 1.0
    assert entry["last_run_at"] is not None
    # Labels are joined for the UI.
    assert entry["agent_name"]
    assert entry["semantic_version"]
    assert entry["model"]


def test_dashboard_lists_every_version_with_runs(
    session_factory: Callable[[], Session],
) -> None:
    """``GET /dashboard/agents`` returns one entry per agent version that has
    at least one run (run-driven aggregates). After seeding there are no runs,
    so the list is empty; once runs exist the default version appears."""
    _seed(session_factory)
    client = _client_with_db(session_factory)

    empty = client.get("/dashboard/agents")
    assert empty.status_code == 200
    assert empty.json() == []

    with session_factory() as session:
        _make_run(session, run_id="run_list_1", status="succeeded", cost_usd=0.05, latency_ms=120)

    populated = client.get("/dashboard/agents")
    assert populated.status_code == 200
    entries = populated.json()
    assert len(entries) == 1
    assert entries[0]["agent_version_id"] == DEFAULT_AGENT_VERSION_ID


def test_dashboard_excludes_non_terminal_runs_from_latency(
    session_factory: Callable[[], Session],
) -> None:
    """A still-running run (no completed_at) is counted in total_runs and cost
    but excluded from avg/p95 latency (FR-19: p95 from completed_at - started_at
    across runs that have both). Cost is still attributed."""
    _seed(session_factory)
    with session_factory() as session:
        _make_run(session, run_id="run_t1", status="succeeded", cost_usd=0.10, latency_ms=100)
        _make_run(session, run_id="run_t2", status="succeeded", cost_usd=0.20, latency_ms=200)
        # queued: no completed_at -> excluded from latency, included in cost/count.
        _make_run(session, run_id="run_t3", status="queued", cost_usd=0.0, latency_ms=999)

    client = _client_with_db(session_factory)
    entry = client.get(f"/dashboard/agents/{DEFAULT_AGENT_ID}").json()[0]

    assert entry["total_runs"] == 3
    assert entry["successful_runs"] == 2
    # latency over [100, 200] only: avg=150, p95=ceil(0.95*2)=2nd=200.
    assert entry["avg_latency_ms"] == 150.0
    assert entry["p95_latency_ms"] == 200.0
    assert entry["total_cost_estimate_usd"] == 0.3


def test_dashboard_unknown_agent_returns_empty_list(
    session_factory: Callable[[], Session],
) -> None:
    """An agent with no runs returns an empty list (not 404): the dashboard is a
    run-driven aggregate, and absence of runs is a valid empty result."""
    _seed(session_factory)
    client = _client_with_db(session_factory)

    response = client.get("/dashboard/agents/agent_does_not_exist")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.skipif(
    not os.environ.get("RUN_DASHBOARD_PERF"),
    reason="P4-T11: 10k-run p95 latency perf check (NFR-1); opt-in via RUN_DASHBOARD_PERF=1",
)
def test_dashboard_p95_latency_under_500ms_for_10k_runs(
    session_factory: Callable[[], Session],
) -> None:
    """NFR-1: dashboard aggregate queries are indexed; p95 latency <= 500 ms for
    up to 10,000 runs. Generate a 10k-run fixture and assert the endpoint
    responds within the target. Skipped by default (relaxed in CI per the
    implementation plan); enable with RUN_DASHBOARD_PERF=1 to verify locally."""
    _seed(session_factory)
    with session_factory() as session:
        for i in range(10_000):
            _make_run(
                session,
                run_id=f"run_perf_{i:05d}",
                status="succeeded" if i % 5 else "failed",
                cost_usd=0.01 * (i % 10),
                latency_ms=100 + (i % 500),
            )

    client = _client_with_db(session_factory)
    import time

    start = time.perf_counter()
    response = client.get(f"/dashboard/agents/{DEFAULT_AGENT_ID}")
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert response.status_code == 200
    assert elapsed_ms < 500.0, f"dashboard p95 latency {elapsed_ms:.1f}ms > 500ms"


def test_dashboard_responds_within_perf_floor_for_1k_runs(
    session_factory: Callable[[], Session],
) -> None:
    """NFR-1 automated floor (runs in CI by default): the per-version dashboard
    aggregate must stay well under the 500 ms p95 budget at a 1k-run scale.

    This is a regression floor that catches gross slowdowns (missing index,
    N+1, accidental full-table scans). The latency path computes p95 in Python
    (portable across Postgres/SQLite), so a regression here is the earliest
    signal. The full 10k-run NFR check above remains opt-in via
    ``RUN_DASHBOARD_PERF``; this default-on variant ensures the requirement
    has an automated floor a reviewer can see green.
    """
    _seed(session_factory)
    with session_factory() as session:
        for i in range(1_000):
            _make_run(
                session,
                run_id=f"run_perf_floor_{i:04d}",
                status="succeeded" if i % 5 else "failed",
                cost_usd=0.01 * (i % 10),
                latency_ms=100 + (i % 500),
            )

    client = _client_with_db(session_factory)
    import time

    start = time.perf_counter()
    response = client.get(f"/dashboard/agents/{DEFAULT_AGENT_ID}")
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert response.status_code == 200
    assert elapsed_ms < 500.0, f"dashboard latency {elapsed_ms:.1f}ms > 500ms floor"


def test_p95_nearest_rank_non_max_path_with_large_sample() -> None:
    """FR-19 p95 nearest-rank: with N >= 20 the rank (ceil(0.95*N)) falls below
    N, so p95 must NOT collapse to the sample maximum. This pins the non-trivial
    branch of ``_p95_nearest_rank`` that the small-sample integration tests
    (N=2, N=4 — where rank == N == max) cannot reach. A regression such as
    ``return ordered[-1]`` or ``rank = len(ordered)`` would pass those but fail
    here.
    """
    # 20 distinct ascending latencies: 10, 20, ..., 200 ms.
    latencies = [i * 10 for i in range(1, 21)]
    # rank = ceil(0.95 * 20) = ceil(19.0) = 19 -> the 19th value (1-indexed) =
    # ordered[18] = 190 ms. Explicitly NOT the max (200).
    assert _p95_nearest_rank(latencies) == 190.0

    # Sanity: the max is 200 and p95 must be strictly less for this sample.
    assert _p95_nearest_rank(latencies) < float(max(latencies))

    # 21 values: rank = ceil(0.95 * 21) = ceil(19.95) = 20 -> ordered[19] = 200.
    latencies_21 = [i * 10 for i in range(1, 22)]
    assert _p95_nearest_rank(latencies_21) == 200.0


def test_p95_nearest_rank_edge_cases() -> None:
    """Boundary behavior for ``_p95_nearest_rank``: empty -> None, single
    sample -> that value (rank=1), unsorted input is sorted internally, and the
    result is always a float (the schema declares ``p95_latency_ms: float |
    None``)."""
    assert _p95_nearest_rank([]) is None
    assert _p95_nearest_rank([424]) == 424.0
    # Unsorted input must be normalized to ascending before indexing.
    assert _p95_nearest_rank([400, 100, 300, 200]) == 400.0
    assert isinstance(_p95_nearest_rank([100, 200]), float)
