# PRD Completion Plan

## Goal

Complete the local MVP contract in `prd.md` for the SaaS Revenue and Support Ops Agent, with evidence that the implemented app satisfies the PRD success criteria and exposes the listed API surface.

## Acceptance Criteria

1. The core API routes listed in `prd.md` are present in the FastAPI OpenAPI schema:
   - `GET /health`
   - `GET /metrics/revenue`
   - `GET /metrics/anomalies`
   - `GET /accounts/{account_id}`
   - `GET /support/tickets`
   - `POST /documents/ingest`
   - `POST /incidents`
   - `POST /agent/investigations`
   - `GET /agent/runs/{run_id}`
   - `POST /approvals/{approval_id}/approve`
   - `POST /approvals/{approval_id}/reject`
   - `POST /evals/run`
2. `GET /metrics/revenue` returns the same revenue metric contract as the existing MRR metrics endpoint.
3. `GET /accounts/{account_id}` returns account, subscription, invoice, support ticket, and product-event context for seeded accounts.
4. `GET /support/tickets` lists support tickets with deterministic ordering and useful filters for account, status, category, and scenario.
5. The reviewer UI has smoke coverage for the major screens needed to inspect anomaly evidence, run reports, approvals, citations, and eval results.
6. The full backend test suite, frontend tests, frontend typecheck, and frontend production build pass after the changes.

## Implementation Steps

1. Add failing backend contract tests for the missing PRD routes.
2. Implement small, focused routers and schemas for accounts and support tickets.
3. Add a revenue route alias that keeps `/metrics/mrr` intact while satisfying `/metrics/revenue`.
4. Add a lightweight frontend smoke test that renders the main page components by checking static page source for reviewer-facing labels.
5. Document human-assisted follow-ups for hosted services and deployment verification.
6. Run verification:
   - `cd apps/api && .venv/bin/python -m pytest`
   - `cd apps/web && npm test`
   - `cd apps/web && npm run lint`
   - `cd apps/web && npm run build`

## Human-Assisted Items

Hosted Langfuse/LangSmith verification, Vercel/backend deployment, and real browser inspection of a deployed stack require credentials or external services. They are tracked separately in `docs/human-assistance.md`.
