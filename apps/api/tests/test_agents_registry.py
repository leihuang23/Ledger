from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Agent, AgentVersion
from app.seed import reseed_database
from app.agents.service import PHASE6_ENABLED_TOOL_IDS, get_default_published_version
from app.agent.persistence import utcnow_naive


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agents_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as session:
        reseed_database(session)

    yield TestingSessionLocal

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
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestAgentsList:
    def test_list_agents_returns_seeded_agent(self, client: TestClient) -> None:
        response = client.get("/agents")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 1
        ids = [a["id"] for a in body["agents"]]
        assert "revenue-ops-agent" in ids

    def test_list_agents_summary_has_required_fields(self, client: TestClient) -> None:
        response = client.get("/agents")
        body = response.json()
        agent = next(a for a in body["agents"] if a["id"] == "revenue-ops-agent")
        assert agent["name"] == "Revenue Ops Agent"
        assert agent["default_model"] == "gpt-4o-mini"
        assert "description" in agent
        assert "version_count" in agent
        assert "latest_published_version" in agent
        assert "current_draft_version" in agent
        assert "created_at" in agent
        assert "updated_at" in agent
        assert agent["version_count"] >= 1

    def test_list_agents_latest_published_version_treats_null_number_as_older(
        self,
        client: TestClient,
        session_factory: Callable[[], Session],
    ) -> None:
        with session_factory() as db_session:
            now = utcnow_naive()
            db_session.add_all(
                [
                    AgentVersion(
                        id="revenue-ops-agent_list_legacy_null",
                        agent_id="revenue-ops-agent",
                        version_number=None,
                        semantic_version=None,
                        status="published",
                        system_prompt="legacy null version",
                        model="gpt-4o-mini",
                        temperature=0.1,
                        max_tokens=1024,
                        enabled_tool_ids=["query_revenue_metrics"],
                        allowed_scopes=[],
                        published_at=now,
                        published_by="test",
                        forked_from_version_id="revenue-ops-agent_v1",
                        created_at=now,
                        updated_at=now,
                    ),
                    AgentVersion(
                        id="revenue-ops-agent_list_v3",
                        agent_id="revenue-ops-agent",
                        version_number=3,
                        semantic_version="3.0.0",
                        status="published",
                        system_prompt="v3",
                        model="gpt-4o-mini",
                        temperature=0.1,
                        max_tokens=1024,
                        enabled_tool_ids=["query_revenue_metrics"],
                        allowed_scopes=[],
                        published_at=now,
                        published_by="test",
                        forked_from_version_id="revenue-ops-agent_v1",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            db_session.commit()

        response = client.get("/agents")
        assert response.status_code == 200
        agent = next(a for a in response.json()["agents"] if a["id"] == "revenue-ops-agent")
        assert agent["latest_published_version"]["id"] == "revenue-ops-agent_list_v3"


class TestAgentDetail:
    def test_get_agent_detail_includes_versions(self, client: TestClient) -> None:
        response = client.get("/agents/revenue-ops-agent")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "revenue-ops-agent"
        assert "versions" in body
        assert len(body["versions"]) >= 1
        v1 = next(v for v in body["versions"] if v["version_number"] == 1)
        assert v1["status"] == "published"
        assert v1["semantic_version"] == "1.0.0"
        assert v1["model"] == "gpt-4o-mini"

    def test_agent_detail_orders_legacy_null_published_versions_last(
        self,
        client: TestClient,
        session_factory: Callable[[], Session],
    ) -> None:
        with session_factory() as db_session:
            now = utcnow_naive()
            db_session.add_all(
                [
                    AgentVersion(
                        id="revenue-ops-agent_detail_legacy_null",
                        agent_id="revenue-ops-agent",
                        version_number=None,
                        semantic_version=None,
                        status="published",
                        system_prompt="legacy null version",
                        model="gpt-4o-mini",
                        temperature=0.1,
                        max_tokens=1024,
                        enabled_tool_ids=["query_revenue_metrics"],
                        allowed_scopes=[],
                        published_at=now,
                        published_by="test",
                        forked_from_version_id="revenue-ops-agent_v1",
                        created_at=now,
                        updated_at=now,
                    ),
                    AgentVersion(
                        id="revenue-ops-agent_detail_v3",
                        agent_id="revenue-ops-agent",
                        version_number=3,
                        semantic_version="3.0.0",
                        status="published",
                        system_prompt="v3",
                        model="gpt-4o-mini",
                        temperature=0.1,
                        max_tokens=1024,
                        enabled_tool_ids=["query_revenue_metrics"],
                        allowed_scopes=[],
                        published_at=now,
                        published_by="test",
                        forked_from_version_id="revenue-ops-agent_v1",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            db_session.commit()

        response = client.get("/agents/revenue-ops-agent")
        assert response.status_code == 200
        body = response.json()
        published = [v for v in body["versions"] if v["status"] == "published"]
        assert body["latest_published_version"]["id"] == "revenue-ops-agent_detail_v3"
        assert published[0]["id"] == "revenue-ops-agent_detail_v3"
        assert published[-1]["id"] == "revenue-ops-agent_detail_legacy_null"

    def test_get_nonexistent_agent_returns_404(self, client: TestClient) -> None:
        response = client.get("/agents/nonexistent-agent")
        assert response.status_code == 404

    def test_detail_and_default_draft_use_stable_null_version_recency(
        self,
        client: TestClient,
        session_factory: Callable[[], Session],
    ) -> None:
        with session_factory() as db_session:
            now = utcnow_naive()
            agent = Agent(
                id="null-only-agent",
                name="Null Only Agent",
                description="Legacy null version ordering",
                default_model="gpt-4o-mini",
                created_at=now,
                updated_at=now,
            )
            older = AgentVersion(
                id="null-only-agent_old_null",
                agent_id=agent.id,
                version_number=None,
                semantic_version=None,
                status="published",
                system_prompt="older null source",
                model="gpt-4o-mini",
                temperature=0.1,
                max_tokens=1024,
                enabled_tool_ids=["query_revenue_metrics"],
                allowed_scopes=[],
                published_at=now,
                published_by="test",
                forked_from_version_id=None,
                created_at=now,
                updated_at=now,
            )
            newer = AgentVersion(
                id="null-only-agent_new_null",
                agent_id=agent.id,
                version_number=None,
                semantic_version=None,
                status="published",
                system_prompt="newer null source",
                model="claude-3-5-sonnet-latest",
                temperature=0.2,
                max_tokens=2048,
                enabled_tool_ids=["query_revenue_metrics", "search_docs"],
                allowed_scopes=[],
                published_at=now + timedelta(seconds=1),
                published_by="test",
                forked_from_version_id=None,
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            )
            db_session.add_all([agent, older, newer])
            db_session.commit()

        detail_resp = client.get("/agents/null-only-agent")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["latest_published_version"]["id"] == "null-only-agent_new_null"

        draft_resp = client.post("/agents/null-only-agent/versions", json={})
        assert draft_resp.status_code == 201
        draft = draft_resp.json()
        assert draft["forked_from_version_id"] == "null-only-agent_new_null"
        assert draft["model"] == "claude-3-5-sonnet-latest"
        assert draft["enabled_tool_ids"] == ["query_revenue_metrics", "search_docs"]


class TestCreateAgent:
    def test_create_agent_succeeds(self, client: TestClient) -> None:
        response = client.post(
            "/agents",
            json={
                "id": "new-agent",
                "name": "New Agent",
                "description": "A brand new agent",
                "default_model": "gpt-4o-mini",
                "system_prompt": "You are a helpful assistant.",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["id"] == "new-agent"
        assert body["name"] == "New Agent"
        assert body["default_model"] == "gpt-4o-mini"
        assert len(body["versions"]) == 1
        assert body["versions"][0]["status"] == "draft"

    def test_create_agent_duplicate_id_returns_409(self, client: TestClient) -> None:
        payload = {
            "id": "duplicate-agent",
            "name": "Duplicate Agent",
            "description": "First",
            "default_model": "gpt-4o-mini",
        }
        client.post("/agents", json=payload)
        response = client.post("/agents", json=payload)
        assert response.status_code == 409

    def test_create_agent_invalid_slug_returns_validation_error(self, client: TestClient) -> None:
        response = client.post(
            "/agents",
            json={
                "id": "Invalid Slug!",
                "name": "Bad Slug Agent",
                "description": "",
                "default_model": "gpt-4o-mini",
            },
        )
        assert response.status_code == 422

    def test_create_agent_invalid_default_model_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/agents",
            json={
                "id": "invalid-model-agent",
                "name": "Invalid Model Agent",
                "default_model": "gpt-100-turbo",
            },
        )
        assert response.status_code == 422


class TestAgentVersions:
    def test_list_versions_for_seeded_agent(self, client: TestClient) -> None:
        response = client.get("/agents/revenue-ops-agent/versions")
        assert response.status_code == 200
        body = response.json()
        assert "total" in body
        assert "versions" in body
        assert body["total"] >= 1
        versions = body["versions"]
        assert len(versions) >= 1
        v1 = next(v for v in versions if v["version_number"] == 1)
        assert v1["status"] == "published"
        assert "system_prompt" not in v1

    def test_list_versions_nonexistent_agent_returns_404(self, client: TestClient) -> None:
        response = client.get("/agents/no-such-agent/versions")
        assert response.status_code == 404

    def test_create_draft_version_forks_from_latest_published_by_default(
        self, client: TestClient
    ) -> None:
        version_resp = client.post(
            "/agents/revenue-ops-agent/versions",
            json={"system_prompt": "Custom draft prompt", "model": "gpt-4o-mini"},
        )
        assert version_resp.status_code == 201
        version = version_resp.json()
        assert version["status"] == "draft"
        assert version["version_number"] is None
        assert version["semantic_version"] is None
        assert version["model"] == "gpt-4o-mini"
        assert version["system_prompt"] == "Custom draft prompt"
        assert version["forked_from_version_id"] == "revenue-ops-agent_phase6"

    def test_create_draft_version_from_specific_version(self, client: TestClient) -> None:
        version_resp = client.post(
            "/agents/revenue-ops-agent/versions",
            json={"fork_from_version_id": "revenue-ops-agent_v1"},
        )
        assert version_resp.status_code == 201
        version = version_resp.json()
        assert version["status"] == "draft"
        assert version["forked_from_version_id"] == "revenue-ops-agent_v1"
        assert version["model"] == "gpt-4o-mini"
        assert isinstance(version["system_prompt"], str)

    def test_create_draft_for_new_agent_starts_blank(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={
                "id": "fresh-agent",
                "name": "Fresh Agent",
                "description": "",
                "default_model": "gpt-4o-mini",
            },
        )
        version_resp = client.post(
            "/agents/fresh-agent/versions",
            json={"system_prompt": "scratch prompt"},
        )
        assert version_resp.status_code == 201
        version = version_resp.json()
        assert version["status"] == "draft"
        assert version["forked_from_version_id"] is None
        assert version["system_prompt"] == "scratch prompt"

    def test_create_draft_for_new_agent_defaults_allowed_scopes_to_v1(
        self, client: TestClient
    ) -> None:
        """A new version created without a source to fork from defaults to the v1
        scopes so each built-in capability can be attached without silently
        failing policy. Token gates remain required for eval execution."""
        client.post(
            "/agents",
            json={
                "id": "scope-default-agent",
                "name": "Scope Default Agent",
                "description": "",
                "default_model": "gpt-4o-mini",
            },
        )
        version_resp = client.post(
            "/agents/scope-default-agent/versions",
            json={"system_prompt": "scratch prompt"},
        )
        assert version_resp.status_code == 201
        version = version_resp.json()
        assert version["forked_from_version_id"] is None
        assert version["allowed_scopes"] == [
            "read_data",
            "write_mock_action",
            "request_approval",
        ]

    def test_create_agent_initial_draft_defaults_allowed_scopes_to_v1(
        self, client: TestClient
    ) -> None:
        """The initial draft auto-created by POST /agents must default to the v1
        scopes, mirroring create_version/seed/migration 0012. Without this, an
        operator publishing that draft directly would block every data tool."""
        resp = client.post(
            "/agents",
            json={
                "id": "initial-draft-scopes-agent",
                "name": "Initial Draft Scopes Agent",
                "description": "",
                "default_model": "gpt-4o-mini",
            },
        )
        assert resp.status_code == 201
        # The agent summary's VersionSummary omits allowed_scopes; fetch the
        # version detail to assert the persisted scopes.
        detail = client.get(
            "/agents/initial-draft-scopes-agent/versions/initial-draft-scopes-agent_draft_v0"
        )
        assert detail.status_code == 200
        assert detail.json()["allowed_scopes"] == [
            "read_data",
            "write_mock_action",
            "request_approval",
        ]

    def test_get_version_detail(self, client: TestClient) -> None:
        response = client.get("/agents/revenue-ops-agent/versions/revenue-ops-agent_v1")
        assert response.status_code == 200
        version = response.json()
        assert version["id"] == "revenue-ops-agent_v1"
        assert version["status"] == "published"
        assert "system_prompt" in version
        assert "enabled_tool_ids" in version
        assert "allowed_scopes" in version

    def test_get_nonexistent_version_returns_404(self, client: TestClient) -> None:
        response = client.get("/agents/revenue-ops-agent/versions/nonexistent")
        assert response.status_code == 404


class TestVersionMutability:
    def test_update_draft_version_succeeds(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={
                "id": "mutable-agent",
                "name": "Mutable Agent",
                "description": "",
                "default_model": "gpt-4o-mini",
                "system_prompt": "initial",
            },
        )
        list_resp = client.get("/agents/mutable-agent/versions")
        version_id = list_resp.json()["versions"][0]["id"]

        update_resp = client.patch(
            f"/agents/mutable-agent/versions/{version_id}",
            json={"system_prompt": "updated prompt", "temperature": 0.5},
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["system_prompt"] == "updated prompt"
        assert updated["temperature"] == 0.5

    def test_update_published_version_returns_409(self, client: TestClient) -> None:
        response = client.patch(
            "/agents/revenue-ops-agent/versions/revenue-ops-agent_v1",
            json={"system_prompt": "trying to change published"},
        )
        assert response.status_code == 409


class TestPublishVersion:
    def test_publish_draft_assigns_version_number(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={
                "id": "publish-test-agent",
                "name": "Publish Test",
                "description": "",
                "default_model": "gpt-4o-mini",
            },
        )
        list_resp = client.get("/agents/publish-test-agent/versions")
        version_id = list_resp.json()["versions"][0]["id"]

        publish_resp = client.post(
            f"/agents/publish-test-agent/versions/{version_id}/publish",
        )
        assert publish_resp.status_code == 200
        published = publish_resp.json()["version"]
        assert published["status"] == "published"
        assert published["version_number"] == 1
        assert published["semantic_version"] == "1.0.0"
        assert published["published_at"] is not None
        assert published["published_by"] is not None

    def test_publish_already_published_returns_409(self, client: TestClient) -> None:
        response = client.post(
            "/agents/revenue-ops-agent/versions/revenue-ops-agent_v1/publish",
        )
        assert response.status_code == 409

    def test_publish_second_draft_gets_next_version_number(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={
                "id": "multi-version-agent",
                "name": "Multi Version",
                "description": "",
                "default_model": "gpt-4o-mini",
            },
        )
        list_resp = client.get("/agents/multi-version-agent/versions")
        v1_id = list_resp.json()["versions"][0]["id"]
        client.post(f"/agents/multi-version-agent/versions/{v1_id}/publish")

        v2_resp = client.post(
            "/agents/multi-version-agent/versions",
            json={"system_prompt": "v2 prompt"},
        )
        v2_id = v2_resp.json()["id"]
        publish_resp = client.post(
            f"/agents/multi-version-agent/versions/{v2_id}/publish",
        )
        assert publish_resp.status_code == 200
        published = publish_resp.json()["version"]
        assert published["version_number"] == 2
        assert published["semantic_version"] == "2.0.0"


class TestSeedIdempotency:
    def test_seed_creates_revenue_ops_agent(self, session_factory: Callable[[], Session]) -> None:
        with session_factory() as db_session:
            agent = db_session.get(Agent, "revenue-ops-agent")
            assert agent is not None
            assert agent.name == "Revenue Ops Agent"
            assert agent.default_model == "gpt-4o-mini"

    def test_seed_creates_published_v1(self, session_factory: Callable[[], Session]) -> None:
        with session_factory() as db_session:
            v1 = db_session.get(AgentVersion, "revenue-ops-agent_v1")
            assert v1 is not None
            assert v1.status == "published"
            assert v1.version_number == 1
            assert v1.semantic_version == "1.0.0"
            assert isinstance(v1.system_prompt, str)
            assert set(v1.enabled_tool_ids) == {
                "query_revenue_metrics",
                "fetch_account_details",
                "search_docs",
                "fetch_support_tickets",
            }
            assert set(v1.allowed_scopes) == {
                "read_data",
                "write_mock_action",
                "request_approval",
            }

    def test_seed_creates_published_phase6_snapshot(
        self, session_factory: Callable[[], Session]
    ) -> None:
        with session_factory() as db_session:
            phase6 = db_session.get(AgentVersion, "revenue-ops-agent_phase6")
            assert phase6 is not None
            assert phase6.status == "published"
            assert phase6.forked_from_version_id == "revenue-ops-agent_v1"
            assert set(phase6.enabled_tool_ids) == set(PHASE6_ENABLED_TOOL_IDS)
            assert set(phase6.allowed_scopes) == {
                "read_data",
                "write_mock_action",
                "request_approval",
                "run_eval",
            }

    def test_seed_creates_phase6_degraded_eval_candidate(
        self, session_factory: Callable[[], Session]
    ) -> None:
        with session_factory() as db_session:
            degraded = db_session.get(
                AgentVersion, "revenue-ops-agent_phase6_degraded"
            )
            assert degraded is not None
            assert degraded.status == "published"
            assert degraded.version_number < 0
            assert degraded.forked_from_version_id == "revenue-ops-agent_phase6"
            assert "search_docs" not in degraded.enabled_tool_ids
            assert "run_eval" in degraded.enabled_tool_ids
            assert "run_eval" in degraded.allowed_scopes

    def test_seed_is_idempotent(self, session_factory: Callable[[], Session]) -> None:
        from app.seed import _seed_control_plane_agent

        with session_factory() as db_session:
            before_count = db_session.scalar(
                select(func.count()).select_from(AgentVersion).where(
                    AgentVersion.agent_id == "revenue-ops-agent"
                )
            )
            _seed_control_plane_agent(db_session)
            db_session.commit()
            after_count = db_session.scalar(
                select(func.count()).select_from(AgentVersion).where(
                    AgentVersion.agent_id == "revenue-ops-agent"
                )
            )
            assert after_count == before_count

    def test_existing_database_seed_does_not_mutate_published_version_permissions(
        self, session_factory: Callable[[], Session]
    ) -> None:
        """Startup seeding must preserve an operator-narrowed published snapshot."""
        from app.seed import ensure_seeded_if_empty

        with session_factory() as db_session:
            version = db_session.get(AgentVersion, "revenue-ops-agent_v1")
            assert version is not None
            version.enabled_tool_ids = ["query_revenue_metrics"]
            version.allowed_scopes = ["read_data"]
            db_session.commit()
            snapshot = (
                list(version.enabled_tool_ids),
                list(version.allowed_scopes),
                version.updated_at,
            )

            assert ensure_seeded_if_empty(db_session) is None
            assert ensure_seeded_if_empty(db_session) is None
            db_session.refresh(version)

            assert (
                version.enabled_tool_ids,
                version.allowed_scopes,
                version.updated_at,
            ) == snapshot

    def test_existing_database_seed_does_not_restore_phase6_permissions(
        self, session_factory: Callable[[], Session]
    ) -> None:
        """Routine startup must not overwrite an operator-narrowed Phase 6 snapshot."""
        from app.seed import ensure_seeded_if_empty

        with session_factory() as db_session:
            version = db_session.get(AgentVersion, "revenue-ops-agent_phase6")
            assert version is not None
            version.enabled_tool_ids = ["query_revenue_metrics"]
            version.allowed_scopes = ["read_data"]
            db_session.commit()
            snapshot = (
                list(version.enabled_tool_ids),
                list(version.allowed_scopes),
                version.updated_at,
            )

            assert ensure_seeded_if_empty(db_session) is None
            db_session.refresh(version)

            assert (
                version.enabled_tool_ids,
                version.allowed_scopes,
                version.updated_at,
            ) == snapshot


class TestValidation:
    def test_invalid_tool_id_format_returns_422(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "validation-agent", "name": "Validation Agent"},
        )
        response = client.post(
            "/agents/validation-agent/versions",
            json={"enabled_tool_ids": ["Not Valid!", "bad id"]},
        )
        assert response.status_code == 422

    def test_invalid_scope_format_returns_422(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "scope-agent", "name": "Scope Agent"},
        )
        response = client.post(
            "/agents/scope-agent/versions",
            json={"allowed_scopes": ["UPPER_SCOPE", " spaces"]},
        )
        assert response.status_code == 422

    def test_temperature_out_of_range_returns_422(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "temp-agent", "name": "Temp Agent"},
        )
        response = client.post(
            "/agents/temp-agent/versions",
            json={"temperature": 3.0},
        )
        assert response.status_code == 422

    def test_max_tokens_out_of_range_returns_422(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "tokens-agent", "name": "Tokens Agent"},
        )
        response = client.post(
            "/agents/tokens-agent/versions",
            json={"max_tokens": 0},
        )
        assert response.status_code == 422

    def test_unknown_tool_id_returns_422(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "tool-agent", "name": "Tool Agent"},
        )
        response = client.post(
            "/agents/tool-agent/versions",
            json={"enabled_tool_ids": ["query_revenue_metrics", "not-a-real-tool"]},
        )
        assert response.status_code == 422

    def test_unknown_scope_value_returns_422(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "scope-value-agent", "name": "Scope Value Agent"},
        )
        response = client.post(
            "/agents/scope-value-agent/versions",
            json={
                "allowed_scopes": ["read_data", "definitely_not_a_real_scope"],
            },
        )
        assert response.status_code == 422

    def test_unsupported_model_returns_422(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "model-agent", "name": "Model Agent"},
        )
        response = client.post(
            "/agents/model-agent/versions",
            json={"model": "gpt-100-turbo"},
        )
        assert response.status_code == 422

    def test_current_anthropic_models_are_allowed(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "current-claude-agent", "name": "Current Claude Agent"},
        )
        response = client.post(
            "/agents/current-claude-agent/versions",
            json={"model": "claude-3-5-sonnet-latest"},
        )
        assert response.status_code == 201
        assert response.json()["model"] == "claude-3-5-sonnet-latest"

    def test_publish_revalidates_persisted_model(
        self, client: TestClient, session_factory: Callable[[], Session]
    ) -> None:
        client.post(
            "/agents",
            json={"id": "persisted-invalid-agent", "name": "Persisted Invalid Agent"},
        )
        list_resp = client.get("/agents/persisted-invalid-agent/versions")
        draft_id = list_resp.json()["versions"][0]["id"]
        with session_factory() as db_session:
            draft = db_session.get(AgentVersion, draft_id)
            assert draft is not None
            draft.model = "gpt-100-turbo"
            db_session.commit()

        response = client.post(
            f"/agents/persisted-invalid-agent/versions/{draft_id}/publish"
        )
        assert response.status_code == 422

    def test_publish_revalidates_persisted_scopes(
        self, client: TestClient, session_factory: Callable[[], Session]
    ) -> None:
        client.post(
            "/agents",
            json={"id": "persisted-invalid-scope-agent", "name": "Persisted Invalid Scope Agent"},
        )
        list_resp = client.get("/agents/persisted-invalid-scope-agent/versions")
        draft_id = list_resp.json()["versions"][0]["id"]
        with session_factory() as db_session:
            draft = db_session.get(AgentVersion, draft_id)
            assert draft is not None
            draft.allowed_scopes = ["read_data", "fabricated_scope"]
            db_session.commit()

        response = client.post(
            f"/agents/persisted-invalid-scope-agent/versions/{draft_id}/publish"
        )
        assert response.status_code == 422

    def test_publish_records_api_published_by(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "pubby-agent", "name": "Pubby Agent"},
        )
        list_resp = client.get("/agents/pubby-agent/versions")
        draft_id = list_resp.json()["versions"][0]["id"]
        pub_resp = client.post(f"/agents/pubby-agent/versions/{draft_id}/publish")
        assert pub_resp.status_code == 200
        detail_resp = client.get(f"/agents/pubby-agent/versions/{draft_id}")
        assert detail_resp.json()["published_by"] == "api"

    def test_list_pagination_respects_limit(self, client: TestClient) -> None:
        for i in range(5):
            client.post(
                "/agents",
                json={"id": f"pag-agent-{i}", "name": f"Pag Agent {i}"},
            )
        response = client.get("/agents?limit=2&offset=0")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 6
        assert len(body["agents"]) == 2


class TestVersionOrdering:
    def test_agent_detail_versions_list_published_first(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "order-agent", "name": "Order Agent"},
        )
        list_resp = client.get("/agents/order-agent/versions")
        draft_id = list_resp.json()["versions"][0]["id"]
        client.post(f"/agents/order-agent/versions/{draft_id}/publish")
        client.post("/agents/order-agent/versions", json={"system_prompt": "second draft"})

        detail_resp = client.get("/agents/order-agent")
        versions = detail_resp.json()["versions"]
        published = [v for v in versions if v["status"] == "published"]
        drafts = [v for v in versions if v["status"] == "draft"]
        assert len(published) == 1
        assert len(drafts) >= 1
        pub_index = versions.index(published[0])
        for d in drafts:
            assert versions.index(d) > pub_index

    def test_default_published_version_uses_null_last_stable_ordering(
        self,
        session_factory: Callable[[], Session],
    ) -> None:
        with session_factory() as db_session:
            now = utcnow_naive()
            db_session.add_all(
                [
                    AgentVersion(
                        id="revenue-ops-agent_legacy_null",
                        agent_id="revenue-ops-agent",
                        version_number=None,
                        semantic_version=None,
                        status="published",
                        system_prompt="legacy null version",
                        model="gpt-4o-mini",
                        temperature=0.1,
                        max_tokens=1024,
                        enabled_tool_ids=["query_revenue_metrics"],
                        allowed_scopes=[],
                        published_at=now,
                        published_by="test",
                        forked_from_version_id="revenue-ops-agent_v1",
                        created_at=now,
                        updated_at=now,
                    ),
                    AgentVersion(
                        id="revenue-ops-agent_v3_ordering",
                        agent_id="revenue-ops-agent",
                        version_number=3,
                        semantic_version="3.0.0",
                        status="published",
                        system_prompt="v3",
                        model="gpt-4o-mini",
                        temperature=0.1,
                        max_tokens=1024,
                        enabled_tool_ids=["query_revenue_metrics"],
                        allowed_scopes=[],
                        published_at=now,
                        published_by="test",
                        forked_from_version_id="revenue-ops-agent_v1",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            db_session.commit()

            default = get_default_published_version(db_session)

            assert default is not None
            assert default.id == "revenue-ops-agent_v3_ordering"


class TestCrossAgentSecurity:
    def test_fork_from_other_agent_version_returns_404(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "agent-a", "name": "Agent A"},
        )
        client.post(
            "/agents",
            json={"id": "agent-b", "name": "Agent B"},
        )

        response = client.post(
            "/agents/agent-b/versions",
            json={"fork_from_version_id": "agent-a_draft_v0"},
        )
        assert response.status_code == 404

    def test_get_version_with_wrong_agent_id_returns_404(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "wrong-agent-a", "name": "Wrong Agent A"},
        )
        client.post(
            "/agents",
            json={"id": "wrong-agent-b", "name": "Wrong Agent B"},
        )

        response = client.get("/agents/wrong-agent-b/versions/wrong-agent-a_draft_v0")
        assert response.status_code == 404

    def test_publish_version_with_wrong_agent_id_returns_404(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "pub-agent-a", "name": "Pub Agent A"},
        )
        client.post(
            "/agents",
            json={"id": "pub-agent-b", "name": "Pub Agent B"},
        )

        response = client.post("/agents/pub-agent-b/versions/pub-agent-a_draft_v0/publish")
        assert response.status_code == 404

    def test_update_version_with_wrong_agent_id_returns_404(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "upd-agent-a", "name": "Upd Agent A"},
        )
        client.post(
            "/agents",
            json={"id": "upd-agent-b", "name": "Upd Agent B"},
        )

        response = client.patch(
            "/agents/upd-agent-b/versions/upd-agent-a_draft_v0",
            json={"system_prompt": "hack attempt"},
        )
        assert response.status_code == 404


class TestVersionsPagination:
    def test_versions_pagination_respects_limit(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "vers-pag-agent", "name": "Vers Pag Agent"},
        )
        list_resp = client.get("/agents/vers-pag-agent/versions")
        draft_id = list_resp.json()["versions"][0]["id"]
        client.post(f"/agents/vers-pag-agent/versions/{draft_id}/publish")

        for i in range(3):
            client.post(
                "/agents/vers-pag-agent/versions",
                json={"system_prompt": f"draft {i}"},
            )

        response = client.get("/agents/vers-pag-agent/versions?limit=2&offset=0")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 4
        assert len(body["versions"]) == 2
        assert body["versions"][0]["version_number"] == 1
        assert body["versions"][1]["status"] == "draft"

    def test_versions_pagination_offset_skips_published(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "vers-pag-agent-2", "name": "Vers Pag Agent 2"},
        )
        list_resp = client.get("/agents/vers-pag-agent-2/versions")
        draft_id = list_resp.json()["versions"][0]["id"]
        client.post(f"/agents/vers-pag-agent-2/versions/{draft_id}/publish")

        for i in range(3):
            client.post(
                "/agents/vers-pag-agent-2/versions",
                json={"system_prompt": f"draft {i}"},
            )

        response = client.get("/agents/vers-pag-agent-2/versions?limit=2&offset=1")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 4
        assert len(body["versions"]) == 2
        assert body["versions"][0]["status"] == "draft"

    def test_versions_pagination_uses_stable_draft_tie_breaker(
        self,
        client: TestClient,
        session_factory: Callable[[], Session],
    ) -> None:
        client.post(
            "/agents",
            json={"id": "vers-pag-agent-3", "name": "Vers Pag Agent 3"},
        )
        created_ids: list[str] = []
        for i in range(3):
            response = client.post(
                "/agents/vers-pag-agent-3/versions",
                json={"system_prompt": f"draft {i}"},
            )
            assert response.status_code == 201
            created_ids.append(response.json()["id"])

        with session_factory() as db_session:
            first_created = db_session.get(AgentVersion, created_ids[0])
            assert first_created is not None
            same_timestamp = first_created.created_at
            drafts = db_session.scalars(
                select(AgentVersion).where(
                    AgentVersion.agent_id == "vers-pag-agent-3",
                    AgentVersion.status == "draft",
                )
            ).all()
            for draft in drafts:
                draft.created_at = same_timestamp
            db_session.commit()

        first_page = client.get("/agents/vers-pag-agent-3/versions?limit=2&offset=0")
        second_page = client.get("/agents/vers-pag-agent-3/versions?limit=2&offset=2")

        assert first_page.status_code == 200
        assert second_page.status_code == 200
        seen_ids = [
            version["id"]
            for version in first_page.json()["versions"] + second_page.json()["versions"]
        ]
        assert len(seen_ids) == len(set(seen_ids))
        assert seen_ids == sorted(seen_ids, reverse=True)

    def test_versions_pagination_uses_stable_published_tie_breaker(
        self,
        client: TestClient,
        session_factory: Callable[[], Session],
    ) -> None:
        client.post(
            "/agents",
            json={"id": "vers-pag-agent-4", "name": "Vers Pag Agent 4"},
        )
        published_ids: list[str] = []
        list_resp = client.get("/agents/vers-pag-agent-4/versions")
        initial_id = list_resp.json()["versions"][0]["id"]
        publish_resp = client.post(
            f"/agents/vers-pag-agent-4/versions/{initial_id}/publish"
        )
        assert publish_resp.status_code == 200
        published_ids.append(initial_id)
        for i in range(2):
            response = client.post(
                "/agents/vers-pag-agent-4/versions",
                json={"system_prompt": f"published {i}"},
            )
            assert response.status_code == 201
            version_id = response.json()["id"]
            publish_resp = client.post(
                f"/agents/vers-pag-agent-4/versions/{version_id}/publish"
            )
            assert publish_resp.status_code == 200
            published_ids.append(version_id)

        with session_factory() as db_session:
            first = db_session.get(AgentVersion, published_ids[0])
            assert first is not None
            same_timestamp = first.published_at
            for version_id in published_ids:
                version = db_session.get(AgentVersion, version_id)
                assert version is not None
                version.version_number = None
                version.published_at = same_timestamp
            db_session.commit()

        first_page = client.get("/agents/vers-pag-agent-4/versions?limit=2&offset=0")
        second_page = client.get("/agents/vers-pag-agent-4/versions?limit=2&offset=2")

        assert first_page.status_code == 200
        assert second_page.status_code == 200
        seen_ids = [
            version["id"]
            for version in first_page.json()["versions"] + second_page.json()["versions"]
            if version["status"] == "published"
        ]
        assert len(seen_ids) == len(set(seen_ids))
        assert seen_ids == sorted(seen_ids)


class TestEdgeCases:
    def test_create_version_nonexistent_agent_returns_404(self, client: TestClient) -> None:
        response = client.post("/agents/no-such-agent/versions", json={})
        assert response.status_code == 404

    def test_update_nonexistent_version_returns_404(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "edge-agent", "name": "Edge Agent"},
        )
        response = client.patch(
            "/agents/edge-agent/versions/nonexistent-version-id",
            json={"system_prompt": "test"},
        )
        assert response.status_code == 404

    def test_publish_nonexistent_version_returns_404(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "edge-pub-agent", "name": "Edge Pub Agent"},
        )
        response = client.post(
            "/agents/edge-pub-agent/versions/nonexistent-version-id/publish",
        )
        assert response.status_code == 404

    def test_update_version_validation_error_returns_422(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "val-patch-agent", "name": "Val Patch Agent"},
        )
        list_resp = client.get("/agents/val-patch-agent/versions")
        version_id = list_resp.json()["versions"][0]["id"]
        response = client.patch(
            f"/agents/val-patch-agent/versions/{version_id}",
            json={"temperature": 5.0},
        )
        assert response.status_code == 422

    def test_update_version_unknown_scope_returns_422(self, client: TestClient) -> None:
        client.post(
            "/agents",
            json={"id": "scope-patch-agent", "name": "Scope Patch Agent"},
        )
        list_resp = client.get("/agents/scope-patch-agent/versions")
        version_id = list_resp.json()["versions"][0]["id"]
        response = client.patch(
            f"/agents/scope-patch-agent/versions/{version_id}",
            json={"allowed_scopes": ["read_data", "invented_scope"]},
        )
        assert response.status_code == 422

    def test_agents_list_pagination_respects_offset(self, client: TestClient) -> None:
        for i in range(5):
            client.post(
                "/agents",
                json={"id": f"offset-agent-{i}", "name": f"Offset Agent {i}"},
            )
        response1 = client.get("/agents?limit=2&offset=0")
        response2 = client.get("/agents?limit=2&offset=2")
        assert response1.status_code == 200
        assert response2.status_code == 200
        body1 = response1.json()
        body2 = response2.json()
        ids1 = {a["id"] for a in body1["agents"]}
        ids2 = {a["id"] for a in body2["agents"]}
        assert len(ids1 & ids2) == 0
