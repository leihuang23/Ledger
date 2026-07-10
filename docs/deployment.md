# Deployment Guide

The tracked production-shaped topology uses Vercel for the Next.js frontend and a Render Blueprint for the FastAPI API, Celery worker, Postgres/pgvector, and Redis-compatible Key Value service. It is a deployment template, not proof that a public environment currently exists.

The provider configuration follows the current official [Render Blueprint specification](https://render.com/docs/blueprint-spec), [Render pgvector support](https://render.com/docs/postgresql-extensions), and [Vercel monorepo guidance](https://vercel.com/docs/monorepos).

## 1. Create the Render backend

1. Connect the repository to Render and create a Blueprint from `render.yaml`.
2. Review the instance plans before applying the Blueprint. The worker cannot use Render's free web-service plan, and provider pricing changes independently of this repository.
3. Enter `BACKEND_CORS_ORIGINS` when Render prompts for it. Use the exact Vercel production origin, for example `https://ops-agent.example.com`; use a comma-separated list only when multiple trusted origins are required.
4. Let the API become ready at `/ready`. Its container runs Alembic and seeds a blank demo database under a Postgres advisory lock before Uvicorn starts.
5. Confirm the generated `DEMO_OPERATOR_TOKEN`, `EVAL_RUN_TOKEN`, and `DOCUMENT_INGEST_TOKEN` values in the API service. Do not commit or log them.

Why the Blueprint sets `ALLOW_UNSAFE_BOOTSTRAP_SEED=true`: a managed Postgres hostname is intentionally rejected by the local-only seed safety check. Startup seeding still occurs only when the accounts table is empty. Destructive CLI reseeding continues to require the explicit `--allow-destructive` flag.

The API accepts Render's `postgresql://` connection string and normalizes it to SQLAlchemy's installed `postgresql+psycopg://` driver. The container also honors Render's `PORT` value.

## 2. Create the Vercel frontend

1. Import the same repository as a Vercel project.
2. Set the project **Root Directory** to `apps/web`. Vercel will read `apps/web/vercel.json` and detect Next.js.
3. Add these server/environment variables in Preview and Production:

| Variable | Value |
| --- | --- |
| `API_INTERNAL_BASE_URL` | Public Render API base URL, such as `https://ops-agent-api.onrender.com` |
| `NEXT_PUBLIC_API_BASE_URL` | The same public Render API base URL |
| `OPERATOR_UI_ENABLED` | `false` for an anonymous public portfolio deployment |

Do **not** copy `DEMO_OPERATOR_TOKEN` or `EVAL_RUN_TOKEN` into an anonymous public Vercel project. The public UI remains read-only and every server action rejects before forwarding credentials.

For a private recording/reviewer deployment, first enable Vercel Deployment Protection or another real authentication boundary. Only then set `OPERATOR_UI_ENABLED=true` and copy the API service's generated `DEMO_OPERATOR_TOKEN` and `EVAL_RUN_TOKEN` as server-only variables. Never use a `NEXT_PUBLIC_` prefix.

4. Deploy the frontend, then update the Render API's `BACKEND_CORS_ORIGINS` to the final Vercel production origin if it changed.

## 3. Optional hosted providers

The default deployment uses deterministic local diagnosis, local embeddings, and local trace identifiers. To enable an external provider, set only the variables for that provider:

| Capability | Variables |
| --- | --- |
| OpenAI diagnosis | `LLM_PROVIDER=openai`, `LLM_MODEL`, `OPENAI_API_KEY` |
| Anthropic diagnosis | `LLM_PROVIDER=anthropic`, `LLM_MODEL`, `ANTHROPIC_API_KEY` |
| OpenAI embeddings | `EMBEDDING_PROVIDER=openai`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_API_KEY` |
| Langfuse | `OBSERVABILITY_PROVIDER=langfuse`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`, optional `LANGFUSE_PROJECT_ID` |
| LangSmith | `OBSERVABILITY_PROVIDER=langsmith`, `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` |

Keep `OBSERVABILITY_FULL_PAYLOADS=false` unless exporting the synthetic evidence payloads is an explicit decision.

## 4. Post-deploy verification

Run these checks without printing tokens:

1. `GET /health` returns a live process response.
2. `GET /ready` proves database connectivity.
3. The Vercel landing page loads revenue evidence and the Agents/Tools navigation.
4. The public web deployment shows the read-only banner and rejects direct server-action mutation attempts without changing API state.
5. In a separately protected operator deployment, a server action with the configured token can launch a run.
6. The Celery worker moves the run out of `queued` and records steps.
7. A high-risk action remains pending until an approval decision.
8. The good/degraded eval comparison shows at least one regression.
9. Browser response headers include CSP, frame protection, MIME sniffing protection, referrer policy, and a restrictive permissions policy.

## Environment variable reference

The complete no-secret samples live in `.env.example`, `apps/api/.env.example`, and `apps/web/.env.example`.

- `APP_ENV=demo` activates fail-closed mutation gates.
- `DEMO_OPERATOR_TOKEN` gates agent/version/tool/run/approval/mock-action mutations.
- `EVAL_RUN_TOKEN` separately gates eval execution.
- `OPERATOR_UI_ENABLED=false` keeps anonymous public Next.js deployments read-only; set it to `true` only behind deployment authentication for recording/operator sessions.
- `DOCUMENT_INGEST_TOKEN` gates HTTP knowledge ingestion.
- `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND` connect managed state.
- `BACKEND_CORS_ORIGINS` lists exact trusted browser origins.
- `RATE_LIMIT_MUTATIONS_PER_MINUTE` and `RATE_LIMIT_SEARCH_PER_MINUTE` bound public traffic.
- `LOG_FORMAT=json` is recommended for hosted log ingestion.
