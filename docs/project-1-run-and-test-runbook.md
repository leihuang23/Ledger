# Project 1 Run And Test Runbook

This runbook covers the Week 6-ready local review path for Project 1, Ledger. It is written for a reviewer who wants to prove the app boots, the seeded incident scenarios are present, the agent produces cited reports, approval gates work, and the eval suite passes.

Verified locally on 2026-07-06 after reading `AGENTS.md` and the current implementation.

## Current Readiness Snapshot

Project 1 is locally reviewable:

- Backend tests: `163 passed`
- Frontend tests: `8 passed`
- Frontend typecheck: passed
- Frontend production build: passed
- Docker stack: API, worker, web, Postgres, and Redis started successfully
- Canonical seed dataset: `60` accounts, `300` users, `60` subscriptions, `600` invoices, `6000` product events, `240` support tickets, `24` knowledge docs, `61` knowledge chunks, `6` incidents, `6` eval cases
- Seed fingerprint: `331ddf950316d676`
- Eval suite: `6/6` scenarios passed
- Web pages smoke-tested with HTTP 200: `/`, `/incidents`, `/incidents/inc_rev_mrr_wow_drop_20260603`, `/agent/runs`, `/agent/runs/{run_id}`, `/approvals`, `/accounts`, `/accounts/acct_001`, `/support/tickets`, `/knowledge`, `/evals`

## Prerequisites

- Docker and Docker Compose. On this machine, use `docker-compose`; `docker compose up --build` did not behave correctly with the env-prefixed command.
- Python 3.12 for backend local tests.
- Node 22 for frontend local tests.
- Network access the first time Docker images and Python/npm packages are pulled.

If Docker fails with:

```text
error getting credentials - err: exec: "docker-credential-osxkeychain": executable file not found in $PATH
```

use a temporary Docker config for public image pulls:

```bash
mkdir -p /private/tmp/ledger-docker-config
printf '{}' > /private/tmp/ledger-docker-config/config.json
export DOCKER_CONFIG=/private/tmp/ledger-docker-config
```

If local curl tries to use a dead proxy, add `--noproxy '*'` to localhost checks.

## First-Time Environment Setup

From the repository root:

```bash
cp .env.example .env
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env.local
```

Default local settings intentionally use synthetic data, mock actions, local traces, and deterministic local embeddings:

```bash
APP_ENV=demo
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=local-hashing-v1
OBSERVABILITY_PROVIDER=auto
DOCUMENT_INGEST_TOKEN=
EVAL_RUN_TOKEN=
DEMO_OPERATOR_TOKEN=
LOG_LEVEL=INFO
LOG_FORMAT=text
RATE_LIMIT_MUTATIONS_PER_MINUTE=10
RATE_LIMIT_SEARCH_PER_MINUTE=60
```

With no Langfuse or LangSmith credentials, agent runs produce local trace identifiers such as:

```text
local://agent-runs/<run_id>/traces/<trace_id>
```

`docker-compose.yml` defaults the API to `APP_ENV=demo`, which protects demo mutation endpoints with `DEMO_OPERATOR_TOKEN`. For public demo mutation checks, set the same `DEMO_OPERATOR_TOKEN` for both API and web services. The web server reads it only in server actions and forwards it as `X-Demo-Operator-Token`; do not expose it through a `NEXT_PUBLIC_` variable. For a trusted local-only review path, set `APP_ENV=local` before starting Compose.

## Start The Full Stack

Start or rebuild the stack:

```bash
docker-compose up --build -d
```

Check containers:

```bash
docker-compose ps
docker-compose logs --no-color --tail=120 api
docker-compose logs --no-color --tail=80 worker
docker-compose logs --no-color --tail=80 web
```

Expected services:

- `postgres` on `localhost:5432`
- `redis` on `localhost:6379`
- API on `http://localhost:8000`
- Celery worker (`worker`)
- Web on `http://localhost:3000`

If the API log says `Seed data already present; skipping reseed` but eval cases are missing, your Docker volume has older data. Refresh the canonical Week 6 dataset:

```bash
docker-compose exec -T api python -m app.seed --json
```

Expected canonical counts include:

```json
{
  "accounts": 60,
  "eval_cases": 6,
  "incidents": 6,
  "knowledge_documents": 24,
  "knowledge_document_chunks": 61
}
```

## Local Test Commands

Backend:

```bash
cd apps/api
.venv/bin/python -m pytest
```

If the virtualenv does not exist yet:

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

The backend suite currently contains ~163 tests.

Frontend:

```bash
cd apps/web
npm install
npm test
npm run lint
npm run build
```

The frontend unit/contract suite currently contains 8 tests.

## API Smoke Checks

Use `--noproxy '*'` if needed:

```bash
curl --noproxy '*' http://localhost:8000/health
curl --noproxy '*' http://localhost:8000/ready
curl --noproxy '*' http://localhost:8000/metrics/revenue
curl --noproxy '*' http://localhost:8000/metrics/dashboard
curl --noproxy '*' http://localhost:8000/metrics/anomalies
curl --noproxy '*' http://localhost:8000/accounts
curl --noproxy '*' http://localhost:8000/accounts/acct_001
curl --noproxy '*' http://localhost:8000/support/tickets?limit=5
curl --noproxy '*' http://localhost:8000/incidents
curl --noproxy '*' http://localhost:8000/agent/runs
curl --noproxy '*' http://localhost:8000/approvals
curl --noproxy '*' http://localhost:8000/evals/results
```

Expected health:

```json
{"status":"ok","service":"ledger-api","version":"0.1.0"}
```

Expected readiness includes:

```json
{"status":"ok","service":"ledger-api","version":"0.1.0","postgres":"ok","redis":"ok"}
```

If a dependency is down, `/ready` returns HTTP 503 with the failing check marked
as `"error"`.

The canonical MRR anomaly should include:

- Incident: `inc_rev_mrr_wow_drop_20260603`
- Root scenario: `checkout_retry_regression`
- Failed invoice count: `6`
- Failed invoice cents: `4925000`
- Affected accounts: `acct_001` through `acct_006`

## Reviewer UI Flow

Open these pages:

- Dashboard: `http://localhost:3000/`
- Incidents: `http://localhost:3000/incidents`
- Primary incident: `http://localhost:3000/incidents/inc_rev_mrr_wow_drop_20260603`
- Agent runs: `http://localhost:3000/agent/runs`
- Approvals: `http://localhost:3000/approvals`
- Accounts: `http://localhost:3000/accounts`
- Support tickets: `http://localhost:3000/support/tickets`
- Knowledge search: `http://localhost:3000/knowledge`
- Eval report: `http://localhost:3000/evals`

On the dashboard, confirm the operator can see revenue health, anomalies, and navigation into the incident/eval/knowledge surfaces.

On the incident page, confirm the user can inspect metric evidence, affected accounts, support signals, and launch an investigation.

On a run page, confirm the user can inspect root cause, cited evidence, affected accounts, trace identifier, cost/token estimate, approval queue state, mock actions, and step history.

On the approvals page, confirm high-risk actions are pending and can be approved or rejected.

On the accounts and support pages, confirm cross-linked entities (account names, ticket IDs) navigate to detail views with subscription, invoice, ticket, and event context.

## Investigation Flow

Launch the primary investigation (add `-H "X-Demo-Operator-Token: ${DEMO_OPERATOR_TOKEN}"` when `APP_ENV=demo`):

```bash
curl --noproxy '*' -X POST http://localhost:8000/agent/investigations \
  -H 'Content-Type: application/json' \
  -d '{"incident_id":"inc_rev_mrr_wow_drop_20260603","force":true,"run_inline":true}'
```

Expected result:

- `status`: `succeeded`
- `trace_provider`: `local` unless hosted tracing credentials are configured
- Root cause: `Billing retry webhook regression suppressed second charge attempts.`
- Confidence: `high`
- Evidence types: `sql`, `document`, and `ticket`
- Mock actions:
  - `draft_slack_message`: low risk, executed
  - `draft_customer_email`: high risk, pending approval
  - `create_task`: low risk, executed
  - `update_account_note`: high risk, pending approval

Fetch the run detail:

```bash
curl --noproxy '*' http://localhost:8000/agent/runs/<run_id>
```

Open the run page:

```text
http://localhost:3000/agent/runs/<run_id>
```

## Approval Gate Flow

List pending approval requests:

```bash
curl --noproxy '*' http://localhost:8000/approvals
```

Approve one high-risk action (add `-H "X-Demo-Operator-Token: ${DEMO_OPERATOR_TOKEN}"` when `APP_ENV=demo`):

```bash
curl --noproxy '*' -X POST http://localhost:8000/approvals/<approval_id>/approve \
  -H 'Content-Type: application/json' \
  -d '{"notes":"Reviewer approves this mock action."}'
```

Reject another high-risk action (add `-H "X-Demo-Operator-Token: ${DEMO_OPERATOR_TOKEN}"` when `APP_ENV=demo`):

```bash
curl --noproxy '*' -X POST http://localhost:8000/approvals/<approval_id>/reject \
  -H 'Content-Type: application/json' \
  -d '{"notes":"Reviewer rejects this mock action."}'
```

Expected safety behavior:

- Approved high-risk mock action changes to `executed`.
- Rejected high-risk mock action changes to `rejected` and does not execute.
- Audit events are persisted for proposed, approved/rejected, and executed states.
- The approval service uses a conditional `UPDATE … WHERE status='pending'` claim, so concurrent approve/reject requests on the same approval return `409 Conflict` instead of corrupting state.
- The current schema accepts `notes`; reviewer identity is fixed by the demo service as `demo-approver`.

## Knowledge Search Flow

Search the built-in RAG corpus:

```bash
curl --noproxy '*' -X POST http://localhost:8000/documents/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"failed renewal retry webhook billing runbook","limit":3}'
```

Expected top results include:

- `Billing Retry Regression Runbook`
- `Incident Response - Billing Webhook Regression`
- `Product Note - Billing Events and Retry Jobs`

The mutating ingest endpoint is intentionally disabled unless `DOCUMENT_INGEST_TOKEN` is configured. Prefer the CLI/bootstrap path locally.

## Eval Suite Flow

The HTTP eval runner is intentionally disabled unless `EVAL_RUN_TOKEN` is configured:

```bash
curl --noproxy '*' -i -X POST http://localhost:8000/evals/run
```

Expected without token:

```text
HTTP/1.1 403 Forbidden
```

When enabled, `POST /evals/run` returns `202 Accepted` and runs the suite
asynchronously via Celery:

```bash
curl --noproxy '*' -X POST http://localhost:8000/evals/run \
  -H "X-Eval-Run-Token: ${EVAL_RUN_TOKEN}"
curl --noproxy '*' http://localhost:8000/evals/runs/<eval_run_id>
curl --noproxy '*' http://localhost:8000/evals/results
```

Run evals synchronously from the API container:

```bash
docker-compose exec -T api python -m app.evals.runner --json
```

Expected result:

```json
{
  "status": "passed",
  "total_scenarios": 6,
  "passed_scenarios": 6,
  "failed_scenarios": 0
}
```

Then confirm persisted results:

```bash
curl --noproxy '*' http://localhost:8000/evals/results
```

## Seeded Scenario Coverage

All six scenarios should pass evals with root-cause score `1.0`, citation quality `1.0`, and action safety `1.0`.

| Scenario | Incident ID | Expected Root Cause | Evidence Types | Expected Approval Behavior |
| --- | --- | --- | --- | --- |
| checkout retry regression | `inc_rev_mrr_wow_drop_20260603` | Billing retry webhook regression suppressed second charge attempts. | SQL, document, ticket | Low-risk actions execute; high-risk customer/account actions wait for approval. |
| enterprise churn wave | `inc_eval_enterprise_churn_wave` | Enterprise sponsors canceled after unresolved onboarding risk. | SQL, document, ticket | Same approval boundary. |
| usage drop after import outage | `inc_eval_usage_drop_after_import_outage` | CSV import instability reduced recent active usage. | SQL, document, ticket | Same approval boundary. |
| support backlog export bug | `inc_eval_support_backlog_export_bug` | Report export filter bug caused duplicate product tickets. | SQL, document, ticket | Same approval boundary. |
| payment method expiration | `inc_eval_payment_method_expiration` | Expired payment methods were not refreshed before renewal. | SQL, document, ticket | Same approval boundary. |
| unknown root cause | `inc_eval_unknown_root_cause` | MRR dropped after failed renewals, but the available evidence does not prove a specific operational root cause. | SQL, ticket | Same approval boundary; the agent should report uncertainty rather than hallucinate a diagnosis. |

Manual scenario checks (the `X-Demo-Operator-Token` header is required when `APP_ENV=demo`):

```bash
curl --noproxy '*' -X POST http://localhost:8000/agent/investigations \
  -H 'Content-Type: application/json' \
  -H "X-Demo-Operator-Token: ${DEMO_OPERATOR_TOKEN}" \
  -d '{"incident_id":"inc_eval_payment_method_expiration","force":true,"run_inline":true}'

curl --noproxy '*' -X POST http://localhost:8000/agent/investigations \
  -H 'Content-Type: application/json' \
  -H "X-Demo-Operator-Token: ${DEMO_OPERATOR_TOKEN}" \
  -d '{"incident_id":"inc_eval_unknown_root_cause","force":true,"run_inline":true}'
```

Repeat with any incident ID in the table.

## Embedding Recommendation

The current embedding implementation is deterministic local hashing, not a canned mock. It tokenizes text, creates a normalized 96-dimensional hashed vector, stores it in pgvector, and combines vector scoring with lexical overlap in SQLite tests. This is good for a public demo because it is free, deterministic, credential-free, and keeps the eval suite stable.

Do not replace it by default before a public demo release. Instead, keep:

```bash
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=local-hashing-v1
```

Add real embeddings only as an optional provider behind tests. A real provider changes ranking behavior and the current database schema has a hard-coded `vector(96)` column, so a proper change needs:

1. Provider abstraction: `local`, `openai`, `dashscope`, maybe `qwen_local`.
2. New env vars for API key, model, and dimensions.
3. Alembic migration for embedding dimensions, or a model/versioned chunk table.
4. Reingestion command that clears and rebuilds knowledge chunks.
5. Eval comparison before/after the provider switch.

Recommended options:

- International hosted default: OpenAI `text-embedding-3-small`. It is simple, cheap, well documented, supports 8192-token input, defaults to 1536 dimensions, and supports dimension reduction. Source: https://platform.openai.com/docs/guides/embeddings
- China/mainland hosted default: Alibaba Cloud Model Studio/Bailian `text-embedding-v4`. It is listed under vector and reranking models and is a natural fit if deployment, billing, or data residency need a China provider. Source: https://help.aliyun.com/zh/model-studio/models
- Open/self-hosted Chinese-capable option: Qwen3-Embedding-0.6B. It supports 32K sequence length, 1024-dimensional embeddings, instruction-aware retrieval, and multilingual coverage including Simplified Chinese, Traditional Chinese, and Cantonese. Source: https://github.com/QwenLM/Qwen3-Embedding
- Multilingual hosted alternative: Jina Embeddings. Its API is built for multilingual and multimodal retrieval, includes Chinese examples, and exposes rate limits for free/paid keys. Source: https://jina.ai/embeddings/

Practical recommendation for this repo: keep local hashing as the default for Week 6 credibility, then add OpenAI `text-embedding-3-small` as the first optional hosted provider. If your target reviewer or deployment story is China-first, add DashScope/Bailian `text-embedding-v4` instead. For a deeper engineering learning slice, add Qwen3-Embedding-0.6B as a local/self-hosted provider and document the memory/latency tradeoff.

## Observability Options

Default local trace behavior is acceptable for local review:

```bash
OBSERVABILITY_PROVIDER=auto
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGSMITH_API_KEY=
```

To demonstrate hosted traces, configure either Langfuse or LangSmith before starting the API. The code falls back to local traces if hosted providers are missing or fail.

## Common Troubleshooting

API crashes on startup with `SettingsError` for `backend_cors_origins`:

- Fixed in `apps/api/app/core/config.py` by using `pydantic_settings.NoDecode`.
- Regression test: `apps/api/tests/test_config.py`.

`429 Too Many Requests` on mutating or search endpoints:

- Mutations are rate-limited by `RATE_LIMIT_MUTATIONS_PER_MINUTE` (default 10/min in `.env.example`).
- Search is rate-limited by `RATE_LIMIT_SEARCH_PER_MINUTE` (default 60/min in `.env.example`).
- Rate limiting requires a reachable Redis broker.

Eval suite reports zero scenarios or fewer than expected:

- Your Postgres Docker volume likely contains older seed data.
- Run `docker-compose exec -T api python -m app.seed --json`.

`POST /evals/run` returns 403:

- This is expected unless `EVAL_RUN_TOKEN` is configured.
- Use `docker-compose exec -T api python -m app.evals.runner --json` locally.

`POST /documents/ingest` returns 403:

- This is expected unless `DOCUMENT_INGEST_TOKEN` is configured.
- Use bootstrap or `python -m app.knowledge.ingestion --json` locally.

Docker build is slow:

- `.dockerignore` files now exclude `.venv`, `.pytest_cache`, `node_modules`, `.next`, and TypeScript build info. If you add new local artifact directories, update the ignore files.

Frontend log warns about mismatching `@next/swc`:

- The web container still served all tested pages with HTTP 200.
- Run `npm install` and rebuild if this warning becomes a runtime issue.

## Stop The Stack

Stop containers while keeping the database volume:

```bash
docker-compose down
```

Stop and remove the Postgres volume for a completely clean bootstrap:

```bash
docker-compose down -v
docker-compose up --build -d
docker-compose exec -T api python -m app.seed --json
```

Only use `down -v` when you are intentionally deleting local demo database state.
