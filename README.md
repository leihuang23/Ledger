# Ops Agent

Ops Agent is a production-shaped SaaS revenue and support investigation workspace. The final product will help an operator answer a prompt like: "MRR dropped this week. Investigate the cause, identify affected accounts, cite evidence, recommend actions, and draft follow-ups."

The product is intentionally not a toy chatbot. Its core promise is an auditable agent loop that combines revenue analytics, product usage, support tickets, knowledge documents, controlled action drafts, approval gates, run traces, and evals. Every important claim should be backed by cited evidence from queried data or retrieved documents.

This repository currently contains the initial monorepo spine: FastAPI backend, Next.js frontend, PostgreSQL, Redis, SQLAlchemy 2, Alembic, Pydantic v2, Docker Compose, and a backend smoke test.

## Product Direction

The intended workflow:

1. Detect or select a revenue anomaly.
2. Investigate billing, account, product usage, support, and incident evidence.
3. Produce a concise root-cause report with citations.
4. Identify affected accounts and recommended actions.
5. Draft follow-ups as approval-gated mock actions.
6. Record run steps, traces, cost estimates, failure modes, and eval results.

The first version should prioritize evidence quality, eval correctness, and approval safety over a large integration surface.

## Structure

- `apps/api` - FastAPI app with Pydantic v2 settings, SQLAlchemy 2 engine/session setup, Alembic, and pytest.
- `apps/web` - Minimal Next.js App Router UI wired to the backend health endpoint.
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
6,000 product events, 240 support tickets, and 1 open incident. Re-running it
should produce the same counts and fingerprint.

## Frontend Development

```bash
cd apps/web
npm install
npm run dev
```

## Smoke Test

The initial smoke test verifies that `GET /health` returns a stable response:

```bash
cd apps/api
pytest tests/test_health.py
```

## Roadmap

- [x] Create the initial FastAPI + Next.js + Postgres + Redis monorepo.
- [x] Add Docker Compose, Pydantic v2 settings, SQLAlchemy 2 setup, Alembic, and a backend smoke test.
- [ ] Add initial database models for accounts, subscriptions, invoices, product events, support tickets, knowledge documents, incidents, agent runs, approval requests, mock actions, and evals.
- [ ] Create at least 5 seeded incident scenarios with root causes, affected accounts, expected evidence, false leads, and expected recommendations.
- [ ] Add scenario integrity tests so seed data stays internally consistent.
- [ ] Implement revenue metrics and anomaly endpoints.
- [ ] Implement support-ticket and account-detail endpoints.
- [ ] Add document ingestion and retrieval with cited excerpts.
- [ ] Build the LangGraph investigation workflow with explicit intermediate artifacts.
- [ ] Persist agent run steps, evidence bundles, token/cost estimates, and final reports.
- [ ] Add approval-gated mock actions for Slack, email, task creation, and CRM updates.
- [ ] Build an operational investigation UI: anomaly summary, evidence panel, affected accounts, run timeline, approval queue, and final report.
- [ ] Add eval cases and require at least 4 of 5 seeded scenarios to resolve to the intended root cause.
- [ ] Add LangSmith tracing once the investigation workflow exists.
- [ ] Add frontend smoke/e2e coverage for the primary investigation flow.
