# Deployment Guide

The tracked production-shaped topology uses Vercel for the Next.js frontend and a Render Blueprint for the FastAPI API, Celery worker, Postgres/pgvector, and Redis-compatible Key Value service. It is a deployment template, not proof that a public environment currently exists.

**Public demo target origin:** `https://ledger.leihuang.me` (frontend) with the Render web service as the API origin (example hostname `https://ledger-api.onrender.com` after Blueprint create). If DNS uses a different hostname, update this document, `BACKEND_CORS_ORIGINS`, and the Vercel custom domain together so they stay exact matches.

The provider configuration follows the current official [Render Blueprint specification](https://render.com/docs/blueprint-spec), [Render pgvector support](https://render.com/docs/postgresql-extensions), and [Vercel monorepo guidance](https://vercel.com/docs/monorepos).

## 0. Captain checklist: anonymous public read-only demo

Use this when standing up or repairing the public read-only demo. Workers without Render/Vercel login cannot complete cloud steps; they can still prove the local `APP_ENV=demo` path (section 5).

### 0.1 DNS (Cloudflare for `leihuang.me`)

Public resolvers currently return **NXDOMAIN** for `ledger.leihuang.me` (root `leihuang.me` already resolves via Cloudflare). Until DNS exists, browsers and TLS clients fail; a local proxy fake-IP (for example `198.18.0.0/15`) can look like a broken TLS handshake rather than NXDOMAIN.

1. In Cloudflare DNS for `leihuang.me`, create `ledger` as a **CNAME** to the Vercel target (`cname.vercel-dns.com` or the value Vercel shows for the project domain).
2. Prefer DNS-only or follow Vercel’s Cloudflare guidance so certificate issuance succeeds.
3. Confirm from a clean network: `dig +short ledger.leihuang.me @1.1.1.1` returns Vercel addresses (not NXDOMAIN, not `198.18.x.x`).
4. Optional API hostname: either use the default `*.onrender.com` service URL or a separate CNAME if you add a custom domain on Render.

### 0.2 Render backend (Blueprint from `render.yaml`)

1. Authenticate to Render (dashboard or CLI). Install CLI only if non-interactive login is available.
2. Connect repo `leihuang23/Ledger` and apply the Blueprint in `render.yaml`.
3. Review paid plans (worker cannot use free web-only constraints; pricing is external to this repo).
4. Set **`BACKEND_CORS_ORIGINS`** to the exact browser origin only, for example:
   - `https://ledger.leihuang.me`
   - Comma-separated list only if you truly need more than one trusted origin.
5. Confirm generated server-only secrets on the API service (do not commit or paste into git/chat logs):
   - `DEMO_OPERATOR_TOKEN`
   - `EVAL_RUN_TOKEN`
   - `DOCUMENT_INGEST_TOKEN`
6. Confirm Blueprint defaults that must remain for public safety:
   - `APP_ENV=demo`
   - `LLM_PROVIDER=none` (or private operator override later)
   - `OBSERVABILITY_FULL_PAYLOADS=false`
   - No Stripe live/test secrets required for the anonymous public surface
7. Wait until `GET https://<api-host>/ready` returns 200. Startup runs Alembic and seeds an empty demo DB under an advisory lock.
8. Note the public API base URL for Vercel (`https://ledger-api.onrender.com` or the custom API host).

Why the Blueprint sets `ALLOW_UNSAFE_BOOTSTRAP_SEED=true`: a managed Postgres hostname is intentionally rejected by the local-only seed safety check. Startup seeding still occurs only when the accounts table is empty. Destructive CLI reseeding continues to require the explicit `--allow-destructive` flag.

The API accepts Render's `postgresql://` connection string and normalizes it to SQLAlchemy's installed `postgresql+psycopg://` driver. The container also honors Render's `PORT` value.

### 0.3 Vercel frontend (`apps/web`)

1. Import the same repository; set project **Root Directory** to `apps/web` (reads `apps/web/vercel.json`).
2. Add the custom domain `ledger.leihuang.me` and finish DNS as in 0.1.
3. Production / Preview env for the **anonymous public** project:

| Variable | Public demo value | Notes |
| --- | --- | --- |
| `API_INTERNAL_BASE_URL` | Public Render API base URL | Server-side fetch base |
| `NEXT_PUBLIC_API_BASE_URL` | Same public Render API base URL | Browser-visible API origin only; not a secret |
| `OPERATOR_UI_ENABLED` | `false` | Required for anonymous public demo |

4. **Do not set** on the anonymous public Vercel project:
   - `DEMO_OPERATOR_TOKEN`, `EVAL_RUN_TOKEN`, `DOCUMENT_INGEST_TOKEN`
   - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, Langfuse/LangSmith secrets
   - Any Stripe secret or publishable key
   - Any secret under a `NEXT_PUBLIC_*` name
5. Deploy Production, then re-check Render `BACKEND_CORS_ORIGINS` if the final origin differs.
6. Optional private recording deployment: enable Vercel Deployment Protection first, then a **separate** protected project/env may set `OPERATOR_UI_ENABLED=true` and server-only operator/eval tokens. Never on the public project.

### 0.4 Post-deploy public verification

From a machine that can reach the public hosts (not blocked by a local fake-IP proxy):

```bash
API_BASE_URL=https://<render-api-host> \
WEB_BASE_URL=https://ledger.leihuang.me \
STRICT_CORS=true \
EXPECTED_ORIGIN=https://ledger.leihuang.me \
REQUIRE_WEB=true \
  ./scripts/verify-public-demo.sh
```

Manual public verification:

1. Open `https://ledger.leihuang.me` without credentials; read-only banner is visible.
2. Dashboard shows seeded revenue anomalies/incidents; Agents/Tools navigation works.
3. Runs show pre-seeded completed investigations with ordered steps, citations, local traces, token/cost fields, a visible failed run, and a blocked-step run (permission enforcement).
4. Approvals show pending/approved/rejected states, and the eval studio shows a good-vs-degraded regression (5/6 vs 6/6); high-risk actions stay pending until an operator decision (operator path only).
5. Direct anonymous `POST` mutations against the API return 403 and do not change state (covered by the script).
6. Response headers include CSP, frame protection, MIME sniffing protection, referrer policy, restrictive permissions policy, and HSTS on HTTPS.
7. View-source / network: no operator tokens, model keys, or Stripe secrets in HTML, JS, or API JSON.

The demo database is seeded with these synthetic audit surfaces automatically in `APP_ENV=demo` (`app/seed.py`, `_seed_demo_audit_surfaces`): seven deterministic runs, five approval requests spanning all decision states, and eval results for every published agent version so the studio comparison is never empty. They are read-only demo artifacts, disclosed as synthetic; anonymous mutations still fail closed at 403.

### 0.5 Captain actions still required when worker is unauthenticated

If `vercel` / `render` CLIs are missing or not logged in, and GitHub has no deployment credentials, the worker stops after in-repo proof with this captain list:

| # | Action | Where |
| --- | --- | --- |
| 1 | Create Cloudflare DNS `ledger` CNAME → Vercel | Cloudflare DNS |
| 2 | Apply `render.yaml` Blueprint; copy API URL | Render |
| 3 | Set `BACKEND_CORS_ORIGINS=https://ledger.leihuang.me` | Render API env |
| 4 | Create Vercel project root `apps/web`; set API URL envs; `OPERATOR_UI_ENABLED=false` | Vercel |
| 5 | Attach domain `ledger.leihuang.me`; wait for cert | Vercel + Cloudflare |
| 6 | Run `./scripts/verify-public-demo.sh` with public URLs | Any clean network |
| 7 | Keep generated tokens server-only; never in public Vercel or git | Render / 1Password |

## 1. Create the Render backend

See section 0.2 for the public-demo checklist. Summary:

1. Connect the repository to Render and create a Blueprint from `render.yaml`.
2. Review the instance plans before applying the Blueprint.
3. Enter `BACKEND_CORS_ORIGINS` when Render prompts for it (exact Vercel production origin).
4. Let the API become ready at `/ready`.
5. Confirm generated `DEMO_OPERATOR_TOKEN`, `EVAL_RUN_TOKEN`, and `DOCUMENT_INGEST_TOKEN` in the API service only.

## 2. Create the Vercel frontend

See section 0.3. Root Directory must be `apps/web`. Public demo deployments stay read-only: every server action rejects before forwarding credentials when `OPERATOR_UI_ENABLED` is not exactly `true`.

## 3. Optional hosted providers

The default deployment uses deterministic local diagnosis, local embeddings, and local trace identifiers. To enable an external provider, set only the variables for that provider **on a non-public operator surface**:

| Capability | Variables |
| --- | --- |
| OpenAI diagnosis | `LLM_PROVIDER=openai`, `LLM_MODEL`, `OPENAI_API_KEY` |
| Anthropic diagnosis | `LLM_PROVIDER=anthropic`, `LLM_MODEL`, `ANTHROPIC_API_KEY` |
| OpenAI embeddings | `EMBEDDING_PROVIDER=openai`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_API_KEY` |
| Langfuse | `OBSERVABILITY_PROVIDER=langfuse`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`, optional `LANGFUSE_PROJECT_ID` |
| LangSmith | `OBSERVABILITY_PROVIDER=langsmith`, `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` |

Keep `OBSERVABILITY_FULL_PAYLOADS=false` unless exporting the synthetic evidence payloads is an explicit decision. Stripe test-mode evidence (if added later) stays off the anonymous public deployment per `prd.md`.

## 4. Post-deploy verification

Prefer `./scripts/verify-public-demo.sh` (section 0.4). Manual checklist without printing tokens:

1. `GET /health` returns a live process response.
2. `GET /ready` proves database connectivity.
3. The Vercel landing page loads revenue evidence and the Agents/Tools navigation.
4. The public web deployment shows the read-only banner and rejects direct server-action mutation attempts without changing API state.
5. In a separately protected operator deployment, a server action with the configured token can launch a run.
6. The Celery worker moves the run out of `queued` and records steps.
7. A high-risk action remains pending until an approval decision.
8. The good/degraded eval comparison shows at least one regression.
9. Browser response headers include CSP, frame protection, MIME sniffing protection, referrer policy, and a restrictive permissions policy.

## 5. Local public-demo proof (no cloud credentials)

Mirrors anonymous production gates with compose:

```bash
cp .env.public-demo.example .env.public-demo
docker compose --env-file .env.public-demo up -d --build
# wait until api + web healthy
# If a local HTTP proxy (Surge/Clash) intercepts localhost, clear proxy env vars:
#   env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy ...
# If another process already binds 127.0.0.1:8000, prefer IPv6 host ports Docker publishes:
#   API_BASE_URL='http://[::1]:8000' WEB_BASE_URL='http://[::1]:3000'
API_BASE_URL=http://localhost:8000 WEB_BASE_URL=http://localhost:3000 REQUIRE_WEB=true \
  ./scripts/verify-public-demo.sh
cd apps/web && npm ci && npx playwright install chromium
PLAYWRIGHT_EXPECT_READ_ONLY=true \
PLAYWRIGHT_BASE_URL=http://localhost:3000 \
PLAYWRIGHT_API_BASE_URL=http://localhost:8000 \
  npm run test:e2e:public-demo
```

Operator-mode Playwright (mutations) still uses default compose env with `OPERATOR_UI_ENABLED=true` and the existing full suite (`npm run test:e2e`).

CI job `public-demo-readonly` in `.github/workflows/e2e.yml` runs the verify script and the public-demo Playwright project against compose with `.env.public-demo.example`.

## Environment variable reference

The complete no-secret samples live in `.env.example`, `.env.public-demo.example`, `apps/api/.env.example`, and `apps/web/.env.example`.

- `APP_ENV=demo` activates fail-closed mutation gates.
- `DEMO_OPERATOR_TOKEN` gates agent/version/tool/run/approval/mock-action mutations.
- `EVAL_RUN_TOKEN` separately gates eval execution.
- `OPERATOR_UI_ENABLED=false` keeps anonymous public Next.js deployments read-only; set it to `true` only behind deployment authentication for recording/operator sessions.
- `DOCUMENT_INGEST_TOKEN` gates HTTP knowledge ingestion.
- `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND` connect managed state.
- `BACKEND_CORS_ORIGINS` lists exact trusted browser origins (for public demo: `https://ledger.leihuang.me`).
- `RATE_LIMIT_MUTATIONS_PER_MINUTE` and `RATE_LIMIT_SEARCH_PER_MINUTE` bound public traffic.
- `LOG_FORMAT=json` is recommended for hosted log ingestion.
