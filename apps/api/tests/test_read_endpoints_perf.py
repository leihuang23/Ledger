"""NFR-1 automated floor: API p95 latency for read endpoints <= 300 ms (seeded data).

The dashboard aggregate has its own perf floor (``test_dashboard_api.py``);
this module covers the remaining NFR-1 read surfaces — ``GET /agents``,
``GET /tools``, and ``GET /runs/{run_id}`` — that previously had no automated
latency check. Like the dashboard floor, this runs in CI by default and catches
gross regressions (missing index, N+1, accidental full-table scans) at the
earliest signal. The 300 ms target is the PRD success-criteria budget for
seeded-data reads.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Generator

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

# NFR-1 success criteria: API read p95 <= 300 ms (seeded data).
READ_P95_BUDGET_MS = 300.0
# Enough samples for a meaningful nearest-rank p95 (ceil(0.95*20)=19th) without
# making the test slow.
SAMPLES = 20

SEEDED_INCIDENT_ID = "inc_rev_mrr_wow_drop_20260603"


@pytest.fixture()
def session_factory(
    tmp_path,
) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'read_perf_test.db'}",
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


def _seed(session_factory: Callable[[], Session]) -> str:
    """Seed the database and create one terminal run for the run-detail probe.

    Returns the run id so the caller can hit ``GET /runs/{run_id}``. The run is
    created directly (not via POST) so this test measures read latency only.
    """
    with session_factory() as session:
        reseed_database(session)
        now = utcnow_naive()
        run = AgentRun(
            id="run_read_perf_probe",
            incident_id=SEEDED_INCIDENT_ID,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="succeeded",
            trace_id="trace_read_perf_probe",
            trace_url="local://agent-runs/run_read_perf_probe",
            trace_provider="local",
            trace_metadata={},
            input_payload={},
            final_report=None,
            token_estimate=10,
            prompt_tokens=5,
            completion_tokens=5,
            cost_estimate_usd=0.001,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.commit()
        run_id = run.id
    return run_id


def _measure_p95_ms(client: TestClient, method: str, path: str) -> float:
    """Issue ``SAMPLES`` requests and return the nearest-rank p95 latency in ms."""
    latencies: list[float] = []
    for _ in range(SAMPLES):
        start = time.perf_counter()
        response = getattr(client, method)(path)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert response.status_code == 200, response.text
        latencies.append(elapsed_ms)
    return _p95_nearest_rank(latencies)  # type: ignore[arg-type]


def test_read_endpoints_p95_under_300ms_seeded(
    session_factory: Callable[[], Session],
) -> None:
    """NFR-1: ``GET /agents``, ``GET /tools``, and ``GET /runs/{id}`` must each
    respond with p95 latency <= 300 ms on seeded data.

    This is the automated floor for the API read-endpoint budget (the dashboard
    budget is covered separately). A regression here signals a missing index, an
    N+1 query, or an accidental full-table scan on a hot read path. Uses the same
    nearest-rank p95 as the dashboard aggregate so the semantics are consistent."""
    run_id = _seed(session_factory)
    client = _client_with_db(session_factory)

    probes = [
        ("GET /agents", "get", "/agents"),
        ("GET /tools", "get", "/tools"),
        (f"GET /runs/{run_id}", "get", f"/runs/{run_id}"),
    ]

    for label, method, path in probes:
        p95 = _measure_p95_ms(client, method, path)
        print(f"read_p95_ms[{label}]={p95:.1f}")
        assert p95 <= READ_P95_BUDGET_MS, (
            f"{label} p95 latency {p95:.1f}ms > {READ_P95_BUDGET_MS}ms budget"
        )
