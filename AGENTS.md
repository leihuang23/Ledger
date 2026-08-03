# AGENTS.md

This repository is for Ledger, a production-shaped SaaS revenue and support operations agent. This file is the product contract and success criteria; the implementation source of truth is the code, Alembic migrations, API OpenAPI docs, and tests. Treat this file as the contract, not a detailed implementation plan.

## Critical Product Read

The project idea is strong because it demonstrates the hard parts of agentic software that engineering teams and technical reviewers actually care about: cross-source investigation, evidence-backed claims, constrained actions, run traces, and evals. The demo prompt, "MRR dropped this week," is a good anchor because it is concrete, business-relevant, and naturally requires analytics plus support context.

The main risk is scope inflation. The recommended stack is credible but heavy for a first pass: Next.js, FastAPI, PostgreSQL, pgvector, Redis, LangGraph, provider-backed observability, Celery, Docker, and split deployment can easily become infrastructure theater before the agent proves anything. Prefer a narrow, verified system over a broad, half-working one.

The second risk is fake intelligence. A fluent final report is not enough. The product must prove that the agent found the right root cause from seeded data, cited concrete evidence, respected approval boundaries, and produced a trace that a reviewer can audit.

The third risk is unrealistic seed data. If scenarios are too obvious or linear, the project becomes another toy demo. Seed data should include confounders, noisy support tickets, partial outages, invoice timing, plan changes, churn, failed payments, and usage changes that require actual analysis.

## Product Guardrails

- Optimize for a reviewer evaluating engineering judgment, not for a flashy agent demo.
- Honest positioning: Ledger is a production-shaped system that demonstrates investigation, cited evidence, auditable run traces, and approval-gated risky actions. Disclose synthetic data, optional Stripe test-mode sandbox evidence, read-only public demo, deterministic classifier as source of truth, and mock external actions behind approval. Do not imply merchant adoption or live commerce.
- Keep the first version focused on the primary investigation loop: anomaly -> evidence gathering -> root cause -> affected accounts -> recommended actions -> approval-gated drafts.
- Every important claim in the UI, API, final report, and eval output must be backed by cited SQL results, tickets, documents, incident records, or (when present) Stripe-derived billing evidence already normalized into Ledger records.
- Never let the agent perform irreversible or external write actions without explicit approval. In the first version, all Slack, email, CRM, and task actions must be mocks.
- Demo mutations are token-gated when `APP_ENV=demo`: `POST /incidents`, `POST /agent/investigations`, `POST /approvals/{id}/approve|reject`, `POST /mock-actions`, `POST /runs`, `POST /runs/{id}/transitions`, `POST /agents` and its version mutation routes (`POST /agents/{id}/versions`, `PATCH .../versions/{vid}`, `POST .../versions/{vid}/publish`), `POST /tools`, and `POST /eval-datasets` (including dataset runs) require `DEMO_OPERATOR_TOKEN`; `POST /evals/run` requires `EVAL_RUN_TOKEN`; `POST /documents/ingest` requires `DOCUMENT_INGEST_TOKEN`. Each gate uses `secrets.compare_digest` and fails closed when its token is unset in the `demo` env. Only the `DEMO_OPERATOR_TOKEN` gate opens in local/test/development; the `EVAL_RUN_TOKEN` and `DOCUMENT_INGEST_TOKEN` gates fail closed in every environment when their token is unset. Never disable the gates for a publicly accessible demo.
- Treat seeded scenarios and eval cases as product-critical assets, not test fixtures of convenience.
- Prefer deterministic analytics and explicit tool results before LLM summarization. The LLM should synthesize evidence, not invent the evidence.
- Do not introduce real customer data. Do not introduce third-party integrations beyond the Stripe test-mode evidence adapter defined in the Stripe sandbox boundary below.

## Stripe sandbox boundary (optional evidence adapter)

Stripe is **in scope only** as a narrow test-mode evidence adapter (Stripe evidence boundary) as defined in this section. It is not a general merchant platform.

Allowed (the adapter is implemented in `apps/api/app/stripe_adapter/`):

- Test-mode only; no live credentials or real customer data.
- Customers, subscriptions, and invoices ingestion into existing Ledger models.
- Verified webhooks with signature checks, event-id uniqueness/idempotency, and out-of-order protection (re-fetch current objects when snapshots may be stale).
- Bounded reconciliation for missed events.
- Stripe Test Clock failed-renewal scenario feeding investigations with citations.
- Stripe credentials and any Stripe-related mutations only outside the anonymous public deployment.

Forbidden unless this file is updated again:

- Checkout, refunds, payment collection, Connect, OAuth.
- Any Stripe write actions beyond ingestion/reconciliation into Ledger records.
- Live mode credentials or real customer data.
- Exposing Stripe secrets via public client config, `NEXT_PUBLIC_*`, or the anonymous demo.

Preserve existing evidence, eval, approval, audit, and demo-token gates whether or not Stripe is configured. The anonymous public demo remains read-only (`APP_ENV=demo` and operator gates unchanged).

Implementation lives in `apps/api/app/stripe_adapter/` (`POST /stripe/webhook`, `POST /stripe/reconcile`, status/events/ingestion-log routes). Optional env: `STRIPE_API_KEY` (`sk_test_` only), `STRIPE_WEBHOOK_SECRET`. See `docs/deployment.md` and `tests/test_stripe_adapter.py`.

## Public demo deployment

- Topology and captain checklist: `docs/deployment.md` (Vercel `apps/web` + Render `render.yaml`; target origin `https://ledger.leihuang.me`).
- Local anonymous proof (no cloud login): `.env.public-demo.example`, `scripts/verify-public-demo.sh`, and `apps/web` `npm run test:e2e:public-demo` with `PLAYWRIGHT_EXPECT_READ_ONLY=true`.
- Never put operator/eval/ingest/Stripe/model secrets in public Vercel env or any `NEXT_PUBLIC_*` variable.

## MVP Definition

Build the smallest credible system that can pass the success criteria in this file:

- At least 5 seeded incident scenarios (the current seed includes 6 scenarios, including one ambiguity case).
- At least 4 of 5 eval scenarios correctly identify the intended root cause.
- Final reports cite SQL queries, support tickets, docs, incidents, or (when present) Stripe-derived billing evidence for every major claim.
- Approval requests block risky actions until approved or rejected.
- Every agent run records step logs, trace identifiers when available, token/cost estimates, and the final report.

If implementation effort grows, cut optional infrastructure before cutting evidence, evals, or approval safety.

## Architecture Guidance

- Keep domain logic separate from presentation, agent orchestration, and persistence.
- Model the investigation as a stateful workflow with explicit intermediate artifacts: anomaly summary, hypotheses, tool calls, evidence bundle, root-cause decision, affected accounts, proposed actions, approval requests, and final report.
- Use structured schemas for all LLM-facing inputs and outputs. Reject malformed or unsupported outputs instead of silently coercing them.
- Keep tool boundaries explicit. SQL/query tools return data; document tools return cited excerpts; action tools create pending mock actions or approval requests.
- Store agent run steps with enough detail to replay or audit the investigation without reading logs.
- Keep provider abstraction minimal until there is real pressure to support multiple LLM providers.
- Do not add dependencies only because they are listed in a product brief or plan. Add each dependency when a real implementation need appears.
- The current backend is organized by domain under `apps/api/app/`: `accounts`, `agent`, `agents`, `approvals`, `core`, `dashboard`, `db`, `evals`, `health`, `incidents`, `knowledge`, `llm`, `metrics`, `runs`, `support`, and `tools`. The Next.js frontend under `apps/web/app/` mirrors the operational surfaces: dashboard, incidents, agent runs, approvals, accounts, support tickets, knowledge search, evals, and the control-plane pages (agents, tools, runs, dashboard).
- LangGraph is the recommended orchestrator. The MVP uses a fixed linear investigation DAG built with LangGraph's `StateGraph` in `apps/api/app/agent/workflow.py` (compiled per run invocation, no dynamic branching). Do not add dynamic branching or loops to the graph unless a real feature requires it; keep the orchestration simple and auditable.

## Data And Scenario Rules

- Seed data must be internally consistent across accounts, subscriptions, invoices, product events, tickets, docs, incidents, and expected eval outcomes.
- Each scenario should include a named root cause, affected account set, expected evidence, likely false leads, and expected recommendations.
- Include negative and ambiguous cases where the agent must say what is unknown.
- Avoid scenarios where the answer can be found from one obvious row or one perfectly worded document.
- Keep PII synthetic and clearly fake.

## Agent Behavior Rules

- The agent must state uncertainty when evidence is incomplete or conflicting.
- The agent must distinguish root cause, contributing factors, symptoms, and recommended next actions.
- The agent must not cite evidence it did not retrieve.
- The agent must not turn a draft follow-up into a sent message. Drafts become approval requests or mock actions.
- The agent must expose failed tool calls, missing data, and reasoning dead ends in the run history.
- The final report should be concise, operational, and auditable.

## Testing And Verification

- Follow TDD for behavior that encodes product intent, safety, evidence quality, or non-obvious domain rules. Write the smallest failing behavior test first, implement the narrowest change to pass it, then refactor while green.
- Prefer behavior and contract tests over implementation-detail tests. Tests should read like product claims: deterministic seed data, evidence-backed metric semantics, approval gating, cited report claims, and failure visibility.
- Use vertical red-green-refactor slices rather than writing a large batch of speculative tests before implementation. One behavior, one failing test, one minimal implementation, then repeat.
- Do not force TDD onto low-value surfaces such as cosmetic CSS, mechanical wiring, or framework boilerplate. For UI, combine focused contract/type checks with browser smoke tests for rendered behavior.
- Add regression tests for scenario seed integrity before changing scenario data.
- Test API contracts with focused backend tests.
- Test the investigation workflow against eval cases, not just happy-path unit tests.
- Test approval gating so risky actions cannot bypass pending approval state.
- For frontend work, run a browser smoke test and inspect the actual rendered flow.
- Before claiming completion, run the narrowest relevant tests plus lint/typecheck/build when the project has those commands.

## UI Guidance

- Build the actual investigation workspace first, not a marketing landing page.
- Prioritize dense, scannable operational UI: anomaly summary, evidence, affected accounts, approval queue, run timeline, and final report.
- Make citations and action status visually obvious.
- Avoid decorative dashboards that do not help the user verify the agent's work.
- The UI should make failure modes visible: missing evidence, low confidence, rejected approvals, failed tools, and eval failures.

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) for all commit messages. Use structured prefixes (`feat:`, `fix:`, `docs:`, `style:`, `refactor:`, `test:`, `chore:`) with an optional scope, and include a concise description in the imperative mood. Append a body when the change needs additional context (e.g., breaking changes, motivation, or side effects). Do not commit with vague or single-word messages.

## Learning Files

When the user asks to create a learning file for current changes:

- Inspect the existing Markdown files in `learning/` first and match their naming, structure, tone, and audience.
- Use the next `week-N.md` filename unless the user asks for another format.
- Write the file as a reviewer-oriented learning guide, not a changelog. Explain what the slice proves, which files to read, the key ideas, review questions, verification commands, and operational gotchas.
- Tailor the guide to a JavaScript full-stack developer learning the Python/FastAPI/Postgres parts when that matches the existing notes.
- Ground the content in the actual changed files and verified behavior from the current branch.
- Include environment variables, manual checks, and testing commands when they are relevant to the slice.
- Note that `learning/` is ignored by `.gitignore`; if the file should be committed, it must be force-added explicitly.

## Working Style For Future Agents

- Read this file before making product or architecture changes.
- Challenge requests that weaken evidence, evals, approval gating, or auditability.
- Keep diffs small and behavior-oriented.
- Prefer deleting unnecessary complexity over adding new layers.
- If the implementation direction conflicts with these instructions, update this file so future agents do not inherit drift.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
