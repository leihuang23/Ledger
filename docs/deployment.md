# Deployment Guide

The tracked production-shaped topology uses Cloudflare Workers for the Next.js frontend and a Render Blueprint for the FastAPI API, Celery worker, Postgres/pgvector, and Redis-compatible Key Value service. The frontend is adapted with `@opennextjs/cloudflare`; the backend remains on Render. This is a deployment template, not proof that a public environment currently exists.

**Public demo target origin:** `https://ledger.leihuang.me` (Cloudflare Worker) with the Render web service as the API origin (`https://ledger-api.onrender.com` in the tracked Worker config). If the Render hostname changes, update `apps/web/wrangler.jsonc` and redeploy. The backend CORS value remains the exact browser origin `https://ledger.leihuang.me`.

The provider configuration follows the current official [Render Blueprint specification](https://render.com/docs/blueprint-spec), [Render pgvector support](https://render.com/docs/postgresql-extensions), [OpenNext Cloudflare setup](https://opennext.js.org/cloudflare/get-started), and [Cloudflare Workers custom-domain configuration](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/). OpenNext documents support for all Next.js 16 minor and patch releases. The tracked adapter version also declares a Next.js peer range of `>=16.2.11`, and the frontend is pinned within Next 16 above that floor.

## 0. Captain checklist: anonymous public read-only demo

Use this when standing up or repairing the public read-only demo. Workers without Render or Cloudflare login cannot complete cloud steps; they can still prove the local `APP_ENV=demo` and OpenNext preview path (section 5).

### 0.1 DNS (Cloudflare for `leihuang.me`)

The `leihuang.me` zone already uses Cloudflare DNS. `apps/web/wrangler.jsonc` declares `ledger.leihuang.me` as a Worker Custom Domain. Cloudflare creates the DNS record and certificate when the production Worker is deployed, so do not create a CNAME to another frontend provider.

1. In Cloudflare, confirm the `leihuang.me` zone and Workers account are in the same account selected by Wrangler.
2. Remove or rename any conflicting `ledger` DNS record before the first production deploy. Do not delete an existing record until the cutover is authorized.
3. From `apps/web`, run `npx wrangler whoami` and confirm the expected account non-interactively.
4. Deploy with `npm run deploy`. Wrangler applies the `custom_domain: true` route from `wrangler.jsonc` and provisions TLS.
5. Confirm from a clean network: `dig +short ledger.leihuang.me @1.1.1.1` resolves and `curl -I https://ledger.leihuang.me` reaches the Worker.
6. Optional API hostname: either use the default `*.onrender.com` service URL or a separate custom domain on Render. Update both public API vars in `wrangler.jsonc` if it changes.

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
8. Confirm the public API base URL matches both Worker vars in `apps/web/wrangler.jsonc` (`https://ledger-api.onrender.com` by default).

Why the Blueprint sets `ALLOW_UNSAFE_BOOTSTRAP_SEED=true`: a managed Postgres hostname is intentionally rejected by the local-only seed safety check. Startup seeding still occurs only when the accounts table is empty. Destructive CLI reseeding continues to require the explicit `--allow-destructive` flag.

The API accepts Render's `postgresql://` connection string and normalizes it to SQLAlchemy's installed `postgresql+psycopg://` driver. The container also honors Render's `PORT` value.

### 0.3 Cloudflare Workers frontend (`apps/web`)

1. Use Node.js 22 or newer, then run `npm ci` from `apps/web`.
2. Inspect `wrangler.jsonc`. Production uses Worker `ledger-web`, disables `workers.dev`, and attaches the custom domain `ledger.leihuang.me`. The `preview` Wrangler environment deploys `ledger-web-preview` to a `workers.dev` URL without the production route.
3. Keep these non-sensitive public demo values under `vars` in both the top-level and `env.preview` config:

| Variable | Public demo value | Notes |
| --- | --- | --- |
| `API_INTERNAL_BASE_URL` | `https://ledger-api.onrender.com` | Server-side Worker fetch base; replace if Render assigns a different URL |
| `NEXT_PUBLIC_API_BASE_URL` | `https://ledger-api.onrender.com` | Browser-visible API origin; embedded during build and not a secret |
| `OPERATOR_UI_ENABLED` | `false` | Required for anonymous public demo |

4. Build with `npm run build:cloudflare`. This runs the existing Next.js Webpack build and adapts `.next` into `.open-next`.
5. Serve the adapted Worker locally with `npm run preview`, or deploy a non-production Worker with `npm run deploy:preview`. The preview environment explicitly sets `routes: []`, so it cannot inherit or reassign the production custom domain.
6. Before a cloud build or deploy, use a clean checkout or remove local `.dev.vars`. Wrangler loads that ignored file for local development, and local values must not override the checked-in Render origins during a cloud build.
7. Deploy production with `npm run deploy`. The checked-in custom-domain route makes the Worker the frontend hosting path.
8. **Do not set** on the anonymous public Worker:
   - `DEMO_OPERATOR_TOKEN`, `EVAL_RUN_TOKEN`, `DOCUMENT_INGEST_TOKEN`
   - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, Langfuse/LangSmith secrets
   - Any Stripe secret or publishable key
   - Any secret under a `NEXT_PUBLIC_*` name
9. After deploy, verify Render still has exactly `BACKEND_CORS_ORIGINS=https://ledger.leihuang.me`.

`wrangler.jsonc` `vars` are checked-in, non-encrypted configuration. OpenNext makes them available through `process.env` at Worker runtime and during its build. `NEXT_PUBLIC_API_BASE_URL` is intentionally browser-visible and requires a rebuild after it changes. For local preview, copy `.dev.vars.example` to ignored `.dev.vars`; it contains only local public values.

Cloudflare secrets are encrypted bindings set with `npx wrangler secret put NAME`, and local secrets belong only in ignored `.dev.vars`. The anonymous Worker needs no secrets, so do not create any for it. A future protected operator surface must be a separate authenticated Worker and security review; never add its tokens to this public Worker or to `NEXT_PUBLIC_*`.

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

### 0.5 Captain actions still required when cloud CLIs are unauthenticated

If Wrangler or Render is not logged in non-interactively, stop after the in-repo proof with this captain list:

| # | Action | Where |
| --- | --- | --- |
| 1 | Apply `render.yaml`; wait for `/ready`; confirm the actual public API URL | Render |
| 2 | Set `BACKEND_CORS_ORIGINS=https://ledger.leihuang.me` exactly | Render API env |
| 3 | Update both `wrangler.jsonc` API vars if the Render URL differs; rebuild and revalidate | Repo |
| 4 | Confirm Wrangler selects the Cloudflare account that owns the `leihuang.me` zone | Cloudflare CLI |
| 5 | Resolve any existing `ledger` DNS conflict, then run `npm run deploy` from `apps/web` | Cloudflare Workers |
| 6 | Confirm TLS and the custom domain, then run `./scripts/verify-public-demo.sh` with public URLs | Any clean network |
| 7 | Run the read-only Playwright suite against `https://ledger.leihuang.me` | Any clean network |
| 8 | Keep generated backend tokens server-only; never put them in Worker vars, secrets, client config, or git | Render / secret manager |

## 1. Create the Render backend

See section 0.2 for the public-demo checklist. Summary:

1. Connect the repository to Render and create a Blueprint from `render.yaml`.
2. Review the instance plans before applying the Blueprint.
3. Enter `BACKEND_CORS_ORIGINS` when Render prompts for it (exact Cloudflare Worker production origin).
4. Let the API become ready at `/ready`.
5. Confirm generated `DEMO_OPERATOR_TOKEN`, `EVAL_RUN_TOKEN`, and `DOCUMENT_INGEST_TOKEN` in the API service only.

## 2. Create the Cloudflare Workers frontend

See section 0.3. Run all frontend deployment commands from `apps/web`. Public demo deployments stay read-only: every server action rejects before forwarding credentials when `OPERATOR_UI_ENABLED` is not exactly `true`. The former `apps/web/vercel.json` has been removed; Vercel is no longer a tracked hosting path.

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

### Optional Stripe test-mode evidence adapter

Stripe is an **optional** sandbox evidence source, not a payments product surface. Leave unset for the public demo.

| Variable | Purpose |
| --- | --- |
| `STRIPE_API_KEY` | Test-mode secret only (`sk_test_...`). Live keys are rejected at settings load. |
| `STRIPE_WEBHOOK_SECRET` | Webhook signing secret (`whsec_...`) for `POST /stripe/webhook`. |
| `STRIPE_WEBHOOK_TOLERANCE_SECONDS` | Signature timestamp tolerance (default `300`). |

Endpoints (all routes except `GET /stripe/status` are disabled when `APP_ENV=demo`; `POST /stripe/webhook` and `POST /stripe/reconcile` also fail closed when the required secret is unset):

- `GET /stripe/status` - whether the adapter is configured/enabled
- `POST /stripe/webhook` - signed event ingest (raw body signature verification, event-id idempotency, out-of-order re-fetch when an API key is present)
- `POST /stripe/reconcile` - operator-gated bounded repair of missed sandbox objects (`X-Demo-Operator-Token`)
- `GET /stripe/events` / `GET /stripe/ingestion-logs` - operator-gated visibility for processed/unsupported/failed events

Local CI tests mock the Stripe client. To exercise a real Test Clock against your sandbox:

```bash
export STRIPE_API_KEY=sk_test_...
cd apps/api && pytest -q tests/test_stripe_adapter.py -m stripe_live
```

Never put Stripe secrets in `NEXT_PUBLIC_*`, the anonymous public demo, or the git tree.

## 4. Post-deploy verification

Prefer `./scripts/verify-public-demo.sh` (section 0.4). Manual checklist without printing tokens:

1. `GET /health` returns a live process response.
2. `GET /ready` proves database connectivity.
3. The Cloudflare Worker landing page loads revenue evidence and the Agents/Tools navigation.
4. The public web deployment shows the read-only banner and rejects direct server-action mutation attempts without changing API state.
5. In a separately protected operator deployment, a server action with the configured token can launch a run.
6. The Celery worker moves the run out of `queued` and records steps.
7. A high-risk action remains pending until an approval decision.
8. The good/degraded eval comparison shows at least one regression.
9. Browser response headers include CSP, frame protection, MIME sniffing protection, referrer policy, and a restrictive permissions policy.

## 5. Local public-demo proof (no cloud credentials)

Run the Render-shaped backend with Compose and the frontend through the OpenNext Worker preview. Use separate terminals for the preview and verification commands:

```bash
cp .env.public-demo.example .env.public-demo
BACKEND_CORS_ORIGINS=http://localhost:8787 \
  docker compose --env-file .env.public-demo up -d --build postgres redis api worker
# wait until the API is healthy
# If a local HTTP proxy (Surge/Clash) intercepts localhost, clear proxy env vars:
#   env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy ...
# If another process already binds 127.0.0.1:8000, use the reachable Docker host port below.
cd apps/web
npm ci
cp .dev.vars.example .dev.vars
npm run preview

# In another terminal, from the repository root:
API_BASE_URL=http://localhost:8000 WEB_BASE_URL=http://localhost:8787 REQUIRE_WEB=true \
  ./scripts/verify-public-demo.sh
cd apps/web
npx playwright install chromium
PLAYWRIGHT_EXPECT_READ_ONLY=true \
PLAYWRIGHT_BASE_URL=http://localhost:8787 \
PLAYWRIGHT_API_BASE_URL=http://localhost:8000 \
  npm run test:e2e:public-demo
```

For a strict local CORS proof, also pass `STRICT_CORS=true EXPECTED_ORIGIN=http://localhost:8787` to `verify-public-demo.sh`. The production value must still be exactly `https://ledger.leihuang.me`.

Operator-mode Playwright (mutations) still uses default compose env with `OPERATOR_UI_ENABLED=true` and the existing full suite (`npm run test:e2e`).

CI job `public-demo-readonly` in `.github/workflows/e2e.yml` runs the verify script and the public-demo Playwright project against compose with `.env.public-demo.example`.

## Environment variable reference

The complete no-secret samples live in `.env.example`, `.env.public-demo.example`, `apps/api/.env.example`, `apps/web/.env.example`, and `apps/web/.dev.vars.example`.

- `APP_ENV=demo` activates fail-closed mutation gates.
- `DEMO_OPERATOR_TOKEN` gates agent/version/tool/run/approval/mock-action mutations.
- `EVAL_RUN_TOKEN` separately gates eval execution.
- `OPERATOR_UI_ENABLED=false` keeps anonymous public Next.js deployments read-only; set it to `true` only behind deployment authentication for recording/operator sessions.
- `DOCUMENT_INGEST_TOKEN` gates HTTP knowledge ingestion.
- `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND` connect managed state.
- `BACKEND_CORS_ORIGINS` lists exact trusted browser origins (for public demo: `https://ledger.leihuang.me`).
- `RATE_LIMIT_MUTATIONS_PER_MINUTE` and `RATE_LIMIT_SEARCH_PER_MINUTE` bound public traffic.
- `LOG_FORMAT=json` is recommended for hosted log ingestion.
- `STRIPE_API_KEY` / `STRIPE_WEBHOOK_SECRET` enable the optional test-mode evidence adapter; omit on the anonymous public demo (`APP_ENV=demo` disables the routes regardless).
