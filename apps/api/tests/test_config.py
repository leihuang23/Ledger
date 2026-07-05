from pathlib import Path

from app.core.config import Settings


def test_docker_compose_wires_app_env_for_api_demo_mode() -> None:
    compose = Path(__file__).resolve().parents[3].joinpath("docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert "APP_ENV: ${APP_ENV:-demo}" in compose
    assert "DEMO_OPERATOR_TOKEN: ${DEMO_OPERATOR_TOKEN:-}" in compose


def test_active_run_guard_migration_normalizes_dirty_active_rows() -> None:
    migration = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("alembic/versions/20260626_0007_add_active_agent_run_guard.py")
        .read_text(encoding="utf-8")
    )

    assert "ROW_NUMBER() OVER" in migration
    assert "status = 'failed'" in migration
    assert "uq_agent_runs_active_incident" in migration


def test_settings_parse_plain_cors_origin_env(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "http://localhost:3000")

    settings = Settings(_env_file=None)

    assert settings.backend_cors_origins == ["http://localhost:3000"]


def test_settings_parse_comma_separated_cors_origins_env(monkeypatch) -> None:
    monkeypatch.setenv(
        "BACKEND_CORS_ORIGINS",
        "http://localhost:3000, https://ops-agent.example.test",
    )

    settings = Settings(_env_file=None)

    assert settings.backend_cors_origins == [
        "http://localhost:3000",
        "https://ops-agent.example.test",
    ]
