# Human-Assisted Follow-Ups

These items need credentials, external services, or an operator-owned deployment target. They are not required for the local PRD MVP behavior, but they are useful before a public portfolio review.

## Hosted Observability

- Configure either Langfuse or LangSmith credentials in the API runtime.
- Run one seeded investigation and one eval suite with hosted tracing enabled.
- Confirm trace URLs open in the hosted provider and contain only approved synthetic/demo metadata unless `OBSERVABILITY_FULL_PAYLOADS=true` is intentionally set.

## Deployment Verification

- Deploy the frontend to Vercel or an equivalent web host.
- Deploy the backend to Render, Fly, Railway, or an equivalent service with Postgres/pgvector and Redis configured.
- Run Alembic migrations and seed only the synthetic demo dataset.
- Confirm the deployed frontend can reach the deployed backend through `NEXT_PUBLIC_API_BASE_URL` and server-side `API_INTERNAL_BASE_URL`.

## Live Browser Review

Local automated checks cover source-level reviewer UI contracts, and the app can be
smoke-tested against a temporary seeded local API by fetching rendered pages. They
do not replace an operator-owned visual browser pass.

- Open the deployed or Docker Compose stack in a real browser.
- Walk through:
  1. Dashboard anomaly list.
  2. Incident detail evidence.
  3. Investigation run report.
  4. Cited evidence panel.
  5. Approval approve/reject actions.
  6. Eval report.
- Capture screenshots for a portfolio README or demo notes if the project will be shared publicly.

## Optional Stack Parity Decisions

The PRD lists Celery, Tailwind CSS, shadcn/ui, and an OpenAI-first LLM provider layer as recommended stack items. The local MVP proves the agentic investigation loop without depending on those pieces. Before adding them, decide whether they improve the demo or create infrastructure weight without improving the evidence, eval, approval, or trace story.
