from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_render_blueprint_wires_free_demo_safety_and_managed_dependencies() -> None:
    blueprint = ROOT.joinpath("render.yaml").read_text(encoding="utf-8")

    assert "type: web" in blueprint
    assert "type: keyvalue" in blueprint
    assert "type: worker" not in blueprint
    assert "plan: starter" not in blueprint
    assert "plan: basic-256mb" not in blueprint
    # The Blueprint manages only the API web service and Key Value: Postgres
    # moved to an external Supabase free project, so exactly two Free plans.
    assert blueprint.count("plan: free") == 2
    assert "databases:" not in blueprint
    assert "fromDatabase:" not in blueprint
    assert "ledger-postgres" not in blueprint
    assert "persistenceMode: off" in blueprint
    # DATABASE_URL is a manually-set server-only secret pointing at the
    # Supabase Supavisor session pool; it must not be Blueprint-synced.
    assert "- key: DATABASE_URL" in blueprint
    assert "sync: false" in blueprint
    assert "APP_ENV" in blueprint and "value: demo" in blueprint
    assert "ALLOW_UNSAFE_BOOTSTRAP_SEED" in blueprint
    assert "DEMO_OPERATOR_TOKEN" in blueprint
    assert "EVAL_RUN_TOKEN" in blueprint
    assert "OBSERVABILITY_FULL_PAYLOADS" in blueprint
    assert 'value: "false"' in blueprint
    assert "healthCheckPath: /ready" in blueprint


def test_free_render_tradeoffs_and_recovery_are_documented() -> None:
    deployment = ROOT.joinpath("docs/deployment.md").read_text(encoding="utf-8")

    assert "about one minute" in deployment
    assert "no background worker" in deployment
    # Postgres now lives on Supabase: the docs must explain the Supavisor
    # session-pool connection choice and the free-project pause/restore path.
    assert "Supavisor" in deployment
    assert "paused" in deployment
    assert "Restore a paused Supabase project" in deployment


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
        '"NEXT_PUBLIC_API_BASE_URL": "https://ledger-api-xvoe.onrender.com"'
        in worker_config
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
