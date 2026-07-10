# Phase 6 Portfolio Sign-off

Status: comprehensive PR review and local remediation are complete. Pull-request CI is the remaining release gate.

This record maps the Project 2 success criteria to durable evidence. Test counts and CI links are updated only after fresh runs complete.

## Success criteria evidence

| Criterion | Evidence |
| --- | --- |
| S-1 — Revenue Ops Agent is registered and runnable | `apps/web/e2e/control-plane-run.spec.ts`; control-plane run API tests |
| S-2 — Tool permissions visible and enforced | tool policy tests, blocked-step tests, `/tools`, and published version UI |
| S-3 — High-risk actions are approval-gated | approval behavior tests and `apps/web/e2e/demo-flow.spec.ts` |
| S-4 — Versions can be compared | Phase 5 eval tests and `apps/web/e2e/phase5-studio.spec.ts` |
| S-5 — Dashboard shows status, trace, latency, and estimated cost | dashboard API tests, `/dashboard`, and portfolio screenshot |
| S-6 — Good version passes at least 4/5 | deterministic eval runner output recorded below |
| S-7 — Major claims cite evidence | investigation workflow tests plus three-run audit recorded below |
| S-8 — No run remains stuck after timeout | Celery timeout and run-lifecycle tests |
| S-9 — Project 1 surfaces remain functional | full backend, frontend, and Playwright suites recorded below |

## Verification record

Fresh local results from July 10, 2026:

- Backend: `353 passed, 2 skipped` with local Postgres and Redis available; the skips are environment/optional-path guards.
- Ruff: all API checks passed.
- Alembic migration coverage includes deterministic v1 sourcing, upgrade/downgrade/re-upgrade, referenced-snapshot fail-closed behavior, bootstrap repair, and a real isolated PostgreSQL `0009 -> HEAD -> bootstrap` pass.
- Deterministic eval CLI: Project 1 compatibility remains pinned to immutable v1 and passes at least `4/5`; the explicit Phase 6 comparison remains the portfolio baseline at `6/6` versus `5/6` for the intentionally document-search-disabled candidate.
- Frontend: `13` contract tests passed; TypeScript and the production Next.js build passed.
- Docker: Postgres, Redis, API, and web health checks passed; the Celery worker was running and processing eval jobs.
- Playwright: `8 passed, 1 skipped` in operator mode with one serial worker; the conditional public read-only test passed separately against `OPERATOR_UI_ENABLED=false`.
- Portfolio assets: both PNGs were inspected at `1440x900`; the WebM duration is `195.8` seconds.
- Performance: the 10,000-run dashboard aggregate completed in `43.2 ms`; 30-request live p95 samples were `/agents 2.8 ms`, `/dashboard/agents 3.3 ms`, `/runs 5.5 ms`, and `/tools 5.0 ms`, all below the `500 ms` target.
- Live safety smoke: anonymous gated eval execution returned `403`; the same request succeeded only after the local test token was supplied.
- Active-run audit: no run remained in `queued`, `running`, or `waiting_for_approval` after the repeatable browser suite.

## Three-run database audit

The following `revenue-ops-agent_phase6` runs were reconstructed with one read-only SQL query, without using application logs:

| Run | Scenario | Steps / tools | Claims / evidence | Actions / approvals |
| --- | --- | --- | --- | --- |
| `run_f5af975d8f27487f` | checkout retry regression | 8 ordered / 5 persisted outcomes | 5 all cited / 8 evidence records | 4 mocks / 2 pending approvals |
| `run_f70b3bda3bf443c5` | usage drop after import outage | 8 ordered / 5 persisted outcomes | 5 all cited / 8 evidence records | 4 mocks / 2 pending approvals |
| `run_da3c0b5dac4140e7` | ambiguous root cause | 8 ordered / 5 persisted outcomes | 6 all cited / 8 evidence records | 4 mocks / 2 pending approvals |

All three rows also proved a published immutable version reference, non-empty input payload, local trace id/URL, token estimate, and estimated cost field.

## Five-minute UAT

The reproducible route and narration are in `docs/demo-script.md`; the generated recording is `docs/assets/ops-agent-walkthrough.webm`. The inspected recording is `195.8` seconds and completes the problem → version/tools → run evidence → approval boundary → observability → eval regression route inside the five-minute limit.

## Final portfolio checklist

- [x] Two connected projects in one coherent repository narrative.
- [x] Recorded walkthrough generated from the current branch.
- [x] Six seeded incident/eval scenarios, including ambiguity.
- [x] Agent traces with local fallback.
- [x] Versioned eval results and regression flags.
- [x] Platform-wide approval queue.
- [x] Governed tool registry.
- [x] Cost and latency dashboard.
- [x] Architecture diagram.
- [x] OWASP LLM risk mapping.
- [x] README covers problem, architecture, demo, evals, limitations, security, and future work.
- [x] All local verification green; the GitHub workflow is the final PR gate.
