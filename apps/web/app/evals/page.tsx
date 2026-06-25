import Link from 'next/link';

import { runEvalSuiteFromReport } from '@/app/actions';
import type { EvalResult } from '@/lib/api';
import { getEvalResults } from '@/lib/api';
import { formatCount, formatDateTime, formatPercent, formatScenario } from '@/lib/format';

type EvalPageProps = {
  searchParams?: Promise<{
    eval_error?: string;
  }>;
};

export default async function EvalReportPage({ searchParams }: EvalPageProps) {
  const resolvedSearchParams = await searchParams;
  const evalError =
    typeof resolvedSearchParams?.eval_error === 'string'
      ? resolvedSearchParams.eval_error
      : null;
  const result = await getEvalResults();

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Regression Evals</p>
          <h1>Investigation eval report</h1>
        </div>
        <div className="header-actions">
          <form action={runEvalSuiteFromReport}>
            <button className="action-button" type="submit">
              Run Suite
            </button>
          </form>
          <Link className="action-button secondary-action" href="/">
            Dashboard
          </Link>
        </div>
      </header>

      {evalError ? (
        <section className="panel anomaly-panel" aria-live="polite">
          <div className="panel-message error-detail">{evalError}</div>
        </section>
      ) : null}

      {!result.ok ? (
        <section className="empty-state">
          <h2>Eval results unavailable</h2>
          <p className="error-detail">{result.error}</p>
        </section>
      ) : result.data.results.length === 0 ? (
        <section className="empty-state">
          <h2>No eval results yet</h2>
          <p>No persisted scenario scores are available.</p>
        </section>
      ) : (
        <EvalReport results={result.data.results} />
      )}
    </main>
  );
}

function EvalReport({ results }: { results: EvalResult[] }) {
  const passed = results.filter((result) => result.passed).length;
  const failed = results.length - passed;
  const latestCompletedAt = results
    .map((result) => result.completed_at)
    .sort()
    .at(-1);
  const evalRunId = results[0]?.eval_run_id;

  return (
    <>
      <section className="snapshot-bar">
        <div>
          <span className="label">Eval run</span>
          <strong>{evalRunId}</strong>
        </div>
        <div>
          <span className="label">Pass rate</span>
          <strong>{formatPercent((passed / results.length) * 100)}</strong>
        </div>
        <div>
          <span className="label">Scenarios</span>
          <strong>
            {formatCount(passed)} passed / {formatCount(failed)} failed
          </strong>
        </div>
        <div>
          <span className="label">Completed</span>
          <strong>{latestCompletedAt ? formatDateTime(latestCompletedAt) : 'unknown'}</strong>
        </div>
      </section>

      <section className="panel table-panel eval-results-panel">
        <div className="panel-header">
          <h2>Scenario results</h2>
          <span>{formatCount(results.length)} scenarios</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Scenario</th>
                <th>Status</th>
                <th>Root cause</th>
                <th>Citations</th>
                <th>Safety</th>
                <th>Latency</th>
                <th>Trace</th>
              </tr>
            </thead>
            <tbody>
              {results.map((result) => (
                <tr key={result.id}>
                  <td>{formatScenario(result.scenario)}</td>
                  <td>
                    <span className={`run-status run-status-${result.status}`}>
                      {result.status}
                    </span>
                  </td>
                  <td>{formatPercent(result.root_cause_score * 100)}</td>
                  <td>{formatPercent(result.citation_quality_score * 100)}</td>
                  <td>{formatPercent(result.action_safety_score * 100)}</td>
                  <td>{formatCount(result.latency_ms)} ms</td>
                  <td>{traceLink(result)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="eval-detail-grid">
        {results.map((result) => (
          <article className="panel eval-result-card" key={`${result.id}-detail`}>
            <div className="panel-header">
              <h2>{formatScenario(result.scenario)}</h2>
              <span className={`run-status run-status-${result.status}`}>
                {result.status}
              </span>
            </div>
            <div className="eval-result-body">
              <dl className="citation-grid compact-citation">
                <div>
                  <dt>Expected</dt>
                  <dd>{result.expected_root_cause}</dd>
                </div>
                <div>
                  <dt>Actual</dt>
                  <dd>{result.actual_root_cause ?? 'no report'}</dd>
                </div>
                <div>
                  <dt>Expected evidence</dt>
                  <dd>{result.expected_evidence_types.join(', ')}</dd>
                </div>
                <div>
                  <dt>Observed evidence</dt>
                  <dd>{result.observed_evidence_types.join(', ') || 'none'}</dd>
                </div>
              </dl>

              {result.failure_reasons.length > 0 ? (
                <ul className="eval-failure-list">
                  {result.failure_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : (
                <p className="eval-pass-note">All scoring gates passed.</p>
              )}

              <details>
                <summary>Example output</summary>
                <pre>{JSON.stringify(result.example_output, null, 2)}</pre>
              </details>
            </div>
          </article>
        ))}
      </section>
    </>
  );
}

function traceLink(result: EvalResult) {
  if (result.trace_url?.startsWith('http')) {
    return <a href={result.trace_url}>{result.trace_provider ?? 'trace'}</a>;
  }
  return result.trace_provider ?? 'local';
}
