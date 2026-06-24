# Week 3 Learning Guide: Knowledge Base and RAG

This guide is for reviewing the Week 3 slice as a JavaScript full-stack
developer who wants to understand how unstructured internal knowledge becomes
searchable, citable evidence for future agent reports.

The goal is not to build a full enterprise search system yet. The goal is to
add a credible internal knowledge base that can ingest Markdown, chunk it,
embed it, search it semantically, and return citations that an agent can use
without inventing sources.

## What This Slice Proves

The slice adds the first unstructured evidence source:

- At least 20 internal Markdown documents are available as seeded knowledge.
- Markdown front matter becomes document metadata.
- Markdown sections become searchable chunks with heading context.
- Chunk embeddings are stored in the database through a pgvector-compatible
  field.
- Search returns source IDs, titles, snippets, scores, and citation metadata.
- The frontend has a small `/knowledge` surface for searching and inspecting
  cited chunks.
- Tests protect ingestion, metadata, search response shape, and operator access
  for document refreshes.

You should be able to explain:

- Why knowledge documents and chunks are separate tables.
- Why every search result needs citation metadata, not just text.
- How deterministic local embeddings make tests and demo data reproducible.
- How the ingestion path refreshes changed docs without duplicating chunks.
- Why the HTTP ingestion endpoint is disabled unless an operator token is set.
- Which docs support the seeded MRR-drop incident.

## 1. Knowledge Models And Migration

Start with:

- `apps/api/app/models.py`
- `apps/api/app/db/types.py`
- `apps/api/alembic/versions/20260612_0003_add_knowledge_documents.py`
- `docker-compose.yml`

Key ideas:

- `KnowledgeDocument` is the source-level record: title, type, owner,
  source path, source URI, checksum, full content, and metadata.
- `KnowledgeDocumentChunk` is the retrieval unit: one section-sized excerpt,
  heading path, token estimate, embedding, and citation metadata.
- The migration creates `knowledge_documents` and `knowledge_document_chunks`.
- Postgres uses pgvector so chunk embeddings can be indexed and queried by
  vector distance.
- SQLite tests use a compatible fallback representation so behavior tests stay
  fast and local.
- Docker now uses the `pgvector/pgvector:pg16` image because plain Postgres
  does not include the vector extension.

Review questions:

- Does the migration match the SQLAlchemy models?
- Why is full document content stored separately from chunk content?
- Which fields would a future agent report cite?
- What breaks if the database image does not include pgvector?
- Is the embedding dimension declared consistently across model, migration, and
  embedding code?

## 2. Built-In Markdown Documents

Read:

- `apps/api/app/knowledge/docs`

The built-in docs cover runbooks, pricing notes, support macros, incident
response docs, and product troubleshooting notes. This is deliberately broader
than the seeded billing incident so search has useful false leads and adjacent
context.

Important examples:

- `mrr-drop-investigation-runbook.md`
- `billing-retry-regression-runbook.md`
- `failed-renewals-triage.md`
- `incident-response-billing-webhook.md`
- `support-macro-billing-retry.md`
- `pricing-renewal-timing.md`
- `runbook-citation-quality.md`

Key ideas:

- Each document has front matter with a stable `source_id`, title,
  `document_type`, owner, and tags.
- Stable source IDs are the durable reference future agent reports should cite.
- The docs are realistic internal knowledge, not public documentation.
- The seeded billing incident should retrieve billing retry, failed renewal,
  webhook, and MRR investigation docs for relevant queries.

Review questions:

- Are there at least 20 documents?
- Do source IDs read like stable internal identifiers?
- Are docs varied enough to avoid one obvious answer for every query?
- Do the docs mention operational details a real support or revenue team would
  need?
- Which docs should rank highly for `retry webhook failed renewal MRR drop`?

## 3. Markdown Chunking

Read:

- `apps/api/app/knowledge/ingestion.py`
- `apps/api/tests/test_knowledge.py`

Key ideas:

- `chunk_markdown(...)` parses front matter and Markdown headings.
- Chunks preserve heading context through `heading_path`.
- Chunk IDs combine source ID and chunk index, such as
  `kb-runbook-billing-retry-regression#chunk-000`.
- Chunk text is bounded by a character target so retrieval returns focused
  excerpts instead of whole documents.
- Token count is approximate, but good enough for chunk sizing and UI context.

This is similar to splitting a long help-center article into searchable
sections while keeping the breadcrumb for each section.

Review questions:

- Does front matter become metadata before chunking?
- Does a chunk know which document and heading it came from?
- Are snippets small enough to inspect in the UI?
- Would citation metadata still be useful if two chunks have similar text?
- What happens to chunks when a document changes?

## 4. Embeddings

Read:

- `apps/api/app/knowledge/embeddings.py`
- `apps/api/app/core/config.py`
- `.env.example`
- `apps/api/.env.example`

Key ideas:

- The default embedding provider is `local`.
- The default embedding model is `local-hashing-v1`.
- Local embeddings are deterministic, require no network access, and make tests
  repeatable.
- Unsupported embedding providers fail fast during settings validation instead
  of silently pretending external embeddings are configured.
- The current dimension is intentionally small for the demo slice, but still
  exercises the database and API contract shape.

Environment variables:

- `EMBEDDING_PROVIDER=local`
- `EMBEDDING_MODEL=local-hashing-v1`
- `DOCUMENT_INGEST_TOKEN` is optional and only enables the protected HTTP
  ingestion endpoint.

Review questions:

- Why is deterministic embedding behavior valuable for this repo?
- Where would a future external embedding provider plug in?
- What tests would need updates if the embedding dimension changed?
- Does the app fail clearly if someone sets an unsupported provider?
- Is local embedding good enough for demo retrieval without claiming production
  semantic quality?

## 5. Ingestion Path

Read:

- `apps/api/app/knowledge/ingestion.py`
- `apps/api/app/seed.py`
- `apps/api/app/main.py`
- `apps/api/app/knowledge/router.py`

Key ideas:

- Built-in docs are ingested during bootstrap and seed flows.
- Ingestion computes checksums so unchanged documents do not need to be
  refreshed.
- Changed built-in docs are reconciled by source ID and source path.
- Removed built-in docs are pruned so stale citations do not linger forever.
- Refreshing a document replaces its chunks and embeddings.
- The HTTP `POST /documents/ingest` endpoint is protected by
  `DOCUMENT_INGEST_TOKEN` and a bootstrap lock.

This matters because ingestion is a write path. Even in a demo app, a mutating
endpoint should not be open just because read-only demo data access is allowed.

Review questions:

- Is ingestion idempotent when run twice?
- Does a changed checksum refresh the document and chunks?
- Are removed built-in documents deleted?
- Why should bootstrap ingestion not skip work based only on table counts?
- Why does the HTTP ingestion endpoint require a separate operator token?

## 6. Semantic Search API

Read:

- `apps/api/app/knowledge/search.py`
- `apps/api/app/knowledge/schemas.py`
- `apps/api/app/knowledge/router.py`

Key ideas:

- `POST /documents/search` accepts a query and limit.
- Search embeds the query with the same embedding code used for chunks.
- Results include source ID, title, snippet, score, and typed citation data.
- `KnowledgeCitation` defines the citation contract instead of returning
  arbitrary JSON.
- The API returns chunks, not whole documents, because future agent reports need
  concise evidence.

Expected citation fields:

- `source_id`
- `chunk_id`
- `title`
- `document_type`
- `heading_path`
- `source_path`
- `source_uri`
- `chunk_index`
- `tags`

Review questions:

- Does every result have a stable source ID?
- Can the frontend render a useful snippet without another request?
- Is the citation shape strict enough for future agent reports?
- Does the score represent distance or relevance clearly enough?
- What should happen when there are no relevant documents?

## 7. Knowledge Search UI

Read:

- `apps/web/app/knowledge/page.tsx`
- `apps/web/lib/api.ts`
- `apps/web/app/page.tsx`
- `apps/web/app/incidents/[incidentId]/page.tsx`
- `apps/web/app/globals.css`

Key ideas:

- `/knowledge` is a compact operational search surface, not a landing page.
- The UI lets a reviewer search internal docs and inspect cited chunks.
- Results show source ID, title, document type, heading path, tags, snippet,
  and source path.
- Dashboard and incident pages link to the knowledge search surface.
- TypeScript response types mirror the backend search schemas.

Review questions:

- Can a reviewer tell which internal document a snippet came from?
- Are source IDs visible enough for auditability?
- Does the page work when search returns no results?
- Are long snippets, tags, and paths readable on narrow screens?
- Does the UI make citations obvious instead of burying them as decoration?

## 8. Backend Tests

Read:

- `apps/api/tests/test_knowledge.py`
- `apps/api/tests/test_seed_and_metrics.py`

Key ideas:

- Chunking tests prove heading context and front matter metadata survive.
- Ingestion tests prove at least 20 docs are stored with chunks and citations.
- Refresh tests prove stale docs update and removed built-in docs are pruned.
- Search tests prove `/documents/search` returns source IDs and the citation
  shape expected by future agent reports.
- Ingest endpoint tests prove refresh requires an explicit operator token.
- Settings tests prove unsupported embedding providers fail fast.
- Existing seed tests now include knowledge document and chunk counts.

Review questions:

- Are tests checking product claims or mirroring implementation details?
- Would tests fail if search stopped returning source IDs?
- Would tests fail if citation metadata lost `heading_path` or `source_path`?
- Would tests catch duplicate ingestion after repeated seeds?
- Are the tests strong enough to prevent fake citations?

## 9. Operational Gotchas From This Slice

These are the lessons worth carrying forward:

- Do not use plain `postgres:16-alpine` when migrations require pgvector.
- Do not treat "some docs exist" as proof that the built-in corpus is current.
- Do not leave a mutating ingestion endpoint available with only broad demo
  data access.
- Do not accept unsupported embedding env vars silently.
- Do not return untyped arbitrary citation JSON if future agent reports will
  depend on it.
- Do not run Next.js lint and build against the same `.next` directory in
  parallel; generated type files can race each other.
- Clean generated Python bytecode before commit if `__pycache__` appears in new
  source folders.

Review questions:

- Which of these gotchas are product risks versus developer-experience risks?
- Which ones should become tests?
- Which ones should stay as reviewer checklist items?

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
npm test
npm run lint
npm run build
```

Manual search checks:

```bash
curl -X POST http://localhost:8000/documents/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"retry webhook failed renewal MRR drop","limit":5}'
```

What each command proves:

- `alembic upgrade head`: a clean database can create the knowledge tables and
  pgvector-backed embedding column.
- `python -m app.seed --json`: seeded SaaS data and built-in knowledge ingest
  together.
- `pytest`: chunking, ingestion, metadata, search contract, operator token, and
  prior metric behavior pass.
- `npm test`: frontend API helpers and knowledge UI behavior pass.
- `npm run lint`: TypeScript and route checks pass.
- `npm run build`: the dashboard, incident page, and knowledge route compile for
  production.
- Manual search: seeded incident terms return useful billing retry, failed
  renewal, and webhook citations.

## 11. What To Learn Before Reviewing This Change

Focus on these topics in order:

1. pgvector basics: vector columns, dimensions, and approximate nearest-neighbor
   indexes.
2. SQLAlchemy custom types: how Postgres and SQLite can share one model with
   dialect-specific storage.
3. Markdown front matter: how metadata travels from file to database to API.
4. Chunking strategy: why retrieval should return focused excerpts with heading
   context.
5. Deterministic embeddings: why local hashing is useful for tests and demos.
6. Idempotent ingestion: checksums, stable source IDs, refreshes, and pruning.
7. API contract design: strict Pydantic schemas for search results and
   citations.
8. Access control for writes: why ingestion is separate from ordinary demo data
   reads.
9. Frontend contract review: checking TypeScript types against Pydantic schemas.
10. Evidence quality: source IDs, snippets, heading paths, and source paths as
    report citations.
11. Operational verification: clean database migration, seed ingestion, targeted
    tests, and manual search.

If you can explain those eleven topics in this repo's code, you can review the
Knowledge Base and RAG changes with useful judgment instead of only checking
that the search box renders.
