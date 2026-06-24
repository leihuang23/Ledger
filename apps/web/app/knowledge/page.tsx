import Link from 'next/link';

import type { KnowledgeSearchItem } from '@/lib/api';
import { searchKnowledge } from '@/lib/api';
import { formatCount } from '@/lib/format';

type KnowledgePageProps = {
  searchParams?: Promise<{
    q?: string;
  }>;
};

export default async function KnowledgePage({ searchParams }: KnowledgePageProps) {
  const resolvedSearchParams = await searchParams;
  const query = typeof resolvedSearchParams?.q === 'string' ? resolvedSearchParams.q : '';
  const result = await searchKnowledge(query);
  const resultCount = result.ok ? result.data.results.length : 0;

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Knowledge base</p>
          <h1>Internal source search</h1>
        </div>
        <div className="header-actions">
          <span className="status-pill incident-status">
            {formatCount(resultCount)} chunks
          </span>
          <Link className="action-button secondary-action" href="/">
            Dashboard
          </Link>
        </div>
      </header>

      <section className="panel knowledge-search-panel">
        <form className="knowledge-search-form">
          <input
            aria-label="Search knowledge base"
            className="knowledge-search-input"
            defaultValue={query}
            name="q"
            placeholder="retry webhook failed renewals"
            type="search"
          />
          <button className="action-button" type="submit">
            Search
          </button>
        </form>
      </section>

      {!result.ok ? (
        <section className="empty-state">
          <h2>Knowledge search unavailable</h2>
          <p className="error-detail">{result.error}</p>
        </section>
      ) : result.data.query ? (
        <section className="knowledge-results" aria-label="Knowledge search results">
          {result.data.results.length === 0 ? (
            <div className="empty-state">
              <h2>No matching chunks</h2>
              <p>No internal document chunks matched this query.</p>
            </div>
          ) : (
            result.data.results.map((item) => (
              <KnowledgeResult item={item} key={item.citation.chunk_id} />
            ))
          )}
        </section>
      ) : null}
    </main>
  );
}

function KnowledgeResult({ item }: { item: KnowledgeSearchItem }) {
  return (
    <article className="panel knowledge-result">
      <div className="panel-header">
        <div>
          <span className="label">{item.source_id}</span>
          <h2>{item.title}</h2>
        </div>
        <span>{Math.round(item.score * 100)} score</span>
      </div>
      <p className="knowledge-snippet">{item.snippet}</p>
      <dl className="citation-grid">
        <div>
          <dt>Chunk</dt>
          <dd>{item.citation.chunk_id}</dd>
        </div>
        <div>
          <dt>Type</dt>
          <dd>{item.citation.document_type}</dd>
        </div>
        <div>
          <dt>Heading</dt>
          <dd>{item.citation.heading_path}</dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>{item.citation.source_path}</dd>
        </div>
      </dl>
    </article>
  );
}
