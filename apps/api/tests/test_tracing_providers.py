from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

from app.agent.tracing import start_agent_trace
from app.core.config import Settings


class FakeLangfuseObservation:
    trace_id = "0123456789abcdef0123456789abcdef"

    def __init__(self, client: "FakeLangfuseClient", name: str) -> None:
        self.client = client
        self.name = name
        self.updates: list[dict[str, Any]] = []

    def __enter__(self) -> "FakeLangfuseObservation":
        self.client.started.append(self.name)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.client.closed.append(self.name)

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)
        self.client.updates.append((self.name, kwargs))


class FakeLangfuseClient:
    instances: list["FakeLangfuseClient"] = []

    def __init__(
        self,
        *,
        public_key: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
        environment: str | None = None,
    ) -> None:
        self.public_key = public_key
        self.secret_key = secret_key
        self.base_url = base_url
        self.timeout = timeout
        self.environment = environment
        self.started: list[str] = []
        self.closed: list[str] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.flushed = False
        self.instances.append(self)

    def start_as_current_observation(self, *, name: str, **_: Any) -> FakeLangfuseObservation:
        return FakeLangfuseObservation(self, name)

    def get_current_trace_id(self) -> str:
        return FakeLangfuseObservation.trace_id

    def flush(self) -> None:
        self.flushed = True


def test_langfuse_provider_records_child_spans_and_trace_url(monkeypatch) -> None:
    FakeLangfuseClient.instances.clear()
    monkeypatch.setitem(
        sys.modules,
        "langfuse",
        SimpleNamespace(Langfuse=FakeLangfuseClient),
    )
    settings = Settings(
        observability_provider="langfuse",
        langfuse_public_key="pk_test",
        langfuse_secret_key="sk_test",
        langfuse_base_url="https://langfuse.example.test",
        langfuse_project_id="project_123",
    )

    trace = start_agent_trace(
        run_id="run_123",
        incident_id="incident_123",
        settings=settings,
    )
    result = trace.record_child(
        name="query_revenue_metrics",
        run_type="tool",
        inputs={"incident_id": "incident_123"},
        action=lambda: {"rows": 3},
    )
    trace.finish(outputs={"status": "succeeded"})

    assert result == {"rows": 3}
    assert trace.provider == "langfuse"
    assert trace.trace_id == FakeLangfuseObservation.trace_id
    assert trace.trace_url == (
        "https://langfuse.example.test/project/project_123/traces/"
        f"{FakeLangfuseObservation.trace_id}"
    )
    assert trace.metadata == {
        "base_url": "https://langfuse.example.test",
        "project_id": "project_123",
        "payload_mode": "metadata_only",
        "timeout_seconds": 2,
    }

    client = FakeLangfuseClient.instances[0]
    assert client.public_key == "pk_test"
    assert client.secret_key == "sk_test"
    assert client.timeout == 2
    assert client.started == ["Ops Agent Investigation", "query_revenue_metrics"]
    assert client.closed == ["query_revenue_metrics", "Ops Agent Investigation"]
    assert client.flushed is True


def test_hosted_traces_summarize_payloads_by_default(monkeypatch) -> None:
    FakeLangfuseClient.instances.clear()
    monkeypatch.setitem(
        sys.modules,
        "langfuse",
        SimpleNamespace(Langfuse=FakeLangfuseClient),
    )
    settings = Settings(
        observability_provider="langfuse",
        langfuse_public_key="pk_test",
        langfuse_secret_key="sk_test",
    )
    trace = start_agent_trace(
        run_id="run_private",
        incident_id="incident_private",
        settings=settings,
    )

    trace.record_child(
        name="fetch_support_tickets",
        run_type="tool",
        inputs={
            "incident_id": "incident_private",
            "tickets": [
                {
                    "account_name": "Synthetic Customer 01",
                    "description": "Payment method failed for owner@example.test",
                }
            ],
        },
        action=lambda: {
            "root_cause": "Expired payment methods were not refreshed before renewal.",
            "affected_accounts": [{"account_name": "Synthetic Customer 01"}],
        },
    )
    trace.finish(outputs={"status": "succeeded"})

    client = FakeLangfuseClient.instances[0]
    serialized_updates = repr(client.updates)
    assert "Synthetic Customer 01" not in serialized_updates
    assert "owner@example.test" not in serialized_updates
    child_input = next(
        payload["input"]
        for name, payload in client.updates
        if name == "fetch_support_tickets" and "input" in payload
    )
    assert child_input["payload_type"] == "dict"
    assert child_input["tickets_count"] == 1


class RaisingEnterObservation(FakeLangfuseObservation):
    def __enter__(self) -> "FakeLangfuseObservation":
        if self.name == "fetch_support_tickets":
            raise RuntimeError("context enter failed")
        return super().__enter__()


class RaisingChildLangfuseClient(FakeLangfuseClient):
    def start_as_current_observation(self, *, name: str, **_: Any) -> FakeLangfuseObservation:
        return RaisingEnterObservation(self, name)


def test_langfuse_child_context_failure_runs_action_once(monkeypatch) -> None:
    RaisingChildLangfuseClient.instances.clear()
    monkeypatch.setitem(
        sys.modules,
        "langfuse",
        SimpleNamespace(Langfuse=RaisingChildLangfuseClient),
    )
    settings = Settings(
        observability_provider="langfuse",
        langfuse_public_key="pk_test",
        langfuse_secret_key="sk_test",
    )
    trace = start_agent_trace(
        run_id="run_enter_failure",
        incident_id="incident_enter_failure",
        settings=settings,
    )
    calls = 0

    def action() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"rows": 1}

    result = trace.record_child(
        name="fetch_support_tickets",
        run_type="tool",
        inputs={"incident_id": "incident_enter_failure"},
        action=action,
    )

    assert result == {"rows": 1}
    assert calls == 1


def test_auto_provider_prefers_langfuse_when_credentials_exist(monkeypatch) -> None:
    FakeLangfuseClient.instances.clear()
    monkeypatch.setitem(
        sys.modules,
        "langfuse",
        SimpleNamespace(Langfuse=FakeLangfuseClient),
    )
    settings = Settings(
        observability_provider="auto",
        langfuse_public_key="pk_auto",
        langfuse_secret_key="sk_auto",
        langsmith_tracing=True,
        langsmith_api_key="lsv2_example",
    )

    trace = start_agent_trace(
        run_id="run_auto",
        incident_id="incident_auto",
        settings=settings,
    )
    trace.finish(outputs={"status": "succeeded"})

    assert trace.provider == "langfuse"
    assert trace.trace_url == f"langfuse://traces/{FakeLangfuseObservation.trace_id}"


def test_explicit_langfuse_provider_falls_back_to_local_without_credentials() -> None:
    settings = Settings(
        observability_provider="langfuse",
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )

    trace = start_agent_trace(
        run_id="run_456",
        incident_id="incident_456",
        settings=settings,
    )

    assert trace.provider == "local"
    assert trace.trace_url.startswith("local://agent-runs/run_456/traces/")
    assert trace.metadata["requested_provider"] == "langfuse"
    assert trace.metadata["reason"] == "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required"


class FakeLangSmithClient:
    instances: list["FakeLangSmithClient"] = []

    def __init__(
        self,
        api_url: str | None = None,
        *,
        api_key: str | None = None,
        web_url: str | None = None,
        timeout_ms: int | None = None,
        **_: Any,
    ) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.web_url = web_url
        self.timeout_ms = timeout_ms
        self.instances.append(self)


class FakeRunTree:
    instances: list["FakeRunTree"] = []

    def __init__(
        self,
        *,
        name: str,
        run_type: str,
        inputs: dict[str, Any],
        project_name: str,
        ls_client: FakeLangSmithClient | None = None,
        **_: Any,
    ) -> None:
        self.name = name
        self.run_type = run_type
        self.inputs = inputs
        self.project_name = project_name
        self.ls_client = ls_client
        self.id = "11111111-1111-1111-1111-111111111111"
        self.trace_id = "22222222-2222-2222-2222-222222222222"
        self.posted = False
        self.ended: dict[str, Any] | None = None
        self.patched = False
        self.instances.append(self)

    def post(self) -> None:
        self.posted = True

    def end(self, **kwargs: Any) -> None:
        self.ended = kwargs

    def patch(self, **_: Any) -> None:
        self.patched = True

    def create_child(self, **kwargs: Any) -> "FakeRunTree":
        return FakeRunTree(project_name=self.project_name, ls_client=self.ls_client, **kwargs)


def test_langsmith_provider_uses_explicit_client_settings(monkeypatch) -> None:
    FakeLangSmithClient.instances.clear()
    FakeRunTree.instances.clear()
    monkeypatch.setitem(
        sys.modules,
        "langsmith",
        SimpleNamespace(Client=FakeLangSmithClient),
    )
    monkeypatch.setitem(
        sys.modules,
        "langsmith.run_trees",
        SimpleNamespace(RunTree=FakeRunTree),
    )
    settings = Settings(
        observability_provider="langsmith",
        langsmith_api_key="lsv2_test",
        langsmith_endpoint="https://smith-api.example.test",
        langsmith_web_url="https://smith-web.example.test",
        langsmith_project="ops-agent-ci",
        observability_timeout_seconds=3,
    )

    trace = start_agent_trace(
        run_id="run_langsmith",
        incident_id="incident_langsmith",
        settings=settings,
    )
    trace.finish(outputs={"status": "succeeded", "affected_accounts": [{"name": "Hidden"}]})

    client = FakeLangSmithClient.instances[0]
    run_tree = FakeRunTree.instances[0]
    assert client.api_url == "https://smith-api.example.test"
    assert client.api_key == "lsv2_test"
    assert client.web_url == "https://smith-web.example.test"
    assert client.timeout_ms == 3000
    assert run_tree.ls_client is client
    assert run_tree.project_name == "ops-agent-ci"
    assert run_tree.inputs["payload_type"] == "dict"
    assert trace.provider == "langsmith"
    assert trace.metadata["endpoint"] == "https://smith-api.example.test"
    assert trace.metadata["payload_mode"] == "metadata_only"
