# Ledger release readiness checklist

**Purpose:** Release acceptance criteria checklist plus a recorded green baseline. Checkboxes mark items that are true in the current product code and tests.

**Branch / contract alignment:** Stripe **test-mode** evidence adapter is in product code (`app/stripe_adapter/`, `POST /stripe/webhook`, `POST /stripe/reconcile`). `prd.md` and `AGENTS.md` define the boundary: test-mode only, normalize into existing account/subscription/invoice records, no Checkout/refunds/Connect/OAuth/live keys, disabled for `APP_ENV=demo`.

**Honest product positioning (use consistently):**

> Ledger is a production-shaped system that demonstrates how an AI agent can investigate revenue anomalies, cite evidence, expose an auditable run trace, and gate risky actions.

Disclose synthetic data, optional sandbox Stripe, read-only public demo, deterministic classifier as source of truth, and mock external actions behind approval.

---

## Day 1 green baseline

Captured from the isolated worktree on **2026-08-03** (UTC `2026-08-03T03:29:16Z` approximate). Do not treat this table as CI; it is a local snapshot.

| Command | Working directory | Result |
| --- | --- | --- |
| Investigation + core suites (see command below) | `apps/api` | **123 passed**, 16 warnings (Python 3.14 deprecations in slowapi) |
| Full API suite `python -m pytest -q` | `apps/api` | **399 passed, 2 skipped, 7 failed** |
| Web unit/contract `npm test` | `apps/web` | **20 passed** |
| Web lint/typecheck `npm run lint` (`next typegen && tsc --noEmit`) | `apps/web` | **passed** (exit 0) |
| Web production build `npm run build` | `apps/web` | **passed** (exit 0); Next.js 16.2.7 |
| Deterministic eval CLI `python -m app.evals.runner --json` | `apps/api` | **not runnable here** - needs Postgres with role/db `ledger` |
| Ruff | `apps/api` | **not installed** in the local `.venv` created for this baseline (`ruff` binary absent; no documented root `ruff` script in this tree) |

### Investigation + core suite command

```bash
cd apps/api
python -m venv .venv && source .venv/bin/activate   # if needed
pip install -e ".[dev]"
python -m pytest -q \
  tests/test_phase6_*package.py \
  tests/test_agent_investigations.py \
  tests/test_approvals_and_actions.py \
  tests/test_evals.py \
  tests/test_seed_and_metrics.py \
  tests/test_prd_api_contract.py \
  tests/test_anomalies_and_incidents.py \
  tests/test_blocked_tool_steps.py \
  tests/test_tool_policy.py
```

### Full-suite failures (environment, not claimed product regressions)

All seven failures failed while connecting to host `localhost:5432` with:

```text
FATAL: role "ledger" does not exist
```

A host Postgres process was already listening on port 5432 without the `ledger` role. Compose `postgres`/`redis` was started briefly; connections from the host still hit the non-Ledger Postgres. Failures:

1. `tests/test_celery_timeout.py::test_eval_suite_task_records_timeout_markers_for_unfinished_cases`
2. `tests/test_celery_timeout.py::test_eval_suite_task_timeout_preserves_completed_case_results`
3. `tests/test_celery_timeout.py::test_eval_suite_task_reraises_original_timeout_when_marker_write_fails`
4. `tests/test_error_envelope.py::test_domain_404_keeps_detail_contract` (500 instead of 404 because the app could not open Postgres)
5. `tests/test_health.py::test_ready_returns_200_with_dependency_status_when_healthy`
6. `tests/test_health.py::test_ready_returns_503_when_redis_is_unreachable`
7. `tests/test_health.py::test_ready_returns_503_when_redis_from_url_fails`

**Baseline interpretation:** investigation-related and core suites are green without external Postgres. Full suite and eval CLI need a Ledger-compatible Postgres (and Redis for readiness paths) - e.g. `docker compose up` with no conflicting host Postgres on 5432.

Prior Phase 6 local sign-off (historical, not re-run here) remains documented in `docs/phase-6-signoff.md` (including deterministic eval comparison notes). This Day 1 baseline does **not** invent new eval scores.

---

## Release acceptance criteria

Checkboxes mark **already true** from current product code, seed design, and this Day 1 baseline. Unchecked items remain for later release preparation.

### Ledger product

- [x] At least five seeded incident scenarios remain valid and internally consistent. (Seed + contract tests in core suite; seed includes 6 scenarios including ambiguity.)
- [ ] At least four of five required eval scenarios identify the intended root cause. (Not re-verified in this environment; eval CLI blocked on Postgres. Re-run `python -m app.evals.runner --json` against a Ledger DB before claiming.)
- [x] Every major report claim cites retrieved SQL, ticket, document, incident, or Stripe-derived evidence. (Product contract + investigation/eval tests; optional Stripe-derived path covered by `tests/test_stripe_adapter.py` citation case.)
- [x] Risky actions remain blocked until explicitly approved or rejected. (Approval suite green in core baseline.)
- [x] Every run exposes ordered steps, trace information, token/cost estimates, failures, and a final report. (Investigation / package tests green.)
- [x] Stripe sandbox events are authenticated, idempotent, reconcilable, and tested end to end. (`POST /stripe/webhook`, `POST /stripe/reconcile`, signature/idempotency/out-of-order/reconcile tests; live Test Clock path opt-in via `STRIPE_API_KEY` + `-m stripe_live`.)
- [x] No real customer data or live Stripe credentials are present. (Synthetic seed; no Stripe credentials in repo; live keys rejected at settings load; adapter disabled when `APP_ENV=demo`.)

### Public demo

- [ ] `ledger.leihuang.me` is reachable without authentication.
- [ ] Anonymous users can inspect the intended evidence and operational surfaces.
- [ ] Anonymous mutation attempts fail closed and do not alter state.
- [x] The core flow works without a paid LLM provider. (Default `LLM_PROVIDER=none` / local embeddings; investigation suite uses deterministic path.)
- [ ] Desktop and mobile browser smoke tests pass with no obvious visual defects.
- [ ] Security headers, CORS, readiness, and secret-exposure checks pass.

### Public package

- [ ] `leihuang.me` uses the approved positioning and project order.
- [ ] The Ledger case study supports both a 60-second scan and a technical deep dive.
- [ ] The README links to the demo, case study, and walkthrough.
- [x] A walkthrough recording and its narration script are tracked. (`docs/assets/ledger-walkthrough.webm`, ~195.8s; narration track in `docs/demo-script.md`.)
- [x] Synthetic data, sandbox integration, and known limitations are disclosed. (PRD/AGENTS and README carry the honest disclosure; case-study wording remains an open publish item below.)
- [x] No personal or sensitive contact information is exposed in this repository package. (No phone/street address published here as product assets.)
- [ ] All public claims match currently verified behavior.

### Day 1 contract work

- [x] `prd.md` permits narrow Stripe test-mode evidence adapter and states remaining out-of-scope Stripe surfaces.
- [x] `AGENTS.md` matches that boundary for future agents.
- [x] Evidence, eval, approval, and audit requirements preserved.
- [x] Baseline commands and results (or honest failures) recorded in this file.
- [x] Existing presentation assets inventoried (below).

---

## Presentation asset inventory (`docs/assets/`)

Existing media suitable for public presentation (no new captures in Day 1):

| Asset | Type | Notes |
| --- | --- | --- |
| `docs/assets/control-plane-dashboard.png` | PNG 1440x900 | Observability / control-plane dashboard screenshot used in README. |
| `docs/assets/eval-regression.png` | PNG 1440x900 | Eval Studio A-vs-B regression screenshot used in README. |
| `docs/assets/ledger-walkthrough.webm` | WebM ~12 MB | Narrated walkthrough; README links it. Phase 6 sign-off: ~195.8s duration. |

Regenerate the screenshots with the existing capture workflow when UI changes (see `docs/demo-script.md`).

---

## Explicitly not done in Day 1

- No Stripe code was in the Day 1 baseline; the test-mode evidence adapter (`app/stripe_adapter/`, webhooks, credentials, Test Clock scenario) is implemented on this branch and its PR is in review.
- No public deploy changes; demo safety topology (`APP_ENV=demo`, operator gates) unchanged.
- No invented eval pass rates from this environment.
- Additional planning notes may live outside this branch until tracked separately; this checklist is the Day 1 tracked artifact.
