from __future__ import annotations

from collections.abc import Callable, Generator

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

    def test_get_nonexistent_agent_returns_404(self, client: TestClient) -> None:
        response = client.get("/agents/nonexistent-agent")
        assert response.status_code == 404


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
        assert version["forked_from_version_id"] == "revenue-ops-agent_v1"

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
        assert len(version["system_prompt"]) > 0

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
            assert len(v1.system_prompt) > 0

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
