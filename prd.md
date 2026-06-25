## Recommended Stack

- Frontend: Next.js, TypeScript, Tailwind CSS, shadcn/ui.
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

## PRD: SaaS Revenue and Support Ops Agent

### Problem

Most AI agent demos are too toy-like. This project should prove that you can build a production-shaped agent that investigates a realistic business incident across structured data, unstructured docs, support tickets, and controlled actions.

### Primary Demo Prompt

"MRR dropped this week. Investigate the cause, identify affected accounts, cite evidence, recommend actions, and draft follow-ups."

### Target User

- Founder, operations lead, support lead, product manager, or revenue operations analyst at a SaaS company.

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

### Out of Scope

- Real customer data.
- Real Stripe, Slack, Gmail, or CRM integrations in the first version.
- Fully autonomous write actions without approval.
- Fine-tuning.
- Voice interface.

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
- Every final report includes evidence from SQL queries, tickets, or docs.
- Risky actions are blocked until approved.
- Every agent run has a trace, step log, token/cost estimate, and final report.
