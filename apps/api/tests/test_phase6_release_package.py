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
    assert 'value: "false"' in blueprint
    assert "healthCheckPath: /ready" in blueprint


def test_api_container_honors_host_port_contract() -> None:
    entrypoint = ROOT.joinpath("apps/api/entrypoint.sh").read_text(encoding="utf-8")
    dockerfile = ROOT.joinpath("apps/api/Dockerfile").read_text(encoding="utf-8")

    assert 'PORT="${PORT:-8000}"' in entrypoint
    assert '--port "$PORT"' in entrypoint
    assert 'CMD curl -fsS "http://localhost:${PORT:-8000}/health"' in dockerfile
    assert "CMD-SHELL" not in dockerfile


def test_cloudflare_worker_config_declares_public_demo_contract() -> None:
    worker_config = ROOT.joinpath("apps/web/wrangler.jsonc").read_text(encoding="utf-8")
    next_config = ROOT.joinpath("apps/web/next.config.ts").read_text(encoding="utf-8")

    assert '"main": ".open-next/worker.js"' in worker_config
    assert '"pattern": "ledger.leihuang.me"' in worker_config
    assert '"custom_domain": true' in worker_config
    assert '"OPERATOR_UI_ENABLED": "false"' in worker_config
    assert (
        '"NEXT_PUBLIC_API_BASE_URL": "https://ledger-api.onrender.com"' in worker_config
    )
    assert "poweredByHeader: false" in next_config
    assert "Strict-Transport-Security" in next_config
    assert "DEMO_OPERATOR_TOKEN" not in worker_config
    assert "EVAL_RUN_TOKEN" not in worker_config
    assert "DOCUMENT_INGEST_TOKEN" not in worker_config
    assert "STRIPE" not in worker_config

    # The active Vercel configuration was removed when the frontend moved to
    # Cloudflare Workers; the tracked config is apps/web/wrangler.jsonc.
    assert not ROOT.joinpath("apps/web/vercel.json").exists()


def test_phase6_release_package_contains_required_artifacts() -> None:
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
        "docs/assets/ledger-walkthrough.webm",
    ):
        assert ROOT.joinpath(path).is_file(), path
