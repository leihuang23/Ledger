# Human-Assisted Follow-Ups

These items need credentials, external services, or an operator-owned deployment target. They are not required for the local PRD MVP behavior, but they are useful before a public engineering review.

## Credential Setup Manual

Do not commit real `.env` files. For Docker Compose, copy the root `.env.example`
to `.env` and set credentials there. For separate local API/web processes, copy
`apps/api/.env.example` to `apps/api/.env` and `apps/web/.env.example` to
`apps/web/.env.local`.

### Demo Operator Writes

Use this when `APP_ENV=demo` and the reviewer should click protected UI actions
such as creating incidents, starting investigations, or approving/rejecting mock
actions.

```bash
DEMO_OPERATOR_TOKEN=<generate-a-long-random-token>
```

Set the same value for the API and web server. In Docker Compose, the root `.env`
now feeds both services. The web app reads this only in server actions and sends
it to the API as `X-Demo-Operator-Token`; never expose it through a
`NEXT_PUBLIC_` variable.

### LLM Diagnosis Provider

The local MVP runs with deterministic diagnosis by default:

```bash
LLM_PROVIDER=none
```

To test provider-backed synthesis with OpenAI:

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=<your-openai-api-key>
```

To test provider-backed synthesis with Anthropic:

```bash
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-haiku-latest
ANTHROPIC_API_KEY=<your-anthropic-api-key>
```

Provider output is still evidence-gated by the workflow. If a specific LLM root
cause does not match retrieved deterministic evidence, the run falls back and
records `unsupported_llm_diagnosis: deterministic_fallback` in trace metadata.

### OpenAI Embeddings

The default local hashing provider needs no key:

```bash
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=local-hashing-v1
```

To use OpenAI embeddings for document ingestion/search:

```bash
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=<your-openai-api-key>
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

If `EMBEDDING_PROVIDER=openai` is set without `OPENAI_API_KEY`, the API falls
back to local embeddings so the demo remains runnable.

### Hosted Observability

For Langfuse:

```bash
OBSERVABILITY_PROVIDER=langfuse
LANGFUSE_PUBLIC_KEY=<public-key>
LANGFUSE_SECRET_KEY=<secret-key>
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_PROJECT_ID=<project-id>
```

For LangSmith:

```bash
OBSERVABILITY_PROVIDER=langsmith
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<api-key>
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_PROJECT=ledger-local
LANGSMITH_WEB_URL=https://smith.langchain.com
```

Keep `OBSERVABILITY_FULL_PAYLOADS=false` unless you intentionally want hosted
traces to include synthetic evidence payloads.

### Rate Limiting

Rate limits require a reachable Redis broker and are enforced by `slowapi` on
mutating and search endpoints:

```bash
RATE_LIMIT_MUTATIONS_PER_MINUTE=10
RATE_LIMIT_SEARCH_PER_MINUTE=60
```

Raise these for a trusted reviewer environment; lower them for a public demo.

### Logging

```bash
LOG_LEVEL=INFO
LOG_FORMAT=text
```

Set `LOG_FORMAT=json` to emit single-line JSON logs with `request_id`, `run_id`,
and `incident_id` fields populated from the ASGI middleware and agent context.

### Operator-Triggered HTTP Utilities

These are optional tokens for mutating utility endpoints:

```bash
DOCUMENT_INGEST_TOKEN=<random-token-for-post-documents-ingest>
EVAL_RUN_TOKEN=<random-token-for-post-evals-run>
```

`EVAL_RUN_TOKEN` must be available to both API and web if the reviewer should run
the eval suite from `/evals`. `DOCUMENT_INGEST_TOKEN` is API-only.

### Stripe Test-Mode Evidence Adapter

Optional. Set `STRIPE_API_KEY` (`sk_test_...` only; live keys are rejected at
settings load) and `STRIPE_WEBHOOK_SECRET` in the API process to enable signed
webhook ingest and bounded reconciliation. Routes are disabled for
`APP_ENV=demo` and secrets must never use a `NEXT_PUBLIC_` prefix. Full setup
and endpoint reference: [docs/deployment.md](deployment.md#optional-stripe-test-mode-evidence-adapter).

## Hosted Observability

- Configure either Langfuse or LangSmith credentials in the API runtime.
- Run one seeded investigation and one eval suite with hosted tracing enabled.
- Confirm trace URLs open in the hosted provider and contain only approved synthetic/demo metadata unless `OBSERVABILITY_FULL_PAYLOADS=true` is intentionally set.

## Deployment Verification

- Deploy the frontend to Cloudflare Workers with the tracked OpenNext config.
- Deploy the backend to Render with Postgres/pgvector and Redis configured.
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
- Capture screenshots for the README or demo notes if the project will be shared publicly.

## Optional Stack Parity Decisions

The original product brief listed Tailwind CSS, shadcn/ui, and an OpenAI-first
LLM provider layer as recommended stack items. Celery is already used for async
investigation and eval runs. The local MVP proves the agentic investigation loop
without Tailwind, shadcn/ui, or an external LLM. Before adding them, decide
whether they improve the demo or create infrastructure weight without improving
the evidence, eval, approval, or trace story. AGENTS.md keeps the dependency
rule: add a dependency only when a real implementation need appears.
