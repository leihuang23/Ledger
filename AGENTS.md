# AGENTS.md

This repository is for a production-shaped SaaS revenue and support operations agent. The current source of truth is `prd.md`; treat it as the product brief, not as a complete implementation plan.

## Critical Product Read

The project idea is strong because it demonstrates the hard parts of agentic software that hiring managers and technical reviewers actually care about: cross-source investigation, evidence-backed claims, constrained actions, run traces, and evals. The demo prompt, "MRR dropped this week," is a good anchor because it is concrete, business-relevant, and naturally requires analytics plus support context.

The main risk is scope inflation. The recommended stack is credible but heavy for a first pass: Next.js, FastAPI, PostgreSQL, pgvector, Redis, LangGraph, LangSmith, Celery, Docker, and split deployment can easily become infrastructure theater before the agent proves anything. Prefer a narrow, verified system over a broad, half-working one.

The second risk is fake intelligence. A fluent final report is not enough. The product must prove that the agent found the right root cause from seeded data, cited concrete evidence, respected approval boundaries, and produced a trace that a reviewer can audit.

The third risk is unrealistic seed data. If scenarios are too obvious or linear, the project becomes another toy demo. Seed data should include confounders, noisy support tickets, partial outages, invoice timing, plan changes, churn, failed payments, and usage changes that require actual analysis.

## Product Guardrails

- Optimize for a reviewer evaluating engineering judgment, not for a flashy agent demo.
- Keep the first version focused on the primary investigation loop: anomaly -> evidence gathering -> root cause -> affected accounts -> recommended actions -> approval-gated drafts.
- Every important claim in the UI, API, final report, and eval output must be backed by cited SQL results, tickets, documents, or incident records.
- Never let the agent perform irreversible or external write actions without explicit approval. In the first version, all Slack, email, CRM, and task actions must be mocks.
- Treat seeded scenarios and eval cases as product-critical assets, not test fixtures of convenience.
- Prefer deterministic analytics and explicit tool results before LLM summarization. The LLM should synthesize evidence, not invent the evidence.
- Do not introduce real customer data or real third-party integrations unless the PRD is explicitly updated to allow them.

## MVP Definition

Build the smallest credible system that can pass the PRD success criteria:

- At least 5 seeded incident scenarios.
- At least 4 of 5 eval scenarios correctly identify the intended root cause.
- Final reports cite SQL queries, support tickets, docs, or incidents for every major claim.
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
- Do not add dependencies only because they are listed in the PRD. Add each dependency when a real implementation need appears.

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

- Read `prd.md` before making product or architecture changes.
- Challenge requests that weaken evidence, evals, approval gating, or auditability.
- Keep diffs small and behavior-oriented.
- Prefer deleting unnecessary complexity over adding new layers.
- If the implementation direction conflicts with these instructions, update the PRD and this file together so future agents do not inherit drift.
