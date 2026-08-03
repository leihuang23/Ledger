from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_public_demo_artifacts_and_checklist() -> None:
    """The public demo path is documented and scriptable without secrets."""
    deployment = ROOT.joinpath("docs/deployment.md").read_text(encoding="utf-8")
    assert "ledger.leihuang.me" in deployment
    assert "Captain checklist" in deployment
    assert "OPERATOR_UI_ENABLED" in deployment
    assert "BACKEND_CORS_ORIGINS" in deployment
    assert "verify-public-demo.sh" in deployment
    assert "APP_ENV=demo" in deployment or "`APP_ENV=demo`" in deployment

    env_example = ROOT.joinpath(".env.public-demo.example").read_text(encoding="utf-8")
    assert "APP_ENV=demo" in env_example
    assert "OPERATOR_UI_ENABLED=false" in env_example
    assert "DEMO_OPERATOR_TOKEN=" in env_example

    verify_script = ROOT.joinpath("scripts/verify-public-demo.sh")
    assert verify_script.is_file()
    script = verify_script.read_text(encoding="utf-8")
    assert "GET /health" in script or "/health" in script
    assert "/ready" in script
    assert "403" in script
    assert (
        "DEMO_OPERATOR_TOKEN" not in script
        or "never prints token" in script.lower()
    )

    e2e_workflow = ROOT.joinpath(".github/workflows/e2e.yml").read_text(
        encoding="utf-8"
    )
    assert "public-demo-readonly" in e2e_workflow
    assert ".env.public-demo.example" in e2e_workflow
    assert "test:e2e:public-demo" in e2e_workflow

    package_json = ROOT.joinpath("apps/web/package.json").read_text(encoding="utf-8")
    assert "test:e2e:public-demo" in package_json
    assert ROOT.joinpath("apps/web/e2e/public-demo-browse.spec.ts").is_file()
    assert ROOT.joinpath("apps/web/e2e/read-only-demo.spec.ts").is_file()
