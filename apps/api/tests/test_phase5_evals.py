from __future__ import annotations

from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from threading import Lock
from time import sleep

import pytest
from fastapi.testclient import TestClient
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.evals.runner import build_eval_run_summary, run_eval_suite
from app.evals.schemas import EvalDatasetCreate
from app.evals.service import create_eval_dataset
from app.models import AgentRun, EvalCase, EvalResult
from app.seed import reseed_database


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'phase5_evals.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as session:
        reseed_database(session)
    yield testing_session
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def client(
    session_factory: Callable[[], Session],
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    get_settings.cache_clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_create_list_and_get_eval_dataset(
    client: TestClient,
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        case_ids = list(
            session.scalars(select(EvalCase.id).order_by(EvalCase.id).limit(2))
        )

    response = client.post(
        "/eval-datasets",
        json={
            "name": "billing-smoke-suite",
            "description": "Two billing-focused regression cases.",
            "case_ids": case_ids,
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "billing-smoke-suite"
    assert [case["id"] for case in created["cases"]] == case_ids

    listed = client.get("/eval-datasets")
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert {item["name"] for item in listed.json()["datasets"]} == {
        "billing-smoke-suite",
        "mrr-drop-suite",
    }

    detail = client.get(f"/eval-datasets/{created['id']}")
    assert detail.status_code == 200
    assert detail.json() == created


def test_create_eval_dataset_rejects_whitespace_only_name(client: TestClient) -> None:
    response = client.post(
        "/eval-datasets",
        json={
            "name": "   ",
            "description": "Invalid blank name.",
            "case_ids": ["eval_checkout_retry_regression"],
        },
    )

    assert response.status_code == 422


def test_run_dataset_uses_selected_published_version_and_lists_filtered_results(
    client: TestClient,
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_with_test_session(
        eval_run_id: str, dataset_id: str, agent_version_id: str
    ) -> None:
        with session_factory() as session:
            run_eval_suite(
                session,
                eval_run_id=eval_run_id,
                dataset_id=dataset_id,
                agent_version_id=agent_version_id,
            )

    monkeypatch.setattr(
        "app.evals.studio_router._enqueue_eval_dataset",
        run_with_test_session,
        raising=False,
    )
    monkeypatch.setenv("EVAL_RUN_TOKEN", "phase5-eval-token")
    get_settings.cache_clear()

    response = client.post(
        "/eval-datasets/mrr-drop-suite/run",
        json={"agent_version_id": "revenue-ops-agent_phase6"},
        headers={"X-Eval-Run-Token": "phase5-eval-token"},
    )

    assert response.status_code == 202
    accepted = response.json()
    assert accepted["dataset_id"] == "mrr-drop-suite"
    assert accepted["agent_version_id"] == "revenue-ops-agent_phase6"
    assert accepted["status"] == "queued"

    results_response = client.get(
        "/eval-results",
        params={
            "agent_version_id": "revenue-ops-agent_phase6",
            "dataset_id": "mrr-drop-suite",
        },
    )
    assert results_response.status_code == 200
    body = results_response.json()
    assert body["total"] == 6
    assert {result["eval_run_id"] for result in body["results"]} == {
        accepted["eval_run_id"]
    }
    assert all(
        result["agent_version_id"] == "revenue-ops-agent_phase6"
        and result["dataset_id"] == "mrr-drop-suite"
        and result["cost_estimate_usd"] >= 0
        and result["trace_url"]
        for result in body["results"]
    )

    with session_factory() as session:
        persisted = session.scalars(
            select(EvalResult).where(
                EvalResult.eval_run_id == accepted["eval_run_id"]
            )
        ).all()
        assert len(persisted) == 6
        assert all(
            result.agent_version_id == "revenue-ops-agent_phase6"
            for result in persisted
        )
        assert all(result.dataset_id == "mrr-drop-suite" for result in persisted)
        assert all(
            result.cost_estimate_usd
            == session.get(AgentRun, result.agent_run_id).cost_estimate_usd
            for result in persisted
        )


def test_compare_latest_complete_version_runs_flags_regressions(
    client: TestClient,
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        good = run_eval_suite(
            session,
            eval_run_id="evalrun_good",
            dataset_id="mrr-drop-suite",
            agent_version_id="revenue-ops-agent_phase6",
        )
        degraded = run_eval_suite(
            session,
            eval_run_id="evalrun_degraded",
            dataset_id="mrr-drop-suite",
            agent_version_id="revenue-ops-agent_phase6_degraded",
        )

    assert good.passed_scenarios >= 4
    assert degraded.passed_scenarios < good.passed_scenarios

    response = client.get(
        "/eval-results/compare",
        params={
            "version_a": "revenue-ops-agent_phase6",
            "version_b": "revenue-ops-agent_phase6_degraded",
            "dataset_id": "mrr-drop-suite",
        },
    )

    assert response.status_code == 200
    comparison = response.json()
    assert comparison["run_a_id"] == "evalrun_good"
    assert comparison["run_b_id"] == "evalrun_degraded"
    assert comparison["pass_rate_a"] >= 4 / 6
    assert comparison["pass_rate_b"] < comparison["pass_rate_a"]
    assert comparison["pass_rate_delta"] < 0
    assert comparison["regressions"]
    assert all(item["change"] == "regression" for item in comparison["regressions"])
    assert {item["eval_case_id"] for item in comparison["cases"]} == {
        f"eval_{scenario}"
        for scenario in {
            "checkout_retry_regression",
            "enterprise_churn_wave",
            "payment_method_expiration",
            "support_backlog_export_bug",
            "unknown_root_cause",
            "usage_drop_after_import_outage",
        }
    }


def test_small_custom_dataset_uses_dataset_relative_completion_and_threshold(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        dataset = create_eval_dataset(
            session,
            EvalDatasetCreate(
                name="two-case-confidence-suite",
                case_ids=[
                    "eval_payment_method_expiration",
                    "eval_unknown_root_cause",
                ],
            ),
        )
        direct = run_eval_suite(
            session,
            eval_run_id="evalrun_two_case",
            dataset_id=dataset.id,
            agent_version_id="revenue-ops-agent_phase6",
        )
        rebuilt = build_eval_run_summary(session, direct.eval_run_id)

    assert direct.total_scenarios == 2
    assert direct.passed_scenarios == 2
    assert direct.status == "passed"
    assert rebuilt is not None
    assert rebuilt.status == "passed"
    assert rebuilt.total_scenarios == 2
    assert rebuilt.completed_at is not None


def test_eval_suite_task_serializes_parallel_suite_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.evals import tasks

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    state_lock = Lock()
    active = 0
    max_active = 0

    def fake_run_eval_suite(*_args, eval_run_id: str, **_kwargs):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        sleep(0.05)
        with state_lock:
            active -= 1
        return SimpleNamespace(
            eval_run_id=eval_run_id,
            status="passed",
            total_scenarios=1,
            passed_scenarios=1,
            failed_scenarios=0,
        )

    monkeypatch.setattr(tasks, "SessionLocal", DummySession)
    monkeypatch.setattr(tasks, "run_eval_suite", fake_run_eval_suite)
    monkeypatch.setattr(tasks, "_acquire_postgres_eval_lock", lambda: None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(tasks.run_eval_suite_task.run, f"evalrun_parallel_{index}")
            for index in range(2)
        ]
        for future in futures:
            assert future.result()["status"] == "passed"

    assert max_active == 1


def test_eval_suite_task_releases_database_lock_when_suite_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.evals import tasks

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    released = False

    def acquire_lock():
        def release() -> None:
            nonlocal released
            released = True

        return release

    def fail_suite(*_args, **_kwargs):
        raise RuntimeError("simulated suite failure")

    monkeypatch.setattr(tasks, "SessionLocal", DummySession)
    monkeypatch.setattr(tasks, "run_eval_suite", fail_suite)
    monkeypatch.setattr(tasks, "_acquire_postgres_eval_lock", acquire_lock)

    with pytest.raises(RuntimeError, match="simulated suite failure"):
        tasks.run_eval_suite_task.run("evalrun_release_on_failure")

    assert released is True


def test_eval_dataset_run_fails_closed_without_both_demo_tokens(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.evals.studio_router._enqueue_eval_dataset",
        lambda *_args: None,
    )
    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "operator-token")
    monkeypatch.delenv("EVAL_RUN_TOKEN", raising=False)
    get_settings.cache_clear()

    missing_eval = client.post(
        "/eval-datasets/mrr-drop-suite/run",
        json={"agent_version_id": "revenue-ops-agent_phase6"},
        headers={"X-Demo-Operator-Token": "operator-token"},
    )

    monkeypatch.setenv("EVAL_RUN_TOKEN", "eval-token")
    get_settings.cache_clear()
    missing_operator = client.post(
        "/eval-datasets/mrr-drop-suite/run",
        json={"agent_version_id": "revenue-ops-agent_phase6"},
        headers={"X-Eval-Run-Token": "eval-token"},
    )
    authorized = client.post(
        "/eval-datasets/mrr-drop-suite/run",
        json={"agent_version_id": "revenue-ops-agent_phase6"},
        headers={
            "X-Eval-Run-Token": "eval-token",
            "X-Demo-Operator-Token": "operator-token",
        },
    )

    assert missing_eval.status_code == 403
    assert missing_operator.status_code == 403
    assert authorized.status_code == 202


def test_eval_dataset_run_requires_version_run_eval_permission(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.evals.studio_router._enqueue_eval_dataset",
        lambda *_args: None,
    )
    monkeypatch.setenv("EVAL_RUN_TOKEN", "eval-token")
    get_settings.cache_clear()

    draft = client.post(
        "/agents/revenue-ops-agent/versions",
        json={
            "fork_from_version_id": "revenue-ops-agent_phase6",
            "enabled_tool_ids": ["run_eval"],
            "allowed_scopes": ["read_data"],
        },
    )
    assert draft.status_code == 201
    version_id = draft.json()["id"]
    assert client.post(
        f"/agents/revenue-ops-agent/versions/{version_id}/publish"
    ).status_code == 200

    response = client.post(
        "/eval-datasets/mrr-drop-suite/run",
        json={"agent_version_id": version_id},
        headers={"X-Eval-Run-Token": "eval-token"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "run_eval blocked: scope_not_allowed"
    blocked_run_id = response.headers["X-Agent-Run-Id"]
    blocked_run = client.get(f"/runs/{blocked_run_id}")
    assert blocked_run.status_code == 200
    run_payload = blocked_run.json()
    assert run_payload["status"] == "failed"
    assert run_payload["input_payload"]["operation"] == "run_eval"
    assert run_payload["input_payload"]["dataset_id"] == "mrr-drop-suite"
    assert run_payload["steps"][-1]["tool_name"] == "run_eval"
    assert run_payload["steps"][-1]["status"] == "blocked"
    assert run_payload["steps"][-1]["blocked_reason"] == "scope_not_allowed"


def test_phase5_migration_is_additive_and_reversible(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'phase5_migration.db'}")
    metadata = sa.MetaData()
    sa.Table(
        "agent_versions",
        metadata,
        sa.Column("id", sa.String(128), primary_key=True),
    )
    sa.Table(
        "eval_cases",
        metadata,
        sa.Column("id", sa.String(80), primary_key=True),
    )
    sa.Table(
        "agent_runs",
        metadata,
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("incident_id", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
    )
    sa.Table(
        "eval_results",
        metadata,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("eval_run_id", sa.String(80), nullable=False),
        sa.Column(
            "eval_case_id", sa.String(80), sa.ForeignKey("eval_cases.id"), nullable=False
        ),
        sa.Column(
            "agent_run_id", sa.String(48), sa.ForeignKey("agent_runs.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_agent_runs_active_incident "
                "ON agent_runs (incident_id) "
                "WHERE status IN ('queued', 'running')"
            )
        )

    migration_path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "20260710_0014_eval_studio.py"
    )
    spec = importlib.util.spec_from_file_location("phase5_eval_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "20260710_0014"
    assert migration.down_revision == "20260710_0013"

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

    inspector = inspect(engine)
    assert {"eval_datasets", "eval_dataset_cases"} <= set(inspector.get_table_names())
    result_columns = {column["name"] for column in inspector.get_columns("eval_results")}
    assert {"agent_version_id", "dataset_id", "cost_estimate_usd"} <= result_columns
    active_index = next(
        index
        for index in inspector.get_indexes("agent_runs")
        if index["name"] == "uq_agent_runs_active_incident"
    )
    assert "waiting_for_approval" in str(active_index["dialect_options"]["sqlite_where"])

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.downgrade()

    inspector = inspect(engine)
    assert "eval_datasets" not in inspector.get_table_names()
    result_columns = {column["name"] for column in inspector.get_columns("eval_results")}
    assert "agent_version_id" not in result_columns
    active_index = next(
        index
        for index in inspector.get_indexes("agent_runs")
        if index["name"] == "uq_agent_runs_active_incident"
    )
    assert "waiting_for_approval" not in str(
        active_index["dialect_options"]["sqlite_where"]
    )
    engine.dispose()
