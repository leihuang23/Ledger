# PRD-Alignment Review

**Date:** 2026-07-06
**Scope:** Full codebase audit of `ledger` (FastAPI `apps/api` + Next.js `apps/web`) against `prd.md` and `AGENTS.md`.
**Method:** Five parallel read-only audits (feature completeness, stability, performance, UX, production readiness), each citing `file:line` evidence. Findings consolidated below.

> **Post-audit update:** Several P0/P1 stability and observability gaps
> identified in this review have been fixed in the current codebase. They are
> marked with **(FIXED)** below; see the relevant code sections for the
> implementation. The remaining open items are operational hardening or
> out-of-scope production concerns.
>
> **Superseded by `prd-alignment-review-2026-07-07.md`.** A re-verification on
> 2026-07-07 confirmed the fixes below and corrected four entries that had gone
> stale: the composite-index and duplicate-metric-query items are now FIXED
> (migration `20260706_0009` + `metrics/service.py:144-146`), and the `/ready`
> failure-mode and real-route rate-limit test gaps are now CLOSED
> (`test_health.py:32-130`, `test_rate_limiting.py:74-125`). The 2026-07-07
> review also added a P1 eval-run liveness fix (`SoftTimeLimitExceeded`
> handling + a staleness self-heal in `build_eval_run_summary`). Read the newer
> review for the current state.

---

## 1. Executive Summary

The implementation is a **credible, production-shaped demo MVP** that meets every PRD success criterion. All 12 core API routes and all 14 data models exist and are wired. The investigation loop is real (fixed linear DAG → tools → evidence → report → approval-gated actions), not a stub. Citations are **structurally enforced** via Pydantic schemas, not merely prompted. Approval gating genuinely blocks risky actions. Every run records trace, steps, token/cost, and a final report. ~163 backend behavior/contract tests plus a Playwright E2E cover the core loop.

**Overall PRD compliance: COMPLIANT** on all five success criteria, with caveats noted below.

The codebase's strongest dimension is **evidence discipline and auditability**; its weakest is **operational hardening** (concurrency safety on approvals, cache/resource lifecycle, error handling on a few routes). None of the gaps violate the PRD's stated demo scope, but two — the **double-approval race** and the **eval-suite crash-on-single-failure** — directly undermine stated PRD success criteria and should be fixed before any reviewer run.

### Headline scores

| Dimension | Rating |
|---|---|
| Feature completeness vs PRD | **COMPLIANT** (2 partial notes) |
| Stability & reliability | **ADEQUATE** (4 weak spots) |
| Performance & efficiency | **ADEQUATE** (6 suboptimal spots, none critical at demo scale) |
| UX / frontend | **STRONG** operationally; **WEAK** on accessibility |
| Production readiness (demo scope) | **NEAR-READY** to **PRODUCTION-READY** |
| Production readiness (real SaaS) | **NOT-READY** (no real auth, no ops metrics) — out of PRD scope |

---

## 2. Feature Completeness

### PRD Success Criteria — all met

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | ≥5 seeded incident scenarios | **COMPLIANT** | [seed.py:51-147](file:///Users/leihuang/workspace/Ledger/apps/api/app/seed.py) defines 6 scenarios with confounders, including an ambiguity case; `test_evals.py:50` asserts count |
| 2 | Root cause for ≥4 of 5 evals | **COMPLIANT** (caveat) | [runner.py:47-94](file:///Users/leihuang/workspace/Ledger/apps/api/app/evals/runner.py) runs real investigations; `test_evals.py:69` asserts `>=4` |
| 3 | Final reports cite SQL/tickets/docs | **COMPLIANT** (structural) | [schemas.py:43-51](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/schemas.py); [workflow.py:405-455](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/workflow.py) |
| 4 | Risky actions blocked until approved | **COMPLIANT** | [approvals/service.py:23,62-129,245-322](file:///Users/leihuang/workspace/Ledger/apps/api/app/approvals/service.py) |
| 5 | Every run has trace/steps/tokens/cost/report | **COMPLIANT** | [models.py:243-296](file:///Users/leihuang/workspace/Ledger/apps/api/app/models.py); [tracing.py:239-278](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/tracing.py) |

### Caveats (not blockers, worth knowing)

1. **The "4 of 5" bar is reliable but deterministic.** [runner.py:238-243](file:///Users/leihuang/workspace/Ledger/apps/api/app/evals/runner.py) scores root cause by exact normalized-string equality. The actual root cause comes from a deterministic keyword classifier ([workflow.py:627-729](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/workflow.py)); the LLM path is optional and falls back to the deterministic classifier when disabled or unsupported. This is consistent with AGENTS.md ("LLM should synthesize, not invent evidence") but means the eval does **not** exercise LLM intelligence by default. A reviewer who configures `OPENAI_API_KEY` and runs the suite will still see passes driven by the deterministic path unless they also weaken the fallback.

2. **Seeded "unknown-root-cause" eval scenario is present. (FIXED)** AGENTS.md asks for negative/ambiguous cases. The agent supports ambiguity: `_diagnose_from_evidence` returns an `is_specific=False` diagnosis ([workflow.py:718-729](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/workflow.py)) and `_report_claims` emits an `uncertainty` claim ([workflow.py:379-389](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/workflow.py)). The `unknown_root_cause` scenario is now seeded as the 6th `EvalCase` ([seed.py:127-146](file:///Users/leihuang/workspace/Ledger/apps/api/app/seed.py)) and is asserted to pass in `test_evals.py:199-221`.

### Other completeness checks

- **Confounders in seed data — COMPLIANT with AGENTS.md:** noisy tickets (4 per account, only some tagged), partial outages (`event_number % 4 == 0`), invoice timing (backdated May 29 invoice), plan changes (enterprise/scale/team), churn (canceled subs), failed payments with recovered reasons (`(account_number + month_index) % 23 == 0`), usage shifts.
- **All 12 API routes exist** and are registered in [main.py:60-69](file:///Users/leihuang/workspace/Ledger/apps/api/app/main.py).
- **All 14 data models exist** in [models.py](file:///Users/leihuang/workspace/Ledger/apps/api/app/models.py) plus a bonus `ActionAuditEvent` for audit trails.
- **RAG with pgvector — COMPLIANT, not stubbed:** [search.py:94-113](file:///Users/leihuang/workspace/Ledger/apps/api/app/knowledge/search.py) uses real `1 - (embedding <=> vector)` cosine distance; HNSW index created in migration `20260612_0003:132-138`. Default embeddings are a local SHA-256 hashing projection (semantically weak but deterministic and free); real OpenAI embeddings are wired ([embeddings.py:120-152](file:///Users/leihuang/workspace/Ledger/apps/api/app/knowledge/embeddings.py)) when configured.
- **Metrics are deterministic — COMPLIANT:** pure SQL aggregations in [metrics/service.py](file:///Users/leihuang/workspace/Ledger/apps/api/app/metrics/service.py); no LLM anywhere in the metrics path.
- **Tool boundaries explicit — COMPLIANT:** 4 read-only tools (SQL/account/docs/tickets) in [tools.py](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/tools.py); actions are proposed post-report structurally, never called by the LLM. No real external writes anywhere.

---

## 3. Stability & Reliability

| Sub-area | Rating | Key evidence |
|---|---|---|
| LLM failure handling | ADEQUATE | [workflow.py:560-568](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/workflow.py) catches all exceptions, falls back to deterministic |
| Malformed LLM output rejection | STRONG | [client.py:177-189](file:///Users/leihuang/workspace/Ledger/apps/api/app/llm/client.py) raises on bad JSON |
| Tool failures in run history | STRONG | [persistence.py:50-55,111-120](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/persistence.py) records failed steps |
| Global exception handling | **FIXED — ADEQUATE** | [main.py:63-82](file:///Users/leihuang/workspace/Ledger/apps/api/app/main.py) handles unhandled exceptions and returns a structured `{error:{code,message,request_id}}` envelope via [core/errors.py](file:///Users/leihuang/workspace/Ledger/apps/api/app/core/errors.py) |
| Route error → status mapping | ADEQUATE | `knowledge/router.py` uses `error_response` and `evals/router.py` raises `HTTPException`; most routes map known failures to the right status |
| Eval suite resilience | **FIXED — STRONG** | [runner.py:59-98](file:///Users/leihuang/workspace/Ledger/apps/api/app/evals/runner.py) wraps each case in try/except, persists a failed `EvalResult`, and continues |
| Correlation IDs in logs | **FIXED — STRONG** | [logging_config.py:17-26](file:///Users/leihuang/workspace/Ledger/apps/api/app/logging_config.py) adds `RequestIdFilter` to the root handler; JSON logs include `request_id` |
| Redis lifecycle | **FIXED — ADEQUATE** | [cache.py:22-56](file:///Users/leihuang/workspace/Ledger/apps/api/app/cache.py) uses a module-level singleton with lazy initialization, health-check reconnection, and an in-memory fallback for Redis outages |
| Celery task timeouts | **FIXED — ADEQUATE** | [celery_app.py:24-27](file:///Users/leihuang/workspace/Ledger/apps/api/app/celery_app.py) sets `task_time_limit=600` and `task_soft_time_limit=540`; dead `max_retries` removed from task decorators |
| DB session scoping | ADEQUATE | `pool_pre_ping=True`; `with SessionLocal()` everywhere; no `pool_recycle` |
| Concurrent-run guard | STRONG | partial unique index (`migration 0007`) + atomic claim ([service.py:164-187](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/service.py)) |
| **Double-approval race** | **FIXED — STRONG** | [approvals/service.py:253-272,314-333](file:///Users/leihuang/workspace/Ledger/apps/api/app/approvals/service.py) uses conditional `UPDATE … WHERE status='pending'` with rowcount check; concurrent requests get `409 Conflict` |

### Highest-impact reliability gaps

The five highest-impact gaps identified during the audit have been fixed:

1. **Double-approval race — FIXED.** `approve_request`/`reject_request` now use a conditional `UPDATE … WHERE status='pending'` with a rowcount check ([approvals/service.py:253-272,314-333](file:///Users/leihuang/workspace/Ledger/apps/api/app/approvals/service.py)). Concurrent requests receive `409 Conflict` and the action state stays consistent with the approval record.

2. **Eval suite crashes on any single case failure — FIXED.** [runner.py:59-98](file:///Users/leihuang/workspace/Ledger/apps/api/app/evals/runner.py) wraps each case in try/except, persists a failed `EvalResult` with a failed `AgentRun`, commits incrementally, and continues to the next case.

3. **`Cache()` per-call ping — FIXED.** [cache.py:22-56](file:///Users/leihuang/workspace/Ledger/apps/api/app/cache.py) uses a module-level singleton with lazy initialization, periodic health checks, and an in-memory fallback on Redis outage.

4. **Request-ID not wired to logs — FIXED.** [logging_config.py:17-26](file:///Users/leihuang/workspace/Ledger/apps/api/app/logging_config.py) adds a `RequestIdFilter` to the root handler so JSON logs automatically include `request_id`.

5. **No Celery task time limit — FIXED.** [celery_app.py:24-27](file:///Users/leihuang/workspace/Ledger/apps/api/app/celery_app.py) configures `task_time_limit=600` and `task_soft_time_limit=540`.

---

## 4. Performance & Efficiency

Indexing and SQL aggregation discipline are **solid**; pgvector HNSW is correctly applied; token/cost estimation is cheap (local tiktoken + dict lookup). The workflow is a fixed linear DAG (one LLM call per run), so runaway cost is impossible by construction. Main risks are operational.

| # | Issue | Rating | Location |
|---|---|---|---|
| 1 | `Cache()` per-call `ping()`, no singleton, no stampede protection | **FIXED** — module-level singleton with fallback | [cache.py:22-56](file:///Users/leihuang/workspace/Ledger/apps/api/app/cache.py) |
| 2 | No Celery `task_time_limit`/concurrency config | **FIXED** — 600s hard / 540s soft limits | [celery_app.py:24-27](file:///Users/leihuang/workspace/Ledger/apps/api/app/celery_app.py) |
| 3 | Eval suite runs synchronously inside the HTTP request | **FIXED** — `POST /evals/run` returns 202 and enqueues `run_eval_suite_task` via Celery | [evals/router.py:48-82](file:///Users/leihuang/workspace/Ledger/apps/api/app/evals/router.py); [evals/tasks.py](file:///Users/leihuang/workspace/Ledger/apps/api/app/evals/tasks.py) |
| 4 | Duplicate `unresolved_count` query identical to `failed_count` | **FIXED** — duplicate removed, field retained for API compatibility | [apps/api/app/metrics/service.py](../apps/api/app/metrics/service.py):144-146 |
| 5 | Multiple scalar queries foldable into one conditional aggregation | SUBOPTIMAL | [metrics/service.py:33-57,76-101,234-265](file:///Users/leihuang/workspace/Ledger/apps/api/app/metrics/service.py) |
| 6 | `list_incidents` loads all incidents, no pagination | **FIXED** — `GET /incidents` supports `limit`/`offset` | [incidents/router.py:23-30](file:///Users/leihuang/workspace/Ledger/apps/api/app/incidents/router.py) |
| 7 | Missing composite `(status, invoice_date)` / `(status, canceled_at)` indexes for hot dashboard filters | **FIXED** — migration `20260706_0009` adds both composite indexes | [alembic/versions/20260706_0009_add_hot_path_indexes.py](file:///Users/leihuang/workspace/Ledger/apps/api/alembic/versions/20260706_0009_add_hot_path_indexes.py); [metrics/service.py:121-145](file:///Users/leihuang/workspace/Ledger/apps/api/app/metrics/service.py) |
| 8 | `AgentRunRecorder` commits after every step (~14 commits/run) | **FIXED** — `_maybe_commit` batches commits via `_COMMIT_EVERY` threshold (failures still commit immediately for visibility) | [persistence.py:117-133](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/persistence.py) |
| 9 | Eval cases run sequentially, not in parallel | SUBOPTIMAL | [runner.py:56](file:///Users/leihuang/workspace/Ledger/apps/api/app/evals/runner.py) |
| 10 | OpenAI embeddings batched per-document, not per-corpus | ADEQUATE-gap | [ingestion.py:231-232,401](file:///Users/leihuang/workspace/Ledger/apps/api/app/knowledge/ingestion.py) |
| 11 | No explicit `max_steps` cap (safe today only because graph is linear) | ADEQUATE-gap | [workflow.py:218-224](file:///Users/leihuang/workspace/Ledger/apps/api/app/agent/workflow.py) |

None of these are critical at demo data volume. The most worthwhile remaining fix is SQL consolidation in `metrics/service.py` (#5 — folding the scalar queries into one conditional aggregation).

---

## 5. User Experience

The frontend is **consistently operational, not marketing** — satisfying AGENTS.md's "build the investigation workspace first." Failure visibility is the strongest area.

| Area | Rating | Headline |
|---|---|---|
| Investigation workspace | STRONG | All six required surfaces present; auto-refresh every 2.5s for 10 min; run timeline shows failed steps |
| Failure visibility | STRONG | Errors, stale runs, failed steps, low confidence, rejected approvals, eval failures all surfaced |
| Approval queue | ADEQUATE | Visible, gated, color-coded; status-filter query param supported; no success toast |
| Other pages | ADEQUATE | Incidents, accounts, tickets, knowledge, and evals are real and reachable |
| Nav & layout | STRONG | Core loop nav present; Support and Accounts links included; `aria-current="page"` applied |
| API integration | STRONG | Robust error handling, env-based URL resolution, demo token handled server-side only |
| **Accessibility** | **ADEQUATE** | Global `:focus-visible` style, skip link, and `aria-current="page"` are implemented; muted text contrast remains borderline |
| Responsiveness | STRONG | Two breakpoints collapse grids to single column; tables scroll horizontally |
| Feedback | ADEQUATE | Good empty/error states; no toasts, no optimistic UI |
| E2E / smoke | ADEQUATE | One happy-path Playwright + source-string smoke; failure paths untested end-to-end |

### Top UX fixes

The following were implemented after the initial review:

1. **Global `:focus-visible` style and skip link** — added in [globals.css:44-73](file:///Users/leihuang/workspace/Ledger/apps/web/app/globals.css) and [layout.tsx:19-23](file:///Users/leihuang/workspace/Ledger/apps/web/app/layout.tsx).
2. **Cross-link entities** — account names and ticket IDs render as `<Link>` in run and incident pages.
3. **Support and Accounts in nav with `aria-current="page"`** — [Nav.tsx:6-15,37-39](file:///Users/leihuang/workspace/Ledger/apps/web/app/Nav.tsx).
4. **Failure-path E2E** — [failure-flow.spec.ts](file:///Users/leihuang/workspace/Ledger/apps/web/e2e/failure-flow.spec.ts) covers a rejected approval and resulting error state.

Remaining open items:

5. **Muted text contrast** — some helper text is borderline against the light background.
6. **Toasts / optimistic UI** — not required for the demo, but would improve perceived responsiveness.

---

## 6. Production Readiness

Framing: per AGENTS.md/prd.md this is a **demo-shaped application** whose deploy target is a local workstation. Rated against that intent, then separately flagged for real production.

| Area | Rating (demo) | Rating (real prod) | Key evidence |
|---|---|---|---|
| Security | NEAR-READY | NOT-READY | env-gated fail-closed, parameterized SQL, token-gated mutations, no hardcoded secrets, fake PII / no general auth on reads, loose rate-limit defaults, no CSP |
| Deployment config | NEAR-READY | — | multi-stage non-root Dockerfiles, entrypoint runs migrations + advisory-locked bootstrap, healthchecks, dependency ordering / no restart policy, no resource limits, no log rotation |
| Monitoring & alerting | NEAR-READY | — | real Langfuse/LangSmith tracing with local fallback, JSON logging, `/ready` checks DB+Redis / no Prometheus, no token/cost aggregation, no alerting |
| Documentation | PRODUCTION-READY | — | README + runbook + human-assistance + 6 learning guides; aligned with current architecture and test counts |
| Tests & coverage | PRODUCTION-READY | — | ~163 behavior/contract tests; 6 eval scenarios including ambiguity; PRD success criteria directly asserted; approval-gating negative cases; full investigation loop; Playwright E2E |

### Security specifics

- **Secrets — GOOD:** `pydantic-settings` with `.env`; `.env` not committed (only `*.env.example`); no hardcoded secrets in code (test fixtures only).
- **Auth — demo-only:** `require_demo_data_access` ([access.py:12-21](file:///Users/leihuang/workspace/Ledger/apps/api/app/core/access.py)) 403s unless `app_env in {local,test,development,demo}`. Mutations gated by `X-Demo-Operator-Token` via `secrets.compare_digest` ([access.py:24-45](file:///Users/leihuang/workspace/Ledger/apps/api/app/core/access.py)). No general auth on reads — acceptable for demo, not for real SaaS.
- **SQL injection — SAFE:** all queries use SQLAlchemy 2 ORM with bound parameters; `source_query` strings in citations are display-only, never executed; the LLM does not generate SQL.
- **pgvector — SAFE:** `CAST(:embedding AS vector)` with bound parameter ([search.py:105-112](file:///Users/leihuang/workspace/Ledger/apps/api/app/knowledge/search.py)).
- **Input validation — GOOD:** `KnowledgeSearchRequest.query` bounded `min_length=1, max_length=400`; `IncidentCreate` only accepts `anomaly_id`; `POST /documents/ingest` takes no body (re-ingests built-in markdown only).
- **Payload validation — GOOD:** `validate_action_payload` rejects unsupported fields (e.g., `send_now` → 422), tested at `test_approvals_and_actions.py:146-168`.

### Test coverage gaps (PRD-relevant)

- `/ready` endpoint failure modes (Postgres down, Redis down) — **CLOSED**: `test_health.py:32-130` covers DB-down, Redis-down, Redis-`from_url`-failure, and both-down cases, asserting 503 + generic error status (no raw exception leakage).
- Rate limiting tested on a synthetic app, not on real decorated routes — **CLOSED**: `test_rate_limiting.py:74-125` exercises the real `POST /approvals/{id}/approve` route with a temporarily-lowered limit and asserts the Phase 2 structured error envelope on 429.
- Celery worker path (`investigate_incident.delay`) only exercised by docker-smoke CI, not a unit test.
- No end-to-end test that `OBSERVABILITY_FULL_PAYLOADS=false` actually withholds evidence from a hosted provider (summarization logic tested in isolation only).

---

## 7. Cross-Cutting Priorities

Ranked by impact on PRD success criteria and reviewer trust:

| Priority | Issue | Area | Effort | Status |
|---|---|---|---|---|
| ~~P0~~ | ~~Double-approval race in `approve_request`/`reject_request`~~ | ~~Stability / PRD #4~~ | ~~Small~~ | **FIXED** |
| ~~P0~~ | ~~Eval suite crashes on single-case failure~~ | ~~Stability / PRD #2~~ | ~~Small~~ | **FIXED** |
| ~~P1~~ | ~~`Cache()` per-call ping + no singleton~~ | ~~Stability + Performance~~ | ~~Small~~ | **FIXED** |
| ~~P1~~ | ~~Request-ID not wired to logs~~ | ~~Stability~~ | ~~Small~~ | **FIXED** |
| ~~P1~~ | ~~Global exception handler + structured error envelope in `main.py`~~ | ~~Stability~~ | ~~Small~~ | **FIXED** |
| ~~P1~~ | ~~Celery `task_time_limit`/`soft_time_limit`~~ | ~~Stability~~ | ~~Small~~ | **FIXED** |
| ~~P2~~ | ~~Move eval suite off the HTTP request path~~ | ~~Performance~~ | ~~Medium~~ | **FIXED** (Celery task, 202 response) |
| ~~P2~~ | ~~Seed one "unknown-root-cause" eval scenario~~ | ~~Feature completeness~~ | ~~Medium~~ | **FIXED** |
| ~~P2~~ | ~~Global `:focus-visible` style + skip link + `aria-current`~~ | ~~UX~~ | ~~Small~~ | **FIXED** |
| ~~P2~~ | ~~Cross-link accounts/tickets in run + incident pages; add Support to nav~~ | ~~UX~~ | ~~Small~~ | **FIXED** |
| ~~P3~~ | ~~Composite indexes for `(status, invoice_date)` / `(status, canceled_at)`~~ | ~~Performance~~ | ~~Small~~ | **FIXED** (migration `20260706_0009`) |
| P3 | Consolidate duplicate/scalar metric queries | Performance | Small | Partial — duplicate removed (`metrics/service.py:144-146`); scalar consolidation still open |
| ~~P3~~ | ~~Add `/ready` failure-mode tests and real-route rate-limit tests~~ | ~~Tests~~ | ~~Small~~ | **CLOSED** (`test_health.py:32-130`, `test_rate_limiting.py:74-125`) |
| P3 | Add Prometheus/ops metrics and token/cost aggregation | Observability | Medium | Open / out of PRD scope |

---

## 8. Overall Assessment

**Is the implementation PRD-compliant?** **Yes.** All five success criteria are met with structural enforcement (not just prompts), real tool boundaries, real pgvector RAG, deterministic metrics, and a complete audit trail. The ~163-test suite directly asserts the PRD's "4 of 5" bar, approval-gating, citation quality, and trace requirements. The seed now includes six scenarios, including an ambiguity case that exercises the agent's uncertainty path.

**Is it production-ready?** For its **intended demo/reviewer scope: yes.** The P0/P1 stability and observability gaps identified in the audit have been fixed. Documentation and tests are production-grade. Security is appropriately fail-closed for a demo. The remaining open items are lower-impact performance polish or out of PRD scope.

**What would block a real multi-tenant production deploy (out of PRD scope)?** Real authentication, hardened rate-limit defaults, CSP/security headers, Prometheus metrics + token/cost aggregation, alerting, and scalar SQL consolidation in the metrics service.

**The two fixes that originally most protected the PRD's claims** — the double-approval race and the eval-suite crash-on-single-failure — are now implemented. Current attention should shift to reviewer UX evidence (run the eval suite, inspect a run trace, exercise approve/reject) and the small open P3 items above.
