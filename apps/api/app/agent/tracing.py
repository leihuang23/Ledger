from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

from app.core.config import Settings, get_settings

T = TypeVar("T")
SAFE_TRACE_SCALAR_KEYS = {
    "confidence",
    "incident_id",
    "root_cause",
    "run_id",
    "status",
    "trace_provider",
}
TRACE_COUNT_KEYS = {
    "accounts",
    "affected_accounts",
    "cited_evidence",
    "invoice_ids",
    "mock_actions",
    "results",
    "sql_evidence",
    "steps",
    "tickets",
}


class TraceProvider(StrEnum):
    AUTO = "auto"
    LOCAL = "local"
    LANGFUSE = "langfuse"
    LANGSMITH = "langsmith"


@dataclass
class AgentTraceHandle:
    provider: str
    trace_id: str
    trace_url: str
    metadata: dict[str, Any]
    _langsmith_run_tree: Any | None = field(default=None, repr=False)
    _langfuse_client: Any | None = field(default=None, repr=False)
    _langfuse_root_context: Any | None = field(default=None, repr=False)
    _langfuse_root_observation: Any | None = field(default=None, repr=False)
    _record_full_payloads: bool = field(default=False, repr=False)
    _finished: bool = field(default=False, repr=False)

    def record_child(
        self,
        *,
        name: str,
        run_type: str,
        inputs: object,
        action: Callable[[], T],
    ) -> T:
        if self._langsmith_run_tree is not None:
            return self._record_langsmith_child(
                name=name,
                run_type=run_type,
                inputs=inputs,
                action=action,
            )

        if self._langfuse_client is not None:
            return self._record_langfuse_child(
                name=name,
                run_type=run_type,
                inputs=inputs,
                action=action,
            )

        return action()

    def _record_langsmith_child(
        self,
        *,
        name: str,
        run_type: str,
        inputs: object,
        action: Callable[[], T],
    ) -> T:
        if self._langsmith_run_tree is None:
            return action()

        try:
            child = self._langsmith_run_tree.create_child(
                name=name,
                run_type=run_type,
                inputs=_trace_payload(self._record_full_payloads, inputs),
            )
            child.post()
        except Exception:
            return action()

        try:
            output = action()
        except Exception as exc:
            try:
                child.end(error=str(exc))
                child.patch()
            except Exception:
                pass
            raise

        try:
            child.end(outputs=_trace_payload(self._record_full_payloads, output))
            child.patch()
        except Exception:
            pass
        return output

    def _record_langfuse_child(
        self,
        *,
        name: str,
        run_type: str,
        inputs: object,
        action: Callable[[], T],
    ) -> T:
        if self._langfuse_client is None:
            return action()

        try:
            child_context = self._langfuse_client.start_as_current_observation(
                name=name,
            )
        except Exception:
            return action()

        try:
            child = child_context.__enter__()
        except Exception:
            return action()

        try:
            _safe_update_langfuse_observation(
                child,
                input=_trace_payload(self._record_full_payloads, inputs),
                metadata={"run_type": run_type},
            )
            try:
                output = action()
            except Exception as exc:
                _safe_update_langfuse_observation(
                    child,
                    output={"error": str(exc)},
                    metadata={"status": "failed"},
                )
                try:
                    child_context.__exit__(type(exc), exc, exc.__traceback__)
                except Exception:
                    pass
                raise

            _safe_update_langfuse_observation(
                child,
                output=_trace_payload(self._record_full_payloads, output),
                metadata={"status": "succeeded"},
            )
            try:
                child_context.__exit__(None, None, None)
            except Exception:
                pass
            return output
        except Exception:
            raise

    def finish(self, *, outputs: object | None = None, error: str | None = None) -> None:
        if self._finished:
            return
        self._finished = True

        if self._langsmith_run_tree is not None:
            self._finish_langsmith_trace(outputs=outputs, error=error)
            return

        if self._langfuse_client is not None:
            self._finish_langfuse_trace(outputs=outputs, error=error)
            return

    def _finish_langsmith_trace(
        self, *, outputs: object | None = None, error: str | None = None
    ) -> None:
        if self._langsmith_run_tree is None:
            return

        try:
            if error is not None:
                self._langsmith_run_tree.end(error=error)
            else:
                self._langsmith_run_tree.end(
                    outputs=_trace_payload(self._record_full_payloads, outputs or {})
                )
            self._langsmith_run_tree.patch()
        except Exception:
            return

    def _finish_langfuse_trace(
        self, *, outputs: object | None = None, error: str | None = None
    ) -> None:
        if self._langfuse_client is None:
            return

        if self._langfuse_root_observation is not None:
            if error is not None:
                _safe_update_langfuse_observation(
                    self._langfuse_root_observation,
                    output={"error": error},
                    metadata={"status": "failed"},
                )
            else:
                _safe_update_langfuse_observation(
                    self._langfuse_root_observation,
                    output=_trace_payload(self._record_full_payloads, outputs or {}),
                    metadata={"status": "succeeded"},
                )

        if self._langfuse_root_context is not None:
            try:
                self._langfuse_root_context.__exit__(None, None, None)
            except Exception:
                pass

        flush = getattr(self._langfuse_client, "flush", None)
        if callable(flush):
            try:
                flush()
            except Exception:
                pass


def start_agent_trace(
    *,
    run_id: str,
    incident_id: str,
    settings: Settings | None = None,
) -> AgentTraceHandle:
    resolved_settings = settings or get_settings()
    provider = TraceProvider(resolved_settings.observability_provider)

    if provider == TraceProvider.LANGFUSE or (
        provider == TraceProvider.AUTO and _has_langfuse_credentials(resolved_settings)
    ):
        handle = _start_langfuse_trace(
            run_id=run_id,
            incident_id=incident_id,
            settings=resolved_settings,
        )
        if handle is not None:
            return handle

    if provider == TraceProvider.LANGSMITH or (
        provider == TraceProvider.AUTO
        and resolved_settings.langsmith_tracing
        and resolved_settings.langsmith_api_key
    ):
        handle = _start_langsmith_trace(
            run_id=run_id,
            incident_id=incident_id,
            settings=resolved_settings,
        )
        if handle is not None:
            return handle

    return _local_trace(
        run_id=run_id,
        incident_id=incident_id,
        settings=resolved_settings,
        reason=_local_trace_reason(resolved_settings, provider),
        requested_provider=provider.value,
    )


def _start_langfuse_trace(
    *,
    run_id: str,
    incident_id: str,
    settings: Settings,
) -> AgentTraceHandle | None:
    if not _has_langfuse_credentials(settings):
        return _local_trace(
            run_id=run_id,
            incident_id=incident_id,
            settings=settings,
            reason="LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required",
            requested_provider=TraceProvider.LANGFUSE.value,
        )

    try:
        from langfuse import Langfuse
    except Exception as exc:
        return _local_trace(
            run_id=run_id,
            incident_id=incident_id,
            settings=settings,
            reason=f"langfuse SDK unavailable: {exc.__class__.__name__}",
            requested_provider=TraceProvider.LANGFUSE.value,
        )

    try:
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            timeout=settings.observability_timeout_seconds,
            environment=settings.app_env,
        )
        root_context = client.start_as_current_observation(
            name="Ops Agent Investigation",
        )
        root_observation = root_context.__enter__()
        _safe_update_langfuse_observation(
            root_observation,
            input=_trace_payload(
                settings.observability_full_payloads,
                {"run_id": run_id, "incident_id": incident_id},
            ),
            metadata={
                "run_id": run_id,
                "incident_id": incident_id,
                "app": settings.app_name,
                "environment": settings.app_env,
                "payload_mode": _trace_payload_mode(settings),
            },
        )
        trace_id = (
            getattr(root_observation, "trace_id", None)
            or _call_optional(client, "get_current_trace_id")
            or f"langfuse-{uuid4().hex[:16]}"
        )
    except Exception as exc:
        return _local_trace(
            run_id=run_id,
            incident_id=incident_id,
            settings=settings,
            reason=f"langfuse trace start failed: {exc}",
            requested_provider=TraceProvider.LANGFUSE.value,
        )

    return AgentTraceHandle(
        provider=TraceProvider.LANGFUSE.value,
        trace_id=str(trace_id),
        trace_url=_langfuse_trace_url(settings=settings, trace_id=str(trace_id)),
        metadata={
            "base_url": settings.langfuse_base_url.rstrip("/"),
            "project_id": settings.langfuse_project_id,
            "payload_mode": _trace_payload_mode(settings),
            "timeout_seconds": settings.observability_timeout_seconds,
        },
        _langfuse_client=client,
        _langfuse_root_context=root_context,
        _langfuse_root_observation=root_observation,
        _record_full_payloads=settings.observability_full_payloads,
    )


def _start_langsmith_trace(
    *,
    run_id: str,
    incident_id: str,
    settings: Settings,
) -> AgentTraceHandle | None:
    if not settings.langsmith_api_key:
        return _local_trace(
            run_id=run_id,
            incident_id=incident_id,
            settings=settings,
            reason="LANGSMITH_API_KEY is required",
            requested_provider=TraceProvider.LANGSMITH.value,
        )

    try:
        from langsmith import Client
        from langsmith.run_trees import RunTree
    except Exception as exc:
        return _local_trace(
            run_id=run_id,
            incident_id=incident_id,
            settings=settings,
            reason=f"langsmith SDK unavailable: {exc.__class__.__name__}",
            requested_provider=TraceProvider.LANGSMITH.value,
        )

    try:
        client = Client(
            api_url=settings.langsmith_endpoint,
            api_key=settings.langsmith_api_key,
            web_url=settings.langsmith_web_url,
            timeout_ms=settings.observability_timeout_seconds * 1000,
        )
        run_tree = RunTree(
            name="Ops Agent Investigation",
            run_type="chain",
            inputs=_trace_payload(
                settings.observability_full_payloads,
                {"run_id": run_id, "incident_id": incident_id},
            ),
            project_name=settings.langsmith_project,
            ls_client=client,
        )
        run_tree.post()
    except Exception as exc:
        return _local_trace(
            run_id=run_id,
            incident_id=incident_id,
            settings=settings,
            reason=f"langsmith trace start failed: {exc}",
            requested_provider=TraceProvider.LANGSMITH.value,
        )

    trace_id = str(getattr(run_tree, "trace_id", None) or getattr(run_tree, "id"))
    run_tree_id = str(getattr(run_tree, "id"))
    trace_url = _read_run_tree_url(run_tree) or _fallback_langsmith_url(
        settings=settings,
        trace_id=trace_id,
        run_tree_id=run_tree_id,
    )
    return AgentTraceHandle(
        provider=TraceProvider.LANGSMITH.value,
        trace_id=trace_id,
        trace_url=trace_url,
        metadata={
            "run_tree_id": run_tree_id,
            "project": settings.langsmith_project,
            "endpoint": settings.langsmith_endpoint,
            "payload_mode": _trace_payload_mode(settings),
            "timeout_seconds": settings.observability_timeout_seconds,
        },
        _langsmith_run_tree=run_tree,
        _record_full_payloads=settings.observability_full_payloads,
    )


def _read_run_tree_url(run_tree: object) -> str | None:
    for attr_name in ("url", "run_url", "web_url"):
        value = getattr(run_tree, attr_name, None)
        if isinstance(value, str) and value:
            return value

    get_url = getattr(run_tree, "get_url", None)
    if callable(get_url):
        try:
            value = get_url()
        except Exception:
            return None
        if isinstance(value, str) and value:
            return value

    return None


def _fallback_langsmith_url(
    *, settings: Settings, trace_id: str, run_tree_id: str
) -> str:
    base_url = settings.langsmith_web_url.rstrip("/")
    project = settings.langsmith_project.replace(" ", "%20")
    return f"{base_url}/projects/p/{project}/r/{run_tree_id}?trace_id={trace_id}"


def _local_trace(
    *,
    run_id: str,
    incident_id: str,
    settings: Settings,
    reason: str,
    requested_provider: str,
) -> AgentTraceHandle:
    trace_id = f"local-{uuid4().hex[:16]}"
    return AgentTraceHandle(
        provider=TraceProvider.LOCAL.value,
        trace_id=trace_id,
        trace_url=f"local://agent-runs/{run_id}/traces/{trace_id}",
        metadata={
            "incident_id": incident_id,
            "requested_provider": requested_provider,
            "reason": reason,
        },
    )


def _local_trace_reason(settings: Settings, provider: TraceProvider) -> str:
    if provider == TraceProvider.LOCAL:
        return "OBSERVABILITY_PROVIDER is local"
    if provider == TraceProvider.LANGFUSE:
        return "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required"
    if provider == TraceProvider.LANGSMITH:
        return "LANGSMITH_API_KEY is required"
    if not _has_langfuse_credentials(settings) and not (
        settings.langsmith_tracing and settings.langsmith_api_key
    ):
        return "no external observability provider is configured"
    return "external observability provider unavailable"


def _has_langfuse_credentials(settings: Settings) -> bool:
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


def _langfuse_trace_url(*, settings: Settings, trace_id: str) -> str:
    if settings.langfuse_project_id:
        base_url = settings.langfuse_base_url.rstrip("/")
        return f"{base_url}/project/{settings.langfuse_project_id}/traces/{trace_id}"
    return f"langfuse://traces/{trace_id}"


def _call_optional(target: object, method_name: str) -> object | None:
    method = getattr(target, method_name, None)
    if not callable(method):
        return None
    try:
        return method()
    except Exception:
        return None


def _safe_update_langfuse_observation(observation: object, **kwargs: Any) -> None:
    update = getattr(observation, "update", None)
    if not callable(update):
        return
    try:
        update(**kwargs)
    except Exception:
        return


def _trace_payload(record_full_payloads: bool, payload: object) -> dict[str, Any] | list[Any]:
    if record_full_payloads:
        return jsonable_encoder(payload)
    return _summarize_trace_payload(payload)


def _trace_payload_mode(settings: Settings) -> str:
    return "full" if settings.observability_full_payloads else "metadata_only"


def _summarize_trace_payload(payload: object) -> dict[str, Any]:
    try:
        encoded = jsonable_encoder(payload)
    except Exception as exc:
        return {
            "payload_type": type(payload).__name__,
            "encoding_error": exc.__class__.__name__,
        }

    if isinstance(encoded, dict):
        return _summarize_mapping(encoded)
    if isinstance(encoded, list):
        return _summarize_list(encoded)
    if isinstance(encoded, str):
        return {"payload_type": "str", "character_count": len(encoded)}
    if isinstance(encoded, (int, float, bool)) or encoded is None:
        return {"payload_type": type(encoded).__name__, "value": encoded}
    return {"payload_type": type(encoded).__name__}


def _summarize_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "payload_type": "dict",
        "keys": sorted(str(key) for key in payload)[:30],
    }
    for key in SAFE_TRACE_SCALAR_KEYS:
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value

    for key in TRACE_COUNT_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            summary[f"{key}_count"] = len(value)

    metric = payload.get("metric_evidence")
    if isinstance(metric, dict):
        summary["metric_name"] = metric.get("metric_name")
        summary["delta_percent"] = metric.get("delta_percent")
        summary["failed_invoice_count"] = metric.get("failed_invoice_count")

    cited_evidence = payload.get("cited_evidence")
    if isinstance(cited_evidence, list):
        summary["evidence_kinds"] = sorted(
            {
                str(item.get("kind"))
                for item in cited_evidence
                if isinstance(item, dict) and item.get("kind")
            }
        )

    action_statuses = payload.get("action_statuses")
    if isinstance(action_statuses, list):
        summary["action_statuses"] = [
            {
                "action_type": item.get("action_type"),
                "risk_level": item.get("risk_level"),
                "status": item.get("status"),
            }
            for item in action_statuses
            if isinstance(item, dict)
        ][:10]

    return summary


def _summarize_list(payload: list[Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "payload_type": "list",
        "item_count": len(payload),
    }
    if payload and isinstance(payload[0], dict):
        summary["first_item_keys"] = sorted(str(key) for key in payload[0])[:30]
    return summary
