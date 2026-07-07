# PRD-Alignment Review — 2026-07-07

**Scope:** Full codebase audit of `ops-agent` (FastAPI `apps/api` + Next.js `apps/web`)
against `prd.md` and `AGENTS.md`.
**Method:** Five parallel read-only deep-dives (feature completeness, stability,
performance, UX, production readiness). Every claim below was verified by reading
the cited `file:line` in the **current** code — the prior audit
(`docs/prd-alignment-review.md`, 2026-07-06) was treated as a hypothesis, not a
source of truth. Where the prior audit and the code disagree, this report says so.

> This review supersedes `docs/prd-alignment-review.md`. It confirms the prior
> audit's "FIXED" claims, corrects its stale entries, flags two pieces of
> documentation drift, and surfaces seven new issues the prior audit did not
> raise.

---

## 1. Executive Summary

The implementation is **PRD-COMPLIANT on all five success criteria**, verified
against current code. The prior audit's six claimed P0/P1 fixes are all
genuinely in place. Documentation and tests are production-grade (163 tests
directly asserting the PRD bar, approval-gating negatives, the ambiguity case,
and the full investigation loop). For the **intended demo/reviewer scope the
codebase is PRODUCTION-READY.**

The audit's strongest dimension remains **evidence discipline and auditability**;
citations are workflow-enforced and approval gating genuinely blocks execution.
The verification surfaced concrete items the prior audit missed or misstated:

- **The most important new finding:** Celery's `SoftTimeLimitExceeded` is never
  caught. A worker killed mid-eval-suite leaves the `eval_run` reporting
  `"running"` **forever** — there is no orphan reaper for eval runs (unlike agent
  runs). This directly degrades the reviewer demo loop (`POST /evals/run` +
  polling) and should be fixed before a reviewer run.
- **The prior audit doc is stale in four places:** two "open P3" performance
  items are actually fixed (composite indexes, duplicate metric query), and two
  "test coverage gaps" are actually closed (`/ready` failure modes, real-route
  rate-limit enforcement).
- **`AGENTS.md` has drifted from code** on two points: it says LangGraph is not
  used, but `workflow.py` imports and compiles a `StateGraph`; and it says
  `POST /evals/run` is gated by `DEMO_OPERATOR_TOKEN`, but it is gated by a
  separate `EVAL_RUN_TOKEN`.
- **The cache "no per-call ping" claim is inaccurate:** the singleton avoids
  reconnect cost but `_get_redis_client` still calls `PING` on every `Cache()`
  instantiation.

### Headline scores

| Dimension | Rating (demo scope) | Change vs. prior audit |
|---|---|---|
| Feature completeness vs PRD | **COMPLIANT** | unchanged |
| Stability & reliability | **ADEQUATE** (1 new moderate issue) | down on eval-run liveness |
| Performance & efficiency | **ADEQUATE→STRONG** (2 P3 items now fixed) | up |
| UX / frontend | **STRONG** op; **ADEQUATE** a11y (3 new gaps) | flat |
| Production readiness (demo) | **PRODUCTION-READY** (2 "gaps" now closed) | up |
| Production readiness (real SaaS) | **NOT-READY** — out of PRD scope | unchanged |

---

## 2. Feature Completeness — COMPLIANT

### PRD success criteria — all met (verified)

| # | Criterion | Verdict | Verified evidence |
|---|---|---|---|
| 1 | ≥5 seeded incident scenarios | **COMPLIANT** | [seed.py:51-147](apps/api/app/seed.py) defines **6** scenarios incl. `unknown_root_cause` ambiguity case; `test_evals.py:50-51` asserts count+set |
| 2 | Root cause for ≥4 of 5 evals | **COMPLIANT** | [runner.py:22,105-111](apps/api/app/evals/runner.py) `PASSING_SCENARIO_THRESHOLD=4`; `test_evals.py:70` asserts `>=4` |
| 3 | Final reports cite SQL/tickets/docs | **COMPLIANT** | [schemas.py:28-51](apps/api/app/agent/schemas.py) typed `ReportEvidence`; [workflow.py:333-454](apps/api/app/agent/workflow.py) always builds evidence from real tool results |
| 4 | Risky actions blocked until approved | **COMPLIANT** | [approvals/service.py:73,81,245-303,306-352](apps/api/app/approvals/service.py); high-risk actions start `pending_approval`, `executed_at=None` |
| 5 | Every run has trace/steps/tokens/cost/report | **COMPLIANT*** | [models.py:243-319](apps/api/app/models.py); [tracing.py:239-278](apps/api/app/agent/tracing.py) always returns a handle |

\* Edge-case caveat: failed runs recorded by the eval suite have
`final_report = None` ([runner.py:279](apps/api/app/evals/runner.py)).
A crashed run cannot synthesize a report, and AGENTS.md wants dead-ends visible,
so this is defensible — but a literal reading of "every run has … a final report"
is not met for error paths.

### Other completeness checks — all COMPLIANT

- **All 12 PRD API routes exist and are registered** in
  [main.py:99-108](apps/api/app/main.py).
- **All 14 PRD data models exist** in [models.py](apps/api/app/models.py)
  (plus bonus `KnowledgeDocumentChunk`, `ActionAuditEvent`).
- **RAG with pgvector is real, not stubbed:**
  [search.py:93-123](apps/api/app/knowledge/search.py)
  runs `1 - (c.embedding <=> CAST(:embedding AS vector))` with a bound param.
- **Metrics are deterministic:** grep for `llm|LLM|openai|anthropic` across
  `apps/api/app/metrics/` returns **no matches**. No LLM in the metrics path.
- **Tool boundaries explicit:**
  [tools.py](apps/api/app/agent/tools.py)
  exposes 4 read-only tools; mock actions are proposed post-report, never invoked
  by the LLM.
- **Demo mutation gating:**
  [access.py:24-45](apps/api/app/core/access.py)
  uses `secrets.compare_digest` and fails closed in `demo` env when the token is
  unset.

### Discrepancies vs. prior audit / AGENTS.md (documentation drift, not PRD violations)

1. **AGENTS.md is stale on LangGraph.** AGENTS.md states "The MVP uses a fixed
   linear investigation DAG implemented directly in `workflow.py`. Do not
   introduce LangGraph unless a real feature requires dynamic branching." But
   [workflow.py:7,65,226-227](apps/api/app/agent/workflow.py)
   imports `from langgraph.graph import END, START, StateGraph`, builds a
   `StateGraph(InvestigationState)`, compiles and invokes it. LangGraph **is**
   used; AGENTS.md should be updated (the PRD recommends LangGraph, so no PRD
   violation — just internal doc drift).
2. **AGENTS.md is stale on the eval-token name.** AGENTS.md lists
   `POST /evals/run` among routes "gated by `DEMO_OPERATOR_TOKEN`." In reality it
   is gated by a **separate** `EVAL_RUN_TOKEN` via `require_eval_run_access`
   ([evals/router.py:27-45](apps/api/app/evals/router.py));
   `POST /documents/ingest` similarly uses `DOCUMENT_INGEST_TOKEN`
   ([knowledge/router.py:34-52](apps/api/app/knowledge/router.py)).
   Security intent is met; the doc's specifics are wrong.
3. **Audit overstates "structurally enforced" citations.**
   [schemas.py:43-51](apps/api/app/agent/schemas.py)
   types `cited_evidence: list[ReportEvidence]` but has **no `min_length=1`**.
   Non-emptiness is guaranteed by the workflow (tools always run), not by the
   schema. Behavior is correct; the audit's "structural" phrasing is stronger
   than reality.

---

## 3. Stability & Reliability — ADEQUATE

### Prior audit's six claimed fixes — all VERIFIED

| Fix | Verdict | Evidence |
|---|---|---|
| Double-approval race | **VERIFIED** | [approvals/service.py:253-272,314-333](apps/api/app/approvals/service.py) conditional `UPDATE … WHERE status='pending'` + rowcount check; 409 in [router.py:89-90,111-112](apps/api/app/approvals/router.py) |
| Eval suite crash-on-single-failure | **VERIFIED** | [runner.py:59-93](apps/api/app/evals/runner.py) per-case try/except, `_record_failed_agent_run`, incremental commit |
| Cache singleton | **VERIFIED** (with caveat, see N2) | [cache.py:22-56](apps/api/app/cache.py) module-level `_redis_client`, lazy init, in-memory fallback |
| Request-ID in logs | **VERIFIED** | [logging_config.py:17-26,66,70](apps/api/app/logging_config.py) `RequestIdFilter` on root handler |
| Celery task time limits | **VERIFIED** | [celery_app.py:26-27](apps/api/app/celery_app.py) `task_time_limit=600`, `task_soft_time_limit=540` |
| Global exception handler | **VERIFIED** | [main.py:63-82](apps/api/app/main.py) `@app.exception_handler(Exception)` → structured envelope via [core/errors.py:8-23](apps/api/app/core/errors.py); detail gated to `{local,test,development}` |

### Broader reliability — solid

- **LLM failure fallback:** [workflow.py:502-551,560-568](apps/api/app/agent/workflow.py) computes a deterministic diagnosis first, tries LLM, falls back on any exception; `trace_metadata` records `llm_used`/`llm_fallback_reason`.
- **Malformed LLM output rejection:** [client.py:177-189](apps/api/app/llm/client.py) raises on bad JSON; [llm/schemas.py:8-12](apps/api/app/llm/schemas.py) `LLMResponse` requires non-empty `root_cause` + confidence enum. Output is rejected, not coerced.
- **Tool failure recording:** [persistence.py:34-72,135-148](apps/api/app/agent/persistence.py) wraps each step in a SAVEPOINT, commits failed steps immediately for audit visibility, re-raises.
- **Concurrent-run guard:** two-layer — partial unique index `uq_agent_runs_active_incident` (migration 0007) + atomic claim `UPDATE … WHERE status='queued'` ([agent/service.py:164-186](apps/api/app/agent/service.py)); orphan reaper for stale agent runs at [service.py:418-434](apps/api/app/agent/service.py).

### NEW stability concerns (not in prior audit)

**N1 — `SoftTimeLimitExceeded` is never caught; an eval run soft-killed mid-suite reports `"running"` forever. (MODERATE — top new finding)**
Neither [agent/tasks.py](apps/api/app/agent/tasks.py) nor
[evals/tasks.py](apps/api/app/evals/tasks.py) catches
`celery.exceptions.SoftTimeLimitExceeded` (grep finds none). When the 540 s soft
limit fires:
- Agent runs recover within ~10 min via the orphan reaper
  ([service.py:418-434](apps/api/app/agent/service.py)).
- **Eval runs do not recover.** `build_eval_run_summary`
  ([runner.py:168-215,194-200](apps/api/app/evals/runner.py))
  returns `status="running"` whenever `len(result_reads) < expected_case_count`,
  and there is **no orphan reaper for eval runs**. A reviewer who soft-kills a
  suite (or hits the 600 s hard limit) will poll `GET /evals/runs/{id}` and see
  `"running"` indefinitely.
- **Fix:** catch `SoftTimeLimitExceeded`/`TimeLimitExceeded` in both Celery
  tasks and mark the run/eval-run failed; or add a staleness reaper for eval runs
  mirroring `_abandon_orphaned_runs`.

**N2 — Per-call Redis `PING` doubles cache round-trips. (MODERATE, performance-adjacent)**
[cache.py:38-44](apps/api/app/cache.py)
calls `_redis_client.ping()` on **every** `Cache()` instantiation despite the
comment at L20-21 claiming the singleton exists "so we don't pay a ping()
round-trip on every call." The singleton avoids reconnect cost, not ping cost.
`get_run_detail` ([agent/service.py:357](apps/api/app/agent/service.py))
and `_invalidate_run_detail_cache` (L353) both construct a `Cache()`, so each
`/agent/runs/{id}` and approval mutation costs PING+GET (or PING+DELETE). Under
load this doubles cache latency. **Fix:** time-gate the health check (ping at
most every N seconds) or ping only on connection error.

**N3 — `_LOCAL_CACHE_VALUES` grows unbounded during Redis outages. (MINOR-MODERATE)**
[cache.py:18](apps/api/app/cache.py)
fallback dict expires entries lazily on `get` (L69-77) and `scan_iter`
(L87-94). Keys written via `setex` that are never read again are never swept,
so a sustained Redis outage can grow memory without bound. **Fix:** periodic
sweep or max-size bound.

**N4 — `OpenAIClient`/`AnthropicClient` do not validate the API response shape. (MINOR, mitigated)**
[client.py:110,163](apps/api/app/llm/client.py)
index `raw["choices"][0]["message"]["content"]` /
`raw["content"][0]["text"]` with no shape guard. A 200 with empty/filtered
`choices` raises `KeyError`/`IndexError`. Mitigated because
[workflow.py:560-568](apps/api/app/agent/workflow.py)
catches broadly and falls back deterministically — but the recorded `LLMUsage`
will say `llm_error` rather than reflecting tokens consumed by the failed call.

**N5 — `request_id_middleware` does not use `try/finally` for contextvar reset. (NEGLIGIBLE today)**
[main.py:93-96](apps/api/app/main.py)
resets `request_id_context` only after `call_next` returns. Safe today because
the global exception handler converts errors to 500 responses before they reach
the middleware, but fragile to future changes. **Fix:** wrap `call_next` in
`try/finally`.

No resource leaks, uncaught exceptions, or additional race conditions were found
beyond the above.

---

## 4. Performance & Efficiency — ADEQUATE→STRONG

### Prior audit's claimed fixes — verified (one with caveat)

| Item | Status | Evidence |
|---|---|---|
| Cache singleton | **PARTIAL** | singleton yes ([cache.py:22-56](apps/api/app/cache.py)); per-call ping still happens (see N2) — audit's "no per-call ping" wording is inaccurate |
| Celery time limits | **FIXED** | [celery_app.py:26-27](apps/api/app/celery_app.py) |
| Eval suite off HTTP path | **FIXED** | [evals/router.py:63-82](apps/api/app/evals/router.py) returns 202 + enqueues Celery task; [evals/tasks.py:8-22](apps/api/app/evals/tasks.py) |
| list_incidents pagination | **FIXED** | [incidents/router.py:23-30](apps/api/app/incidents/router.py) `limit`/`offset` |

### Prior audit's "open P3" items — two are now actually FIXED (audit doc is stale)

| Item | Actual status | Evidence |
|---|---|---|
| Composite indexes `(status, invoice_date)` / `(status, canceled_at)` | **FIXED** | [migration 20260706_0009:30-42](apps/api/alembic/versions/20260706_0009_add_hot_path_indexes.py) creates both; backs [metrics/service.py:84-113,135-140](apps/api/app/metrics/service.py). Prior audit L199 lists this as Open P3 — stale. |
| Duplicate `unresolved_count` query | **FIXED** | [metrics/service.py:135-146](apps/api/app/metrics/service.py) comment documents removal; `unresolved_count = failed_count` aliases for API compat. Prior audit L107 lists SUBOPTIMAL — stale. |
| `AgentRunRecorder` commits after every step | **FIXED** | [persistence.py:23,129-133](apps/api/app/agent/persistence.py) `_COMMIT_EVERY=5`; ~3 commits/run instead of ~14 |
| Multiple scalar queries foldable into one | **OPEN** (mitigated) | [metrics/service.py:33-264](apps/api/app/metrics/service.py) — MRR + churn scan `subscriptions` with near-identical predicates; mitigated by 60 s cache ([L286-291](apps/api/app/metrics/service.py)) |
| Eval cases run sequentially | **OPEN** | [runner.py:56](apps/api/app/evals/runner.py) `for case in cases:`; fine at 6 cases, now off-HTTP |
| No explicit `max_steps` cap | **OPEN** (low risk) | [workflow.py:212-227](apps/api/app/agent/workflow.py) fixed linear DAG; Celery 600 s limit is the backstop |

### Other assessments

- **pgvector HNSW — CORRECT:** migration `20260612_0003:132-138` creates
  `USING hnsw (embedding vector_cosine_ops)`; [search.py:104,107](apps/api/app/knowledge/search.py)
  uses the matching `<=>` cosine operator.
- **Token/cost estimation — CHEAP and CORRECT:**
  [llm/tokenizer.py:6-39](apps/api/app/llm/tokenizer.py)
  tiktoken with model-specific encoding + regex fallback;
  [llm/pricing.py:12-43](apps/api/app/llm/pricing.py)
  O(1) dict-lookup arithmetic; live clients use provider-reported counts
  ([client.py:111,169](apps/api/app/llm/client.py)).
- **N+1 risks — LOW.** `list_support_tickets` uses an explicit JOIN
  ([support/service.py:20-21](apps/api/app/support/service.py));
  `list_accounts`/`list_incidents` are single-query+count. `get_account_detail`
  issues 6 round-trips per detail view (consolidatable, minor).
- **Pagination gaps:** `list_accounts` hardcodes `limit=100` with no `offset`
  ([accounts/router.py:18-20](apps/api/app/accounts/router.py));
  `list_support_tickets` exposes `limit` but **no `offset`**
  ([support/router.py:31-39](apps/api/app/support/router.py));
  `list_agent_runs` has no pagination at all
  ([agent/service.py:315-338](apps/api/app/agent/service.py)).
  None critical at demo volume.

### Three highest-impact remaining performance issues

1. **Cache per-call PING** (N2 above) — a network round-trip on every cached read.
2. **`get_dashboard_metrics` issues 5+ aggregations when 2-3 would suffice**
   ([metrics/service.py:33-264](apps/api/app/metrics/service.py));
   the 60 s cache caps cost, but every cache miss pays full multi-query latency.
3. **Eval suite runs cases strictly sequentially**
   ([runner.py:56](apps/api/app/evals/runner.py));
   a Celery chord/group would cut suite wall-time ~6×, directly improving the
   reviewer demo loop.

---

## 5. User Experience — STRONG operationally, ADEQUATE on accessibility

### Claimed features — verified

| Feature | Verdict | Evidence |
|---|---|---|
| 6 investigation workspace surfaces | **VERIFIED*** | [agent/runs/[runId]/page.tsx:141-453](apps/web/app/agent/runs) |
| Auto-refresh 2.5 s / 10 min | **VERIFIED** | [RunRefresh.tsx:6-7,17-25](apps/web/app/agent/runs/[runId]/RunRefresh.tsx) |
| Failure visibility (errors/stale/failed steps/low confidence/rejected/eval) | **VERIFIED** — strongest dimension | multiple `aria-live`/`role="alert"` panels; [globals.css:734-743](apps/web/app/globals.css) |
| Approval queue visible/gated/color-coded | **VERIFIED** | [approvals/page.tsx:30-86](apps/web/app/approvals/page.tsx); [globals.css:722-754](apps/web/app/globals.css) |
| Approval status-filter | **OVERSTATED** | `status` query param is consumed ([approvals/page.tsx:12-13](apps/web/app/approvals/page.tsx)) but **no UI control** lets a user change it — URL-editing only |
| `:focus-visible` + skip link + `aria-current` | **VERIFIED** | [globals.css:44-73](apps/web/app/globals.css); [layout.tsx:19-23](apps/web/app/layout.tsx); [Nav.tsx:39](apps/web/app/Nav.tsx) |
| Cross-link entities | **VERIFIED** | account names / ticket IDs as `<Link>` on run, incident, account, ticket pages |
| E2E tests (happy + failure path) | **VERIFIED** | [demo-flow.spec.ts](apps/web/e2e/demo-flow.spec.ts) + [failure-flow.spec.ts](apps/web/e2e/failure-flow.spec.ts) — failure-flow is now present, fixing the prior audit's "failure paths untested" note |
| Responsiveness (2 breakpoints) | **VERIFIED** | [globals.css:1246,1288](apps/web/app/globals.css) |

\* The run page's snapshot bar shows **run metadata** (started/completed/trace/
tokens/cost), not the numeric anomaly metrics (MRR delta, drop %). Those live one
click away on [incidents/[incidentId]/page.tsx:89-106](apps/web/app/incidents/[incidentId]/page.tsx).
5 of 6 surfaces are fully on the run page; the anomaly panel is one click away.

### NEW UX/accessibility gaps (not in prior audit)

**U1 — No custom error boundary.** No `error.tsx`/`global-error.tsx`/`not-found.tsx`
anywhere under `apps/web/app/` (glob returned none). Unhandled server-component
or server-action errors fall through to Next.js defaults; server-action throws
(e.g. [actions.ts:111-117](apps/web/app/actions.ts)
`readRequiredFormValue`) surface as generic error pages rather than inline
recovery UI.

**U2 — No client-side form validation.** Grep for
`required|aria-invalid|aria-describedby|pattern=` across `apps/web/app` found
**zero matches**. The knowledge search input
([knowledge/page.tsx:50-57](apps/web/app/knowledge/page.tsx))
is `type="search"` with no `required`/`pattern`. Empty/invalid input round-trips
to the backend before feedback.

**U3 — Metric-card tone is color-only.**
[page.tsx:389](apps/web/app/page.tsx)
applies `metric-${tone}` which only changes `border-top-color`
([globals.css:330-344](apps/web/app/globals.css)).
A colorblind reviewer cannot distinguish "danger" from "good" without reading the
detail text. (Status pills elsewhere correctly include text labels — this is the
one color-only case.)

### Confirmed prior-audit gaps

- **Muted text contrast borderline:** `--muted: #637083` is ~4.94:1 on white,
  ~4.61:1 on `--background` — passes WCAG AA technically but used at 11-13 px in
  many labels ([globals.css:439-445,278-289](apps/web/app/globals.css)).
- **No toasts / optimistic UI / action pending state:** approve/reject/run-
  investigation are plain `<form>` submissions; the browser's native navigation
  spinner is the only feedback.
- **No skeleton loading** — only text-based loading messages.

---

## 6. Production Readiness — PRODUCTION-READY (demo scope)

### Security

| Area | Rating (demo) | Evidence |
|---|---|---|
| Secrets | PRODUCTION-READY | [config.py:8-101](apps/api/app/core/config.py) pydantic-settings, `.env` ignored in `.gitignore:2-5`, no hardcoded secrets |
| Auth (demo gating) | PRODUCTION-READY | [access.py:12-45](apps/api/app/core/access.py) fail-closed in `demo` env, `secrets.compare_digest` |
| SQL injection | PRODUCTION-READY | SQLAlchemy 2 ORM throughout; `source_query` strings display-only |
| pgvector | PRODUCTION-READY | [search.py:104,107,111](apps/api/app/knowledge/search.py) `CAST(:embedding AS vector)` bound param |
| Input validation | PRODUCTION-READY | [knowledge/schemas.py:12-14](apps/api/app/knowledge/schemas.py) bounded; `IncidentCreate` only `anomaly_id`; `/documents/ingest` no body |
| Payload validation | PRODUCTION-READY | [approvals/service.py:370-381](apps/api/app/approvals/service.py) rejects unsupported fields |
| Rate limiting | NEAR-READY | [core/limiter.py:9-29](apps/api/app/core/limiter.py) Redis+memory fallback; **defaults loose** (1000/min, [config.py:55-56](apps/api/app/core/config.py)); real-route enforcement **is tested** ([test_rate_limiting.py:74-125](apps/api/tests/test_rate_limiting.py)) |
| CSP / security headers | NOT-READY (prod) | [apps/web/next.config.ts:1-6](apps/web/next.config.ts) is bare — no `headers()`, no CSP, no `X-Frame-Options` |

### Deployment

- **Dockerfiles — PRODUCTION-READY:** both multi-stage with non-root users and
  `HEALTHCHECK` ([apps/api/Dockerfile](apps/api/Dockerfile),
  [apps/web/Dockerfile](apps/web/Dockerfile)).
- **docker-compose — NEAR-READY:** healthchecks + dependency ordering
  ([docker-compose.yml](docker-compose.yml));
  **gaps:** no `restart:` policy, no resource limits, worker healthcheck
  disabled (L116), no log rotation.
- **Entrypoint migrations + advisory lock — PRODUCTION-READY:**
  [entrypoint.sh:11-14](apps/api/entrypoint.sh)
  + [bootstrap.py:15-66](apps/api/app/bootstrap.py)
  `pg_advisory_lock` serializes first-boot migrations/seeding; orphaned active
  runs abandoned on startup.

### Monitoring

- **Tracing — PRODUCTION-READY:** [tracing.py:239-278,281-485](apps/api/app/agent/tracing.py)
  Langfuse → LangSmith → local fallback; all SDK calls degrade gracefully.
- **JSON logging — PRODUCTION-READY:** [logging_config.py:17-71](apps/api/app/logging_config.py)
  `RequestIdFilter` + `JsonFormatter`; request-ID header sanitized against log
  injection ([main.py:89-91](apps/api/app/main.py)).
- **`/ready` — PRODUCTION-READY:** [health/router.py:37-87](apps/api/app/health/router.py)
  checks Postgres + Redis independently, returns 503 on failure. **Failure modes
  ARE tested** ([test_health.py:32-130](apps/api/tests/test_health.py))
  — this **contradicts** the prior audit's claim (L176) that they are untested.
- **Prometheus / global token-cost aggregation — NOT present** (out of PRD
  scope; per-run cost is recorded at [models.py:266-269](apps/api/app/models.py)).

### Documentation & tests

- **Docs — PRODUCTION-READY:** `README.md`, `docs/` (runbook, human-assistance,
  prd-completion-plan, prior review), `learning/` (week-1..6 + study notes).
  Note: `learning/` is gitignored (`.gitignore:13`).
- **Tests — PRODUCTION-READY:** **163 test functions across 21 files**. PRD
  criteria directly asserted: eval 4-of-5 + ambiguity
  ([test_evals.py:70,157,199,292](apps/api/tests/test_evals.py));
  approval-gating negatives including concurrent double-approval
  ([test_approvals_and_actions.py:148,207,464,512](apps/api/tests/test_approvals_and_actions.py));
  full investigation loop with cited evidence
  ([test_agent_investigations.py:44,833,838](apps/api/tests/test_agent_investigations.py));
  Playwright happy + failure E2E.

### Prior-audit "test coverage gaps" — two are now CLOSED (audit doc stale)

| Prior audit claim (L176-179) | Actual status |
|---|---|
| `/ready` failure modes untested | **CLOSED** — [test_health.py:32-130](apps/api/tests/test_health.py) covers DB down, Redis down, `from_url` failure, both down |
| Rate-limit tested on synthetic app, not real routes | **CLOSED** — [test_rate_limiting.py:74-125](apps/api/tests/test_rate_limiting.py) exhausts the approve route's limit and asserts the 429 envelope |
| Celery worker path only exercised by docker-smoke | **PARTIAL** — `test_celery_config.py` (1 test) + synchronous `.run()` in test env ([agent/router.py:30-33](apps/api/app/agent/router.py)); light but present |
| `OBSERVABILITY_FULL_PAYLOADS=false` end-to-end | **OPEN** — `test_tracing_providers.py` covers summarization in isolation only; no end-to-end "hosted provider receives summarized payload" test |

---

## 7. Cross-Cutting Priorities

Ranked by impact on PRD success criteria and reviewer trust. Items marked
**(NEW)** were not in the prior audit.

| Priority | Issue | Area | Effort | Status |
|---|---|---|---|---|
| **P1 (NEW)** | Catch `SoftTimeLimitExceeded` in Celery tasks; add eval-run orphan reaper | Stability / reviewer demo | Small | **Open** |
| P2 (NEW) | Cache per-call PING → time-gated health check | Performance | Small | Open |
| P2 | Add error boundary (`error.tsx`/`global-error.tsx`) | UX | Small | Open |
| P2 (NEW) | Add client-side form validation (`required`/`aria-invalid`/`aria-describedby`) | UX / a11y | Small | Open |
| P3 (NEW) | Metric-card tone is color-only — add icon/text label | UX / a11y | Small | Open |
| P3 (NEW) | Bound `_LOCAL_CACHE_VALUES` during Redis outage | Stability | Small | Open |
| P3 | Consolidate metric queries (MRR+churn) | Performance | Small | Open |
| P3 | Parallelize eval cases (Celery chord/group) | Performance | Medium | Open |
| P3 | Add status-filter UI control on approvals page | UX | Small | Open |
| P3 | `request_id_middleware` use `try/finally` | Stability | Trivial | Open |
| P3 | Update AGENTS.md: LangGraph is used; `EVAL_RUN_TOKEN` not `DEMO_OPERATOR_TOKEN` for `/evals/run` | Docs | Trivial | Open |
| P3 | Update `docs/prd-alignment-review.md`: composite indexes + duplicate query are FIXED; `/ready` + rate-limit tests are CLOSED | Docs | Trivial | Open |
| P3 | Pagination: add `offset` to `list_accounts`/`list_support_tickets`; paginate `list_agent_runs` | Performance | Small | Open |
| P3 | Add end-to-end `OBSERVABILITY_FULL_PAYLOADS=false` test | Tests | Medium | Open |
| **Out of PRD scope** | Real auth, CSP/security headers, Prometheus + global cost aggregation, alerting, composite restart/resource-limit policy, hardened rate-limit defaults | Production (real SaaS) | Large | Out of scope |

---

## 8. Overall Assessment

**Is the implementation PRD-compliant?** **Yes.** All five success criteria are
met, verified against current code with structural enforcement (typed citations,
approval-gated actions, deterministic metrics), real tool boundaries, real
pgvector RAG, and a complete audit trail. The 163-test suite directly asserts the
"4 of 5" bar, approval-gating (including concurrent double-approval negatives),
citation quality, and trace requirements. The seed includes six scenarios
including an ambiguity case.

**Is it production-ready?** For its **intended demo/reviewer scope: yes.** The
prior audit's six P0/P1 fixes are all genuinely in place. Documentation and tests
are production-grade. Security is appropriately fail-closed for a demo. Two of
the prior audit's four "test coverage gaps" are now closed. The remaining open
items are lower-impact polish or out of PRD scope.

**What most protects the PRD's claims now?** The verified approval race fix, the
verified eval resilience, the verified citation workflow, and the verified
trace/steps/tokens/report completeness.

**What should be fixed before a reviewer run?** The single most worthwhile fix is
**N1** — catching `SoftTimeLimitExceeded` so a soft-killed eval run cannot report
`"running"` forever. It directly undermines the reviewer demo loop
(`POST /evals/run` + polling) and is a small, surgical change. After that, the
P2/UX items (error boundary, client-side validation) and the doc-drift corrections
(AGENTS.md, prior audit) are the highest-value low-effort wins.

**What would block a real multi-tenant production deploy (out of PRD scope)?**
Real authentication, CSP/security headers, Prometheus metrics + global token/cost
aggregation, alerting, composite restart/resource-limit policy, and hardened
rate-limit defaults.
