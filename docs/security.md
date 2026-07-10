# Security Model and OWASP LLM Risk Mapping

This is a portfolio demo with synthetic data. Its public surface is read-only; a separately protected single-operator deployment can enable mutations for recordings. Its primary security objective is to prove constrained agent execution and auditable approvals, not to claim enterprise identity or tenant isolation.

## Trust boundaries

1. **Browser → Next.js:** public pages may read synthetic data. Server actions fail before forwarding credentials unless `OPERATOR_UI_ENABLED=true`; that setting is reserved for an authenticated/protected operator deployment. Secrets are not shipped to the client bundle.
2. **Next.js → FastAPI:** demo mutations require server-owned tokens. The API compares them with `secrets.compare_digest` and fails closed when a required token is unset.
3. **Agent version → tool runtime:** both the tool id and its permission scope must be present in the published version snapshot before dispatch.
4. **Workflow → external action:** the workflow creates mock actions only. High-risk actions create pending approvals and cannot execute before a recorded decision.
5. **Application → tracing provider:** hosted payloads default to metadata and summaries. Raw evidence export is opt-in.

## OWASP LLM risk mapping

### Prompt injection

- The Revenue Ops Agent uses a fixed linear graph and a closed implementation registry.
- Tool inputs and outputs are typed; a retrieved document cannot introduce a new tool or callable.
- Published versions freeze the prompt, enabled tools, and scopes used by a run.
- Residual risk: retrieved text can still influence LLM synthesis. The deterministic fallback and citation checks reduce impact but do not replace adversarial prompt-injection evals.

### Sensitive information disclosure

- All seeded records are synthetic and clearly portfolio data.
- API/provider keys and mutation tokens are server-only environment variables.
- `OBSERVABILITY_FULL_PAYLOADS=false` is the default and the Render Blueprint preserves it.
- Error envelopes return request ids, not stack traces or secrets.
- Residual risk: enabling full hosted payloads exports synthetic evidence to the configured provider and must be an explicit operator decision.

### Improper output handling

- LLM reports are parsed into Pydantic schemas; malformed or unsupported output falls back or fails visibly.
- React renders report strings as text rather than trusted HTML.
- No model output is executed as shell, SQL, Python, or a dynamic implementation reference.
- External side effects are mock records behind policy and approval checks.

### Excessive agency

- A tool is callable only when its id is enabled and its fixed scope is allowed by the published version.
- Blocked calls persist as visible failed/blocked run steps with a reason.
- High-risk actions remain pending until an operator decision; rejected actions are terminal.
- Project scope explicitly excludes real email, Slack, CRM, and payment integrations.

### Vector and embedding weaknesses

- The built-in corpus is bounded, version-controlled Markdown with source/chunk citation metadata.
- Anonymous ingestion is disabled; HTTP ingestion requires `DOCUMENT_INGEST_TOKEN`.
- Local hashing embeddings make the default path deterministic and offline.
- Residual risk: the current corpus does not include poisoning or cross-tenant retrieval tests because v1 has no tenant model.

### Misinformation

- Major report claims must reference retrieved SQL, ticket, document, product-event, or incident evidence.
- The eval suite scores root-cause match, citation coverage, and action safety rather than prose fluency.
- An ambiguity scenario requires the agent to state uncertainty when evidence is incomplete.
- Residual risk: deterministic root-cause signatures do not grade every semantically equivalent explanation.

### Unbounded consumption

- Mutation/search rate limits use Redis-backed SlowAPI rules.
- LLM max tokens and timeouts are bounded in settings.
- Celery has soft and hard task limits, and stale runs self-heal to failed.
- Eval execution has its own token gate and runs asynchronously.
- The dashboard labels token-derived cost as an estimate and makes regressions/cost visible per version.

## Deployment checklist

- Keep `APP_ENV=demo` on any public portfolio deployment.
- Keep `OPERATOR_UI_ENABLED=false` and omit operator/eval tokens from anonymous public frontend deployments.
- Enable the operator UI and copy server-only tokens only after deployment authentication protects the whole frontend.
- Set non-empty, independent operator, eval, and ingestion tokens.
- Copy tokens only into protected server-side service settings; never use `NEXT_PUBLIC_` for secrets.
- Restrict CORS to the deployed frontend origin.
- Keep Postgres and Key Value on private networking.
- Keep hosted trace payloads redacted unless synthetic export is intentional.
- Rotate tokens after recordings or public review sessions.
- Do not connect real customer data or real action providers without revising the PRD, authorization model, retention policy, and threat model.
