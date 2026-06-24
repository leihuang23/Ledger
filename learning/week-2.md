# Week 2 Learning Guide: Revenue Anomalies and Incident Creation

This guide is for reviewing the Week 2 slice as a JavaScript full-stack
developer who wants to understand the Python/FastAPI/Postgres changes well
enough to review them critically.

The goal is not to learn every FastAPI or SQLAlchemy feature. The goal is to
understand how a seeded revenue signal becomes a reviewable incident that a
future agent can investigate.

## What This Slice Proves

The slice creates the first realistic investigation starting point:

- Seeded invoice data contains a week-over-week paid MRR drop.
- The backend can detect that anomaly from invoice and subscription rows.
- The app can persist an incident from anomaly data.
- The incident detail page shows metric evidence, affected accounts, support
  tickets, product signals, and evidence-source descriptions.
- Tests prove the anomaly and incident behavior without any agent behavior yet.

You should be able to explain:

- Why this slice uses paid invoices for anomaly detection instead of only active
  subscription MRR.
- How deterministic anomaly IDs make incident creation idempotent.
- What evidence is stored on the incident and what is re-queried at read time.
- How the frontend links from anomaly detection to incident review.
- Which tests prove the product behavior versus just checking implementation.

## 1. Incident Model And Migration

Start with:

- `apps/api/app/models.py`
- `apps/api/alembic/versions/20260611_0002_add_incidents.py`

Key ideas:

- `Incident` is the durable record for a business investigation.
- `affected_account_ids` and `evidence` are JSON fields because this first slice
  needs a compact evidence bundle without adding many join tables yet.
- `metric_name`, `current_value_cents`, `previous_value_cents`, `delta_cents`,
  and `delta_percent` make the incident auditable without rerunning the detector.
- `status`, `severity`, `anomaly_type`, `source_scenario`, and `detected_at` are
  indexed because they are natural review and triage fields.
- The migration is the real database contract. The ORM model alone is not
  enough.

Review questions:

- Does the migration match the `Incident` model fields?
- Are JSON fields justified here, or should any field be normalized already?
- Can a reviewer understand the incident without an LLM-generated report?
- Would the incident still be useful if the anomaly detector changed later?

## 2. Anomaly Constants And Window Semantics

Read:

- `apps/api/app/incidents/constants.py`

Key ideas:

- `REVENUE_MRR_DROP_ANOMALY_TYPE` names the kind of anomaly.
- `PAID_INVOICE_MRR_METRIC` names the metric being compared.
- `revenue_week_windows(...)` turns the dataset anchor into deterministic
  current and previous seven-day windows.
- `anomaly_id_for_window(...)` and `incident_id_for_anomaly(...)` create stable
  IDs from the detection window.

This is similar to keeping route names, enum values, and ID builders in one
place in a TypeScript app.

Review questions:

- What is the current anomaly window in the seeded data?
- Why does this code use `DATASET_ANCHOR` indirectly instead of the current wall
  clock?
- What would break if anomaly IDs were random?
- Are the ID formats readable enough for debugging?

## 3. Revenue Anomaly Detection

Read:

- `apps/api/app/incidents/service.py`
- `apps/api/app/metrics/router.py`

The important function is `detect_revenue_anomalies(...)`.

Key ideas:

- The detector compares paid invoice MRR in the current seven-day window against
  paid invoice MRR in the previous seven-day window.
- It only emits an anomaly when previous paid MRR is positive, current paid MRR
  is lower, and there are failed current-window renewal invoices.
- Affected accounts come from failed invoices joined to active subscriptions and
  accounts.
- Support and product signals are gathered for affected accounts in the recent
  window.
- `/metrics/anomalies` exposes the detector result as a reviewable API
  response.

Review questions:

- Which SQL filters define the current and previous windows?
- Why does the detector require failed invoices instead of reporting any drop?
- Which account fields make the anomaly operationally useful?
- What false positives might this simple detector still allow?

## 4. Incident Creation

Read:

- `apps/api/app/incidents/router.py`
- `apps/api/app/incidents/service.py`
- `apps/api/app/incidents/schemas.py`

Key ideas:

- `POST /incidents` accepts an `anomaly_id`.
- `create_or_get_incident_from_anomaly(...)` is idempotent: posting the same
  anomaly twice returns the same incident.
- If the incident already exists, the API returns it instead of duplicating it.
- If the anomaly does not exist, the API returns a 404.
- `IncidentDetail` is the response shape used by the frontend detail page.

Review questions:

- Where is idempotency enforced?
- What response status is returned for first creation versus repeat creation?
- What evidence is persisted when the incident is created?
- What evidence is recalculated when the incident is viewed?

## 5. Seeded Scenario Changes

Read:

- `apps/api/app/seed.py`
- `README.md`

Key ideas:

- The checkout retry regression accounts now have May invoices dated in the
  previous week and June failed invoices dated in the current week.
- The seed creates one open incident so the UI has an immediate review target.
- `clear_domain_data(...)` deletes `Incident` first so reseeding remains safe
  and deterministic.
- Seed counts now include `incidents: 1`.
- The seed still keeps other scenarios, noisy failures, tickets, and product
  events so the data is not a one-row toy case.

Review questions:

- Which accounts are affected by the seeded checkout retry regression?
- Why are invoice dates moved instead of using the first day of each month?
- Does reseeding create duplicate incidents?
- Does the seeded incident cite invoice IDs and account IDs that really exist?

## 6. Backend Tests

Read:

- `apps/api/tests/test_anomalies_and_incidents.py`
- `apps/api/tests/test_seed_and_metrics.py`

Key ideas:

- Tests use SQLite for fast behavior checks.
- `test_revenue_anomaly_detects_seeded_week_over_week_mrr_drop` proves the
  detector finds the seeded drop and attaches related signals.
- `test_anomalies_endpoint_returns_reviewable_incident_starting_point` proves
  the API contract is useful for the UI.
- `test_incident_creation_from_anomaly_is_idempotent` proves repeated creation
  does not duplicate incidents.
- `test_incident_detail_endpoint_shows_accounts_and_metric_evidence` proves the
  detail endpoint has the evidence a reviewer needs.
- Existing seed tests now expect one incident.

Review questions:

- Are the tests checking product claims or implementation details?
- Would the tests fail if the anomaly detector stopped citing invoices?
- Would the tests fail if incident creation duplicated records?
- What behavior is still only covered by browser smoke instead of automated
  frontend tests?

## 7. Frontend API Contracts

Read:

- `apps/web/lib/api.ts`
- `apps/web/lib/format.ts`

Key ideas:

- TypeScript types mirror Pydantic response schemas from the backend.
- `getRevenueAnomalies()` fetches `/metrics/anomalies`.
- `createIncidentFromAnomaly(...)` posts to `/incidents`.
- `getIncident(...)` fetches `/incidents/{incident_id}`.
- Formatting helpers were moved to `format.ts` because both dashboard and
  incident pages need money, percent, date, count, and scenario formatting.

Review questions:

- Do the TypeScript field names match the backend JSON field names?
- What happens if `/metrics/anomalies` is unavailable?
- Which frontend function performs a write?
- Are formatting helpers pure and easy to reuse?

## 8. Dashboard Anomaly UI

Read:

- `apps/web/app/page.tsx`
- `apps/web/app/actions.ts`
- `apps/web/app/globals.css`

Key ideas:

- The dashboard remains the first screen. This is still an operations workspace,
  not a marketing page.
- `AnomalyPanel` shows detected revenue anomalies above the metric cards.
- If an anomaly already has an incident, the UI shows `View incident`.
- If not, the server action `openIncidentFromAnomaly(...)` creates one and
  redirects to the incident page.
- The anomaly row summarizes the metric delta, current window, failed renewal
  count, failed amount, and affected accounts.

Review questions:

- Can a user see the incident starting point without knowing agent internals?
- Does the dashboard distinguish anomaly evidence from general metrics?
- Is the call-to-action idempotent from the user's perspective?
- Does the mobile layout keep the anomaly action visible and readable?

## 9. Incident Detail UI

Read:

- `apps/web/app/incidents/[incidentId]/page.tsx`
- `apps/web/app/globals.css`

Key ideas:

- The incident page is useful before any agent exists.
- The top snapshot shows status, severity, detected time, MRR delta, drop
  percent, and affected account count.
- Metric evidence shows current paid invoice MRR, previous paid invoice MRR,
  failed renewal amount, failed invoice count, and invoice IDs.
- Affected accounts are listed with segment, failed amount, and health score.
- Support and product signals make the incident operational instead of just a
  financial chart.
- Evidence sources explain the query surfaces used to build the incident.

Review questions:

- Could an ops lead decide what to inspect next from this page?
- Are invoice IDs and account names visible enough to audit the claim?
- Does the page avoid pretending an agent already diagnosed root cause?
- What additional evidence would you want before allowing follow-up drafts?

## 10. Verification Commands

These are the commands worth understanding:

```bash
cd apps/api
./.venv/bin/alembic upgrade head
./.venv/bin/python -m app.seed --json
./.venv/bin/python -m pytest
```

```bash
cd apps/web
npm run lint
npm run build
```

Manual browser checks:

- Open `http://localhost:3000`.
- Confirm `Detected revenue anomalies` shows the seeded MRR drop.
- Click `View incident`.
- Confirm the incident page shows metric evidence, affected accounts, support
  signals, product signals, and evidence sources.
- Resize to mobile width and check that sections stack without overlap.

What each command proves:

- `alembic upgrade head`: the incident table can be created in Postgres.
- `python -m app.seed --json`: deterministic data includes the seeded incident.
- `pytest`: anomaly detection, incident creation, and existing metrics pass.
- `npm run lint`: TypeScript contracts and route types are valid.
- `npm run build`: the dashboard and incident route compile for production.

## 11. What To Learn Before Reviewing This Change

Focus on these topics in order:

1. SQL date windows: understand inclusive start and exclusive end filters.
2. SQL joins and grouping: invoices joined to subscriptions and accounts, then
   grouped by affected account.
3. SQLAlchemy query style: `select(...)`, `.join(...)`, `.where(...)`,
   `func.sum(...)`, `func.count(...)`, and `session.execute(...)`.
4. Pydantic response models: how Python schemas become JSON API contracts.
5. Alembic migrations: how schema changes are applied and reversed.
6. Idempotent POST design: creating a resource from a stable natural key without
   duplicating it.
7. Deterministic seed data: why fixed IDs and fixed dates matter for evals.
8. Next.js App Router server components: async pages that fetch backend data.
9. Next.js server actions: form submission that performs a backend write and
   redirects.
10. Frontend contract review: checking TypeScript types against backend schemas.
11. Product-minded test review: asking whether tests prove user-visible claims.
12. Evidence quality: distinguishing metrics, symptoms, affected accounts, and
    source citations.

If you can explain those twelve topics in this repo's code, you can review the
Week 2 changes with useful judgment instead of only checking syntax.
