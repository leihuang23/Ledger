## Recommended Stack

- Frontend: Next.js, TypeScript, hand-rolled CSS with design tokens (`app/globals.css`).
  (Revised 2026-07-17: originally "Tailwind CSS, shadcn/ui"; the shipped UI uses
  hand-written CSS, which proved sufficient for the dense operational surfaces. Adopt
  Tailwind/shadcn only if a future surface genuinely needs them.)
- Backend: FastAPI, Pydantic v2, SQLAlchemy 2, Alembic.
- Data: PostgreSQL, pgvector, Redis.
- Agent orchestration: LangGraph.
- Observability: provider adapter with Langfuse as the recommended open-source
  provider, LangSmith as an optional adapter, and local trace identifiers as the
  deterministic fallback.
- Async jobs: Celery.
- LLM provider layer: OpenAI first, Anthropic-compatible abstraction later.
- Testing: pytest, Playwright, seeded local eval cases persisted in the app
  database; hosted Langfuse datasets or LangSmith experiments can be added later.
- Deployment: Docker Compose locally; Vercel for frontend; Render/Fly/Railway for backend.

## PRD: Ledger

Ledger is a production-shaped portfolio system built to demonstrate how an AI agent can investigate revenue anomalies, cite evidence, expose an auditable run trace, and gate risky actions.

It is also a forensic revenue and support operations agent for SaaS-shaped data: the product story is real engineering judgment on synthetic (and optional sandbox) evidence, not merchant adoption or live commerce.

### Problem

Most AI agent demos are too toy-like. This project should prove that you can build a production-shaped agent that investigates a realistic business incident across structured data, unstructured docs, support tickets, and controlled actions.

### Primary Demo Prompt

"MRR dropped this week. Investigate the cause, identify affected accounts, cite evidence, recommend actions, and draft follow-ups."

### Target User

- Founder, operations lead, support lead, product manager, or revenue operations analyst at a SaaS company.
- Hiring managers and technical reviewers evaluating evidence-backed agent engineering.

### Honest portfolio disclosure

- Business records and incident scenarios are synthetic unless explicitly labeled as Stripe test-mode sandbox data.
- Stripe, when present, operates only in test mode as an optional evidence source.
- The anonymous public deployment is read-only.
- The deterministic evidence classifier is the source of truth; an LLM may synthesize supported evidence when configured, but must not invent or override the supported conclusion.
- Slack, email, CRM, and task actions remain mocks behind approval boundaries.

### Core User Stories

1. As an ops lead, I want to detect a revenue anomaly, so that I can respond before it becomes a customer or cash-flow problem.
2. As an ops lead, I want the agent to query billing, product, and support data, so that the diagnosis is evidence-backed.
3. As a support lead, I want the agent to connect account impact with support tickets, so that follow-up is targeted.
4. As a reviewer, I want every claim to include citations or queried evidence, so that I can trust the report.
5. As an approver, I want risky actions to require approval, so that the agent cannot act beyond its authority.
6. As a hiring manager, I want to see traces, evals, and failure cases, so that I can judge engineering maturity.

### In Scope

- Seeded SaaS business dataset.
- Revenue and usage analytics.
- RAG over runbooks, pricing docs, incident docs, and support macros.
- LangGraph investigation agent.
- Mock tools for Slack, email, task creation, and CRM updates.
- Approval queue for sensitive actions.
- Audit log and run history.
- Provider-neutral tracing and local eval result persistence.
- Optional narrow Stripe **test-mode** evidence adapter (not a general merchant platform):
  - customers, subscriptions, and invoices ingestion only;
  - verified webhooks with idempotency and out-of-order protection;
  - bounded reconciliation for missed events;
  - a Stripe Test Clock failed-renewal scenario that feeds the same account/subscription/invoice evidence model used by investigations.

### Out of Scope

- Real customer data and live Stripe credentials.
- Stripe Checkout, refunds, payment collection, Connect, OAuth, or any Stripe write actions beyond ingestion/reconciliation into Ledger records.
- Real Slack, Gmail, CRM, or other third-party write integrations (actions stay mocks).
- Fully autonomous write actions without approval.
- Public anonymous controls that launch investigations, run evals, ingest documents, approve actions, or hold Stripe credentials.
- Multi-tenancy or a generic connector framework.
- Fine-tuning.
- Voice interface.

### Stripe evidence boundary (optional)

Stripe is an **optional evidence source**, not a merchant or payments product surface. When implemented:

- Use **test-mode only**; never store live credentials or real customer data.
- Keep Stripe outside investigation logic: normalize sandbox billing events into Ledger's existing account, subscription, and invoice model.
- Verify webhook signatures against the raw body; persist event IDs with uniqueness before apply; treat redelivery as a successful no-op; re-fetch current objects when order may stale embedded snapshots.
- Record ingestion failures and unsupported event types visibly.
- Keep all Stripe mutations and credentials outside the anonymous public deployment.
- The anonymous public demo remains read-only; `APP_ENV=demo` and existing operator / eval / ingest token gates are unchanged.

Every major report claim must still cite retrieved SQL, tickets, documents, incidents, or (when present) Stripe-derived billing evidence already normalized into Ledger records. Approval, audit, eval, and run-trace requirements below are non-negotiable whether or not Stripe is configured.

### Key Data Models

- accounts
- users
- subscriptions
- invoices
- product_events
- support_tickets
- knowledge_documents
- incidents
- agent_runs
- agent_run_steps
- approval_requests
- mock_actions
- eval_cases
- eval_results

### Core API Routes

- GET /health
- GET /metrics/revenue
- GET /metrics/anomalies
- GET /accounts/{account_id}
- GET /support/tickets
- POST /documents/ingest
- POST /incidents
- POST /agent/investigations
- GET /agent/runs/{run_id}
- POST /approvals/{approval_id}/approve
- POST /approvals/{approval_id}/reject
- POST /evals/run

### Success Criteria

- The app contains at least 5 seeded incident scenarios.
- The agent correctly identifies the root cause for at least 4 of 5 eval scenarios.
- Every final report includes evidence from SQL queries, tickets, docs, incidents, or (when present) Stripe-derived billing records already normalized into Ledger.
- Risky actions are blocked until approved.
- Every agent run has a trace, step log, token/cost estimate, and final report.
- When the optional Stripe adapter is enabled, sandbox events are authenticated, idempotent, reconcilable, covered by focused tests, and never use live credentials or real customer data.
