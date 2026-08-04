# Deployment Guide

The tracked public-demo topology uses Cloudflare Workers for the Next.js frontend and a zero-cost Render Blueprint for the FastAPI API, Postgres/pgvector, and Redis-compatible Key Value service. The public Render path has no background worker. It serves only pre-seeded, anonymous read-only data; async investigations and eval execution remain available in local or paid operator deployments. The frontend is adapted with `@opennextjs/cloudflare`; the backend remains on Render. This is a deployment template, not proof that a public environment currently exists.

**Public demo target origin:** `https://ledger.leihuang.me` (Cloudflare Worker) with the Render web service as the API origin (`https://ledger-api-xvoe.onrender.com` in the tracked Worker config). If the Render hostname changes, update `apps/web/wrangler.jsonc` and redeploy. The backend CORS value remains the exact browser origin `https://ledger.leihuang.me`.

The provider configuration follows the current official [Render free-instance limits](https://render.com/docs/free), [Render Blueprint specification](https://render.com/docs/blueprint-spec), [Render pgvector support](https://render.com/docs/postgresql-extensions), [OpenNext Cloudflare setup](https://opennext.js.org/cloudflare/get-started), and [Cloudflare Workers custom-domain configuration](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/). OpenNext documents support for all Next.js 16 minor and patch releases. The tracked adapter version also declares a Next.js peer range of `>=16.2.11`, and the frontend is pinned within Next 16 above that floor.

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

1. Use a Render Hobby workspace with no payment method attached. This is required to make the hard cost ceiling $0: if included bandwidth or build minutes are exhausted, Render suspends service or builds instead of charging overages.
2. Authenticate to Render (dashboard or CLI). Install CLI only if non-interactive login is available.
3. Connect repo `leihuang23/Ledger` and apply the Blueprint in `render.yaml`.
4. Before confirming, verify that the Blueprint contains exactly three Render resources and every resource shows the Free instance type:
   - `ledger-api` web service
   - `ledger-postgres` Postgres database
   - `ledger-redis` Key Value instance
   - Do not add `ledger-worker`; Render does not offer a free background-worker instance type.
5. Set **`BACKEND_CORS_ORIGINS`** to the exact browser origin only, for example:
   - `https://ledger.leihuang.me`
   - Comma-separated list only if you truly need more than one trusted origin.
6. Confirm generated server-only secrets on the API service (do not commit or paste into git/chat logs):
   - `DEMO_OPERATOR_TOKEN`
   - `EVAL_RUN_TOKEN`
   - `DOCUMENT_INGEST_TOKEN`
7. Confirm Blueprint defaults that must remain for public safety:
   - `APP_ENV=demo`
   - `LLM_PROVIDER=none` (or private operator override later)
   - `OBSERVABILITY_FULL_PAYLOADS=false`
   - No Stripe live/test secrets required for the anonymous public surface
8. Wait until `GET https://<api-host>/ready` returns 200. Startup runs Alembic and seeds an empty demo DB under an advisory lock.
9. Confirm the public API base URL matches both Worker vars in `apps/web/wrangler.jsonc` (`https://ledger-api-xvoe.onrender.com` by default).

Why the Blueprint sets `ALLOW_UNSAFE_BOOTSTRAP_SEED=true`: a managed Postgres hostname is intentionally rejected by the local-only seed safety check. Startup seeding still occurs only when the accounts table is empty. Destructive CLI reseeding continues to require the explicit `--allow-destructive` flag.

The API accepts Render's `postgresql://` connection string and normalizes it to SQLAlchemy's installed `postgresql+psycopg://` driver. The container also honors Render's `PORT` value.

#### Free-tier behavior and tradeoffs

- Render spins down a Free web service after 15 minutes without inbound traffic. The first request after idle triggers a cold start that Render says takes about one minute. Ledger also runs migrations and its empty-database seed check before starting the API, so recovery can take longer. The browser may show Render's loading page during this interval.
- Free Postgres is limited to 1 GB and expires 30 days after creation. At expiry it becomes inaccessible, the API readiness check returns 503, and the public demo remains unavailable until the database is recreated. Render retains the expired database for a 14-day paid-upgrade grace period and then deletes it. Free databases have no Render backups, point-in-time recovery, or managed connection pooling.
- Free Key Value is limited to 25 MB and 50 connections, and `persistenceMode: off` is mandatory because the free instance is memory-only. A restart clears cached knowledge-search results and rate-limit counters. The source data remains in Postgres, and all mutation gates remain fail-closed, so losing Key Value state does not remove evidence or authorize a write.
- The public Blueprint intentionally has no background worker. Token-gated async investigations, control-plane runs, eval execution, scheduled stale-run cleanup, and other Celery processing are absent on this path. The UI can inspect pre-seeded runs, approvals, traces, and eval results, but it cannot create or process new ones. Use the local Compose stack or a separately protected paid operator deployment for those features.
- Free services remain subject to Render's monthly included instance hours, outbound bandwidth, and build minutes. With no payment method attached, exhaustion causes suspension or disabled builds rather than a charge. This preserves the $0 ceiling but does not guarantee uninterrupted availability.

#### Re-create and reseed Free Postgres after day 30

The public data is synthetic and deterministic, so the zero-cost recovery path intentionally discards the expired database rather than pretending it has a backup:

1. In the Render Dashboard, confirm `ledger-postgres` is expired and `GET https://<api-host>/ready` reports 503.
2. Delete the expired `ledger-postgres` resource in the Render Dashboard. This permanently discards its data. Do not do this if the database contains anything other than the expected synthetic demo seed.
3. Open the existing Ledger Blueprint and click **Manual Sync**. Keep `ledger-postgres` in `render.yaml`; Render recreates a Blueprint-managed resource that was deleted outside the Blueprint, and the `plan: free` definition creates a fresh 1 GB Free Postgres instance.
4. Wait for the Blueprint sync and `ledger-api` deploy to finish. Its `DATABASE_URL` reference is refreshed to the replacement database. The API entrypoint runs `alembic upgrade head`, then automatically seeds the empty database because `ALLOW_UNSAFE_BOOTSTRAP_SEED=true` is scoped to this intentional demo bootstrap.
5. Confirm the API logs show migrations and `Seeded empty database`, wait for `/ready` to return 200, then rerun `./scripts/verify-public-demo.sh` and the read-only Playwright suite from section 0.4.

Record the new creation date and schedule this procedure before the next 30-day expiry. There is an outage between expiry and successful reseeding, and there is no zero-cost in-place renewal.

### 0.3 Cloudflare Workers frontend (`apps/web`)

1. Use Node.js 22 or newer, then run `npm ci` from `apps/web`.
2. Inspect `wrangler.jsonc`. Production uses Worker `ledger-web`, disables `workers.dev`, and attaches the custom domain `ledger.leihuang.me`. The `preview` Wrangler environment deploys `ledger-web-preview` to a `workers.dev` URL without the production route.
3. Keep these non-sensitive public demo values under `vars` in both the top-level and `env.preview` config:

| Variable | Public demo value | Notes |
| --- | --- | --- |
| `API_INTERNAL_BASE_URL` | `https://ledger-api-xvoe.onrender.com` | Server-side Worker fetch base; replace if Render assigns a different URL |
| `NEXT_PUBLIC_API_BASE_URL` | `https://ledger-api-xvoe.onrender.com` | Browser-visible API origin; embedded during build and not a secret |
| `OPERATOR_UI_ENABLED` | `false` | Required for anonymous public demo |

The same three public values also live in the tracked `apps/web/.env.production`, which `next build` reads to inline `NEXT_PUBLIC_API_BASE_URL` into the browser bundle. Keep it in sync with the `vars` above.

4. Build with `npm run build:cloudflare`. This runs the existing Next.js Webpack build and adapts `.next` into `.open-next`.
5. Serve the adapted Worker locally with `npm run preview`, or deploy a non-production Worker with `npm run deploy:preview`. The preview environment explicitly sets `routes: []`, so it cannot inherit or reassign the production custom domain.
6. Before a cloud build or deploy, use a clean checkout, or remove the ignored local overrides `.dev.vars` and `.env.production.local`. Wrangler loads `.dev.vars` for local development, and Next loads `.env.production.local` ahead of the tracked `.env.production`; local values must not override the checked-in Render origins during a cloud build.
7. Deploy production with `npm run deploy`. The checked-in custom-domain route makes the Worker the frontend hosting path.
8. **Do not set** on the anonymous public Worker:
   - `DEMO_OPERATOR_TOKEN`, `EVAL_RUN_TOKEN`, `DOCUMENT_INGEST_TOKEN`
   - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, Langfuse/LangSmith secrets
   - Any Stripe secret or publishable key
   - Any secret under a `NEXT_PUBLIC_*` name
9. After deploy, verify Render still has exactly `BACKEND_CORS_ORIGINS=https://ledger.leihuang.me`.

`wrangler.jsonc` `vars` are checked-in, non-encrypted configuration that OpenNext exposes through `process.env` at Worker runtime (server-side). The browser cannot read Worker vars, so `NEXT_PUBLIC_API_BASE_URL` is also declared in the tracked `apps/web/.env.production`: `next build` reads that file and inlines the value into the browser bundle. Because the value is baked in at build time, `NEXT_PUBLIC_API_BASE_URL` requires a rebuild after it changes. For local preview, copy `.dev.vars.example` to ignored `.dev.vars` and to ignored `.env.production.local`; both contain only local public values.

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
| 1 | Apply `render.yaml`; verify all three resources are Free; wait for `/ready`; confirm the actual public API URL | Render |
| 2 | Set `BACKEND_CORS_ORIGINS=https://ledger.leihuang.me` exactly | Render API env |
| 3 | Update both `wrangler.jsonc` API vars if the Render URL differs; rebuild and revalidate | Repo |
| 4 | Confirm Wrangler selects the Cloudflare account that owns the `leihuang.me` zone | Cloudflare CLI |
| 5 | Resolve any existing `ledger` DNS conflict, then run `npm run deploy` from `apps/web` | Cloudflare Workers |
| 6 | Confirm TLS and the custom domain, then run `./scripts/verify-public-demo.sh` with public URLs | Any clean network |
| 7 | Run the read-only Playwright suite against `https://ledger.leihuang.me` | Any clean network |
| 8 | Keep generated backend tokens server-only; never put them in Worker vars, secrets, client config, or git | Render / secret manager |
| 9 | Record the Free Postgres creation date and schedule the day-30 re-create/reseed procedure in section 0.2 | Operations calendar |

## 1. Create the Render backend

See section 0.2 for the public-demo checklist. Summary:

1. Connect the repository to Render and create a Blueprint from `render.yaml`.
2. Verify the web service, Postgres, and Key Value plans are all Free and that no background worker is present.
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
5. The public UI exposes no launch, transition, approval-decision, ingestion, or eval-execution controls; direct API mutations still return 403.
6. Pre-seeded run timelines and approval states remain inspectable without a Celery worker.
7. The good/degraded eval comparison shows at least one regression from pre-seeded results.
8. Browser response headers include CSP, frame protection, MIME sniffing protection, referrer policy, and a restrictive permissions policy.

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
cp .dev.vars.example .env.production.local
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

The gitignored `.env.production.local` overrides the tracked `apps/web/.env.production`, so the local preview's browser bundle targets `http://localhost:8000` instead of the Render origin. Remove it (with `.dev.vars`) before a cloud build or deploy.

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
