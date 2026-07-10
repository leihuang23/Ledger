from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_render_blueprint_wires_demo_safety_and_managed_dependencies() -> None:
    blueprint = ROOT.joinpath("render.yaml").read_text(encoding="utf-8")

    assert "type: web" in blueprint
    assert "type: worker" in blueprint
    assert "type: keyvalue" in blueprint
    assert "fromDatabase:" in blueprint
    assert "property: connectionString" in blueprint
    assert "APP_ENV" in blueprint and "value: demo" in blueprint
    assert "ALLOW_UNSAFE_BOOTSTRAP_SEED" in blueprint
    assert "DEMO_OPERATOR_TOKEN" in blueprint
    assert "EVAL_RUN_TOKEN" in blueprint
    assert "OBSERVABILITY_FULL_PAYLOADS" in blueprint
    assert "value: \"false\"" in blueprint
    assert "healthCheckPath: /ready" in blueprint


def test_api_container_honors_host_port_contract() -> None:
    entrypoint = ROOT.joinpath("apps/api/entrypoint.sh").read_text(encoding="utf-8")
    dockerfile = ROOT.joinpath("apps/api/Dockerfile").read_text(encoding="utf-8")

    assert 'PORT="${PORT:-8000}"' in entrypoint
    assert '--port "$PORT"' in entrypoint
    assert 'CMD curl -fsS "http://localhost:${PORT:-8000}/health"' in dockerfile
    assert "CMD-SHELL" not in dockerfile


def test_vercel_config_declares_nextjs_project_contract() -> None:
    config = ROOT.joinpath("apps/web/vercel.json").read_text(encoding="utf-8")
    next_config = ROOT.joinpath("apps/web/next.config.ts").read_text(encoding="utf-8")

    assert '"$schema": "https://openapi.vercel.sh/vercel.json"' in config
    assert '"framework": "nextjs"' in config
    assert "poweredByHeader: false" in next_config
    assert "Strict-Transport-Security" in next_config


def test_phase6_hiring_package_contains_required_artifacts() -> None:
    readme = ROOT.joinpath("README.md").read_text(encoding="utf-8")
    required_sections = (
        "## The Problem",
        "## Architecture",
        "## Five-Minute Demo",
        "## Eval Methodology",
        "## Security Model",
        "## Limitations",
        "## Future Work",
    )
    for heading in required_sections:
        assert heading in readme

    assert "final product will" not in readme.lower()
    assert "docs/assets/control-plane-dashboard.png" in readme
    assert "docs/assets/eval-regression.png" in readme

    for path in (
        "docs/demo-script.md",
        "docs/deployment.md",
        "docs/security.md",
        "docs/phase-6-signoff.md",
        "docs/assets/control-plane-dashboard.png",
        "docs/assets/eval-regression.png",
        "docs/assets/ops-agent-walkthrough.webm",
    ):
        assert ROOT.joinpath(path).is_file(), path
