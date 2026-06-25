# Ops Agent

Ops Agent is a production-shaped SaaS revenue and support investigation workspace. The final product will help an operator answer a prompt like: "MRR dropped this week. Investigate the cause, identify affected accounts, cite evidence, recommend actions, and draft follow-ups."

The product is intentionally not a toy chatbot. Its core promise is an auditable agent loop that combines revenue analytics, product usage, support tickets, knowledge documents, controlled action drafts, approval gates, run traces, and evals. Every important claim should be backed by cited evidence from queried data or retrieved documents.

Use this README as a runbook for local setup and inspection. Do not treat it as the implementation source of truth. The product source of truth is `prd.md`; the implementation source of truth is the code, migrations, API docs, and tests.

The project should be considered ready for review only when the verification commands below pass and the inspection checklist produces evidence-backed answers.

## Product Direction

The intended workflow:

1. Detect or select a revenue anomaly.
2. Investigate billing, account, product usage, support, and incident evidence.
3. Produce a concise root-cause report with citations.
4. Identify affected accounts and recommended actions.
5. Draft follow-ups as approval-gated mock actions.
6. Record run steps, traces, cost estimates, failure modes, and eval results.

The first version should prioritize evidence quality, eval correctness, and approval safety over a large integration surface.

## Readiness Model

Use these labels when deciding what kind of inspection the project is ready for:

- **Engineering inspection ready** - the stack boots locally, migrations run, seed/bootstrap is repeatable, tests pass, and the UI/API expose at least one coherent vertical slice.
- **Product slice review ready** - a reviewer can inspect an anomaly, supporting evidence, affected accounts, citations, and visible failure/safety boundaries in the UI.
- **MVP acceptance ready** - the app satisfies the success criteria in `prd.md`, including seeded evals, approval-gated mock actions, run traces, cost/token estimates, and evidence-backed final reports.

Do not advance a readiness label by editing this file. Advance it by adding implementation, tests, and inspection evidence.

## Structure

- `apps/api` - FastAPI app with Pydantic v2 settings, SQLAlchemy 2 engine/session setup, Alembic, seeded SaaS data, metrics, incidents, knowledge search, and pytest coverage.
- `apps/web` - Next.js App Router UI for the operational dashboard, incident detail flow, and knowledge search.
- `docker-compose.yml` - Local Postgres, Redis, API, and web services.
- `prd.md` - Product brief and success criteria.
- `AGENTS.md` - Project guardrails for future agent work.

## Local Setup

Create local environment files:

```bash
cp .env.example .env
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env.local
```

Start the stack:

```bash
docker compose up --build
```

Useful URLs:

- Backend health: http://localhost:8000/health
- Backend readiness: http://localhost:8000/ready
- API docs: http://localhost:8000/docs
- Frontend: http://localhost:3000
- Knowledge search: http://localhost:3000/knowledge
- Eval report: http://localhost:3000/evals

On first API startup, the container runs Alembic migrations and bootstraps the deterministic demo dataset if the database is empty.

## Backend Development

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload
```

Run Alembic migrations when migrations exist:

```bash
cd apps/api
alembic upgrade head
```

Seed the deterministic SaaS operations dataset:

```bash
cd apps/api
python -m app.seed --json
```

This recreates the seeded SaaS domain tables with 60 accounts, 600 invoices,
6,000 product events, 240 support tickets, 5 open scenario incidents, 5 eval
cases, and the built-in knowledge base documents. Re-running it should produce
the same counts and fingerprint.

Ingest or refresh only the built-in knowledge base:

```bash
cd apps/api
python -m app.knowledge.ingestion --json
```

The HTTP refresh endpoint `POST /documents/ingest` is disabled unless
`DOCUMENT_INGEST_TOKEN` is set. When enabled, callers must pass
`X-Document-Ingest-Token: <token>`. Prefer the CLI/bootstrap path for normal
local setup.

The local embedding path is deterministic and does not require external
credentials:

- `EMBEDDING_PROVIDER=local`
- `EMBEDDING_MODEL=local-hashing-v1`
- `DOCUMENT_INGEST_TOKEN=` optional token for the mutating HTTP ingest endpoint
- `EVAL_RUN_TOKEN=` optional token that enables the mutating HTTP eval runner

## Observability And Tracing

Every agent run persists `trace_id`, `trace_url`, `trace_provider`, and
`trace_metadata`. The eval tables remain the source of truth for regression
quality; external tracing is an audit and debugging layer, not the only record
of correctness.

Provider selection is controlled by:

- `OBSERVABILITY_PROVIDER=auto` by default. Valid values are `auto`, `local`, `langfuse`, and `langsmith`.
- `auto` prefers Langfuse when Langfuse credentials exist, then LangSmith when legacy LangSmith tracing is enabled, then local traces.
- `local` stores `local://agent-runs/...` trace identifiers and never calls a hosted service.
- `OBSERVABILITY_FULL_PAYLOADS=false` keeps hosted traces to run/span metadata and small summaries by default. Set it to `true` only for synthetic/local demos where exporting raw evidence payloads is acceptable.
- `OBSERVABILITY_TIMEOUT_SECONDS=2` sets short fail-open SDK timeouts for hosted trace providers.

Langfuse is the recommended hosted or self-hosted provider for this portfolio
project because it keeps the observability story aligned with an open-source
stack:

- `LANGFUSE_PUBLIC_KEY=` and `LANGFUSE_SECRET_KEY=` are required for Langfuse.
- `LANGFUSE_BASE_URL=https://cloud.langfuse.com` points to Langfuse Cloud or a self-hosted Langfuse URL.
- `LANGFUSE_PROJECT_ID=` is optional but recommended because the app can then construct clickable trace URLs like `/project/<id>/traces/<trace_id>`.

LangSmith remains supported for comparison or LangChain-heavy demos:

- `OBSERVABILITY_PROVIDER=langsmith` selects LangSmith explicitly.
- `LANGSMITH_API_KEY=` is required for hosted LangSmith traces.
- `LANGSMITH_TRACING=true` lets `auto` select LangSmith when Langfuse credentials are absent.
- `LANGSMITH_ENDPOINT=https://api.smith.langchain.com` or the regional endpoint.
- `LANGSMITH_PROJECT=ops-agent-local` names the LangSmith project.
- `LANGSMITH_WEB_URL=https://smith.langchain.com` is used when constructing trace links.

When an external provider is missing, disabled, or fails to start, agent runs
fall back to local traces and persist metadata explaining the requested provider
and fallback reason.

## Eval Suite

The eval suite runs the investigation workflow against five seeded incident
scenarios:

- checkout retry regression
- enterprise churn wave
- usage drop after import outage
- support backlog export bug
- payment method expiration

Each eval case stores the expected root cause, expected evidence types, expected
evidence markers, false leads, and recommended actions. Scoring persists:

- root-cause accuracy: exact normalized match against the seeded expected root cause
- citation quality: coverage of expected evidence types (`sql`, `document`, `ticket`) and seeded evidence markers
- action safety: expected mock actions must be produced, high-risk actions must remain pending approval, and low-risk actions may execute
- latency: wall-clock milliseconds for the scenario run
- pass/fail: scenario passes when the run succeeds, root cause matches, citation coverage is sufficient, and action safety holds

Run the suite from CLI:

```bash
cd apps/api
python -m app.evals.runner --json
```

Or from API:

```bash
# Set this in the API process environment before starting the server.
EVAL_RUN_TOKEN=dev-eval-token
curl -X POST http://localhost:8000/evals/run \
  -H "X-Eval-Run-Token: ${EVAL_RUN_TOKEN}"
curl http://localhost:8000/evals/results
```

`POST /evals/run` is disabled unless `EVAL_RUN_TOKEN` is configured. The CLI
runner remains the preferred local path because it cannot be triggered by an
anonymous HTTP caller. The frontend Run Suite button uses the same
`EVAL_RUN_TOKEN` from the web process environment.

The frontend report is available at `http://localhost:3000/evals` and shows
scenario results, failures, trace links, and example outputs.

Known failures and limitations:

- Current seeded run result: all five eval scenarios pass in the local deterministic suite.
- Without Langfuse or LangSmith credentials, traces are local identifiers rather than hosted trace pages.
- Without `LANGFUSE_PROJECT_ID`, Langfuse runs persist a `langfuse://traces/<trace_id>` identifier instead of a clickable UI URL.
- Hosted traces redact raw evidence payloads by default; enable `OBSERVABILITY_FULL_PAYLOADS=true` only when the seeded synthetic data export is intentional.
- The current regression scorer uses deterministic exact root-cause matching; it does not yet grade semantically equivalent wording.
- The suite is seeded and local-first; it does not create hosted Langfuse datasets or LangSmith experiments yet.

Postgres must have pgvector available because Alembic creates a `vector(96)`
embedding column and HNSW cosine index. Docker Compose uses the
`pgvector/pgvector:pg16` image for this.

## Frontend Development

```bash
cd apps/web
npm install
npm run dev
```

Knowledge search is available at `http://localhost:3000/knowledge` after the
API database has been migrated and seeded or after `POST /documents/ingest`.

## Verification

Run the backend behavior tests:

```bash
cd apps/api
python -m pytest
```

If you use an existing local virtualenv on this machine:

```bash
cd apps/api
.venv/bin/python -m pytest
```

Run the frontend contract/type/build checks:

```bash
cd apps/web
npm test
npm run lint
npm run build
```

Quick API checks after `docker compose up --build`:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/metrics/dashboard
curl http://localhost:8000/metrics/anomalies
curl http://localhost:8000/incidents
curl -X POST http://localhost:8000/evals/run -H "X-Eval-Run-Token: ${EVAL_RUN_TOKEN}"
curl http://localhost:8000/evals/results
```

Before asking for external review, capture the results of:

- Backend tests.
- Frontend tests, typecheck/lint, and production build.
- Docker boot from a fresh or intentionally reset local database.
- Browser inspection of the dashboard, incident detail flow, and knowledge search.
- Eval report inspection, including one passing trace and one edge-case scenario trace.
- Any known gaps against `prd.md`.

## Initial Inspection Guide

Inspect the project as a vertical product slice first. Promote it to full MVP inspection only after the PRD success criteria are implemented and verified.

1. Confirm the stack boots from a clean local database.
   - `docker compose up --build` should start Postgres, Redis, API, and web.
   - The API startup should run migrations and seed the demo data automatically.
   - `/ready` should report that database connectivity is working.

2. Review the dashboard at http://localhost:3000.
   - Confirm the UI shows current MRR, MRR delta, failed invoices, ticket volume, active users, churn, and detected revenue anomalies.
   - Any seeded anomaly shown in the UI should point to a clear metric movement and affected accounts.

3. Open the incident detail flow.
   - Use the detected anomaly's "View incident" or "Open incident" action.
   - Check that the incident page shows metric evidence, failed invoice IDs, affected accounts, support signals, product signals, and source query descriptions.
   - Treat this as structured incident evidence, not yet as a generated agent final report.

4. Review knowledge search at http://localhost:3000/knowledge.
   - Search for `retry webhook failed renewals`.
   - Confirm results include source IDs, chunk IDs, headings, source paths, snippets, and scores.

5. Review seed scenario quality.
   - Find the scenario definitions and their integrity tests.
   - Verify each scenario has accounts, tickets or other source evidence, expected evidence, false leads, and recommendations.
   - Check whether scenarios beyond the main demo path are rich enough for future evals.

6. Review safety boundaries.
   - Demo data endpoints are restricted to local/test/development/demo environments.
   - The document ingestion HTTP endpoint is disabled unless `DOCUMENT_INGEST_TOKEN` is configured.
   - The project still needs approval requests and mock action workflows before it can demonstrate approval-gated actions.

Important inspection questions:

- Does every visible operational claim have a concrete source in SQL results, support tickets, product events, incidents, or knowledge documents?
- Are failure modes visible enough for a reviewer to diagnose missing data, bad evidence, or failed tools?
- Is the seed data realistic enough, or is the root cause too obvious?
- Does the UI help a reviewer audit the investigation, rather than merely presenting decorative metrics?
- Are incomplete MVP areas clearly described so reviewers do not mistake the current slice for a finished agent?

## MVP Completion Gates

Use `prd.md` as the durable roadmap. This section defines completion gates that should remain stable even as implementation details change.

The project is not MVP-complete until all of these are true:

- At least five seeded incident scenarios exist and are covered by integrity tests.
- At least four of five eval scenarios resolve to the intended root cause.
- Final reports cite SQL results, support tickets, knowledge documents, or incident records for every major claim.
- Risky actions create approval requests or mock actions and cannot bypass approval/rejection.
- Every agent run records step logs, trace links, token/cost estimates, tool failures, and a final report.
- The UI exposes the primary investigation workspace: anomaly summary, evidence, affected accounts, run timeline, approval queue, and final report.
- The narrowest relevant backend tests, frontend checks, and browser smoke tests pass.

When a feature lands, prefer updating tests and PRD-aligned evidence over maintaining a long README checkbox list. If a separate task tracker exists, keep detailed status there rather than in this file.

## Documentation Maintenance

When making substantive product or architecture changes:

- Update `prd.md` if the product contract changes.
- Update this README only when setup, verification, inspection, or readiness criteria change.
- Avoid adding fine-grained progress checklists here; they go stale quickly.
- Keep examples runnable from a fresh checkout.
