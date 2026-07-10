# Five-Minute Portfolio Demo

This script is the narration track for `docs/assets/ops-agent-walkthrough.webm`. It uses only synthetic seeded data and the local deterministic execution path, so a reviewer can reproduce the same evidence without API keys.

## Before the recording

1. Copy the three example environment files. Export matching non-empty `DEMO_OPERATOR_TOKEN` and `EVAL_RUN_TOKEN` values, and set `OPERATOR_UI_ENABLED=true`, for this local/protected recording session.
2. Start the stack with `docker compose up -d --build` and wait for the API, Postgres, Redis, and web health checks.
3. Run `cd apps/web && npm run portfolio:assets`. The script performs the workflow in the browser and waits for fresh run/eval jobs; it does not reuse partial eval rows or pre-provision the result before recording.

## 0:00–0:30 — Frame the problem

Open `/`.

> “A fluent agent answer is not enough. This workspace starts with deterministic revenue and support evidence, then makes the agent version, tool calls, citations, approvals, traces, cost, and eval results inspectable.”

Point out the seeded MRR movement, failed invoices, support signals, and incident links. These are business facts, not LLM-generated claims.

## 0:30–1:05 — Version and govern the agent

Open the published Phase 6 baseline (`revenue-ops-agent_phase6` on migrated deployments), choose **New draft from this version**, remove `search_docs`, keep `run_eval` and its scope enabled, save, and publish the candidate.

> “Published versions are immutable snapshots of the prompt, model, enabled tools, and allowed scopes. A run always points back to the version that produced it.”

Open `/tools` and confirm the candidate choices came from the same seven-entry registry.

> “The tool surface is explicit. Each binding has input/output schemas, an implementation reference, and one fixed permission scope. Runtime policy requires both an enabled tool id and an allowed scope.”

## 1:05–2:00 — Launch and audit a run

From the newly published version, launch the seeded MRR-drop incident. Open the resulting `/runs/<id>` page and find the blocked `search_docs` step.

> “The run records every stage and tool call in order. The report is available with a root cause, affected accounts, and citations to SQL results, support tickets, and knowledge documents. Failures and blocked calls appear in the same timeline rather than disappearing into server logs.”

Point out:

- published agent version;
- succeeded or waiting-for-approval status;
- cited evidence;
- local or hosted trace link;
- token counts and estimated cost label;
- ordered step status and duration.

## 2:00–2:35 — Show the action boundary

Open `/approvals` and filter to **Pending** and **High risk**.

> “Customer-facing follow-up remains a mock action. A high-risk action cannot execute until an operator approves it, rejection is terminal, and every transition writes an audit event.”

Reject both pending requests in the disposable local recording database and show the recorded decisions/audit state. The run must reach terminal `succeeded` before the eval sequence starts.

## 2:35–3:10 — Inspect operations

Open `/dashboard`.

> “The control-plane dashboard aggregates total runs, success rate, average and p95 latency, and estimated cost per published version. Every aggregate drills back into the underlying run list.”

Emphasize that cost is an estimate, not billing precision.

## 3:10–4:20 — Prove quality with A-vs-B evals

Open `/evals`. Trigger a fresh `mrr-drop-suite` run for `revenue-ops-agent_phase6`, then trigger a fresh run for the candidate created earlier. Wait for both terminal summaries and compare them.

> “The same six seeded incidents run against both versions. Results persist per case with root-cause accuracy, citation quality, action safety, latency, cost, trace, and failure reasons.”

Show the regression banner and the case marked **Regression**.

> “The candidate intentionally lacks a required evidence tool. A case that passed on the Phase 6 baseline flips to fail, which makes the release risk obvious rather than hiding it inside an average score.”

## 4:20–5:00 — Close with the engineering claim

Return to `/runs` or the successful run detail.

> “The portfolio claim is not that the model sounds smart. It is that the system gathers the right evidence, enforces a constrained tool and action boundary, records an auditable trace, and detects behavioral regressions before release.”

## Recovery notes

- If a run is already active for the incident, reset the disposable synthetic seed before rerunning the capture; the script fails instead of recording a competing run.
- The capture command always enqueues fresh eval runs. A stale partial run is ignored rather than reused; if a worker dies, restart the worker and rerun the command.
- If hosted tracing is not configured, `local://agent-runs/...` is the expected trace fallback.
- If mutations return `403` in `demo`, verify the same server-only tokens are configured in the API and web processes and `OPERATOR_UI_ENABLED=true` is set only for the protected recording environment.
