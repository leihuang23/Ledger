import Link from 'next/link';

import { runEvalDatasetFromStudio } from '@/app/actions';
import { ReadOnlyOperatorNotice } from '@/app/ReadOnlyOperatorNotice';
import { TraceLink } from '@/app/TraceLink';
import type {
  AgentSummary,
  AgentVersionSummary,
  EvalComparison,
  EvalComparisonCase,
  EvalDatasetDetail,
  EvalDatasetSummary,
  EvalResult,
} from '@/lib/api';
import {
  compareEvalResults,
  getEvalDataset,
  listAgents,
  listAgentVersions,
  listEvalDatasets,
  listEvalResults,
} from '@/lib/api';
import {
  formatCount,
  formatPercent,
  formatScenario,
  formatUsd,
} from '@/lib/format';
import { operatorMutationsEnabled } from '@/lib/operatorMutations';

export const dynamic = 'force-dynamic';

type EvalSearchParams = {
  dataset_id?: string;
  results_version_id?: string;
  version_a?: string;
  version_b?: string;
  eval_error?: string;
  eval_notice?: string;
};

type PublishedVersion = {
  agent: AgentSummary;
  version: AgentVersionSummary;
};

export default async function EvalStudioPage({
  searchParams,
}: {
  searchParams: Promise<EvalSearchParams>;
}) {
  const params = await searchParams;
  const [datasetListResult, versions] = await Promise.all([
    listEvalDatasets(),
    loadPublishedVersions(),
  ]);

  if (!datasetListResult.ok) {
    return (
      <main className="dashboard-shell">
        <StudioHeader />
        <section className="empty-state" aria-live="polite">
          <h2>Eval datasets unavailable</h2>
          <p className="error-detail">{datasetListResult.error}</p>
        </section>
      </main>
    );
  }

  const datasets = datasetListResult.data.datasets;
  const selectedDatasetId = selectKnownId(
    params.dataset_id,
    datasets.map((dataset) => dataset.id),
  );
  const versionIds = versions.map(({ version }) => version.id);
  const versionA = selectKnownId(params.version_a, versionIds) ?? versionIds[0];
  const versionB =
    selectKnownId(params.version_b, versionIds) ??
    versionIds.find((versionId) => versionId !== versionA);
  const resultsVersionId =
    selectKnownId(params.results_version_id, versionIds) ?? versionA;

  const [datasetResult, resultsResult, comparisonResult] = await Promise.all([
    selectedDatasetId ? getEvalDataset(selectedDatasetId) : null,
    selectedDatasetId && resultsVersionId
      ? listEvalResults({
          dataset_id: selectedDatasetId,
          agent_version_id: resultsVersionId,
        })
      : null,
    selectedDatasetId && versionA && versionB && versionA !== versionB
      ? compareEvalResults({
          dataset_id: selectedDatasetId,
          version_a: versionA,
          version_b: versionB,
        })
      : null,
  ]);

  return (
    <main className="dashboard-shell">
      <StudioHeader />

      {params.eval_error ? (
        <section className="panel-message eval-studio-alert error-detail" aria-live="polite">
          {params.eval_error}
        </section>
      ) : null}
      {params.eval_notice ? (
        <section className="panel-message eval-studio-alert" aria-live="polite">
          {params.eval_notice}
        </section>
      ) : null}

      {datasets.length === 0 ? (
        <section className="empty-state">
          <h2>No eval datasets</h2>
          <p>Create a dataset through the API before running a version benchmark.</p>
        </section>
      ) : (
        <>
          <section className="eval-studio-layout">
            <DatasetRail
              datasets={datasets}
              selectedDatasetId={selectedDatasetId}
              params={params}
            />
            <div className="eval-studio-main">
              {!datasetResult ? null : !datasetResult.ok ? (
                <section className="panel panel-message error-detail">
                  Failed to load dataset: {datasetResult.error}
                </section>
              ) : (
                <DatasetDetail dataset={datasetResult.data} />
              )}
              <RunControls
                datasetId={selectedDatasetId}
                versions={versions}
                resultsVersionId={resultsVersionId}
                versionA={versionA}
                versionB={versionB}
              />
            </div>
          </section>

          <ComparisonPanel
            comparisonResult={comparisonResult}
            versionA={versionA}
            versionB={versionB}
            versions={versions}
          />

          <ResultsPanel
            resultsResult={resultsResult}
            resultsVersionId={resultsVersionId}
            versions={versions}
          />
        </>
      )}
    </main>
  );
}

function StudioHeader() {
  return (
    <header className="dashboard-header">
      <div>
        <p className="eyebrow">Quality control plane</p>
        <h1>Eval Studio</h1>
        <p className="page-summary">
          Run reproducible datasets against published agent versions and inspect regressions
          before release.
        </p>
      </div>
      <Link className="action-button secondary-action" href="/dashboard">
        Observability
      </Link>
    </header>
  );
}

function DatasetRail({
  datasets,
  selectedDatasetId,
  params,
}: {
  datasets: EvalDatasetSummary[];
  selectedDatasetId: string | undefined;
  params: EvalSearchParams;
}) {
  return (
    <aside className="panel eval-dataset-rail" aria-label="Eval datasets">
      <div className="panel-header">
        <h2>Datasets</h2>
        <span>{formatCount(datasets.length)}</span>
      </div>
      <nav className="eval-dataset-list">
        {datasets.map((dataset) => {
          const query = new URLSearchParams({ dataset_id: dataset.id });
          copyQueryParam(params, query, 'results_version_id');
          copyQueryParam(params, query, 'version_a');
          copyQueryParam(params, query, 'version_b');
          const selected = dataset.id === selectedDatasetId;
          return (
            <Link
              aria-current={selected ? 'page' : undefined}
              className={selected ? 'eval-dataset-link selected' : 'eval-dataset-link'}
              href={`/evals?${query.toString()}`}
              key={dataset.id}
            >
              <strong>{dataset.name}</strong>
              <span>{formatCount(dataset.case_count)} cases</span>
              <small>{dataset.description || 'No dataset description.'}</small>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}

function DatasetDetail({ dataset }: { dataset: EvalDatasetDetail }) {
  return (
    <section className="panel eval-dataset-detail">
      <div className="panel-header">
        <div>
          <span>Selected dataset</span>
          <h2>{dataset.name}</h2>
        </div>
        <span>{formatCount(dataset.cases.length)} cases</span>
      </div>
      <p className="eval-dataset-description">{dataset.description}</p>
      <div className="eval-case-list">
        {dataset.cases.map((evalCase, index) => (
          <article className="eval-case-row" key={evalCase.id}>
            <span className="eval-case-index">{String(index + 1).padStart(2, '0')}</span>
            <div>
              <h3>{evalCase.title}</h3>
              <p>{evalCase.expected_root_cause}</p>
              <dl className="eval-case-meta">
                <div>
                  <dt>Scenario</dt>
                  <dd>{formatScenario(evalCase.scenario)}</dd>
                </div>
                <div>
                  <dt>Evidence gates</dt>
                  <dd>{evalCase.expected_evidence_types.join(', ')}</dd>
                </div>
                <div>
                  <dt>False leads</dt>
                  <dd>{evalCase.false_leads.join(', ') || 'none'}</dd>
                </div>
              </dl>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function RunControls({
  datasetId,
  versions,
  resultsVersionId,
  versionA,
  versionB,
}: {
  datasetId: string | undefined;
  versions: PublishedVersion[];
  resultsVersionId: string | undefined;
  versionA: string | undefined;
  versionB: string | undefined;
}) {
  const mutationsEnabled = operatorMutationsEnabled();

  return (
    <section className="panel eval-controls-panel">
      <div className="panel-header">
        <h2>Run and compare</h2>
        <span>Published versions only</span>
      </div>
      {versions.length === 0 ? (
        <div className="panel-message">Publish an agent version before running this dataset.</div>
      ) : (
        <div className="eval-control-grid">
          <form action={runEvalDatasetFromStudio} className="eval-control-form">
            <h3>Run dataset</h3>
            {!mutationsEnabled ? <ReadOnlyOperatorNotice /> : null}
            <input name="dataset_id" type="hidden" value={datasetId} />
            <input name="version_a" type="hidden" value={versionA} />
            <input name="version_b" type="hidden" value={versionB} />
            <label className="field-label">
              <span>Agent version</span>
              <select
                className="field-select"
                defaultValue={resultsVersionId}
                disabled={!mutationsEnabled}
                name="agent_version_id"
                required
              >
                {versions.map((item) => (
                  <option key={item.version.id} value={item.version.id}>
                    {versionLabel(item)}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="action-button"
              disabled={!mutationsEnabled || !datasetId}
              type="submit"
            >
              Run selected dataset
            </button>
          </form>

          <form action="/evals" className="eval-control-form" method="get">
            <h3>Compare versions</h3>
            <input name="dataset_id" type="hidden" value={datasetId} />
            <input name="results_version_id" type="hidden" value={resultsVersionId} />
            <label className="field-label">
              <span>Baseline · Version A</span>
              <select className="field-select" defaultValue={versionA} name="version_a" required>
                {versions.map((item) => (
                  <option key={item.version.id} value={item.version.id}>
                    {versionLabel(item)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field-label">
              <span>Candidate · Version B</span>
              <select className="field-select" defaultValue={versionB} name="version_b" required>
                {versions.map((item) => (
                  <option key={item.version.id} value={item.version.id}>
                    {versionLabel(item)}
                  </option>
                ))}
              </select>
            </label>
            <button className="action-button secondary-action" type="submit">
              Compare A vs B
            </button>
          </form>
        </div>
      )}
    </section>
  );
}

function ComparisonPanel({
  comparisonResult,
  versionA,
  versionB,
  versions,
}: {
  comparisonResult: Awaited<ReturnType<typeof compareEvalResults>> | null;
  versionA: string | undefined;
  versionB: string | undefined;
  versions: PublishedVersion[];
}) {
  const labelA = versionLabelById(versions, versionA);
  const labelB = versionLabelById(versions, versionB);

  if (!versionA || !versionB || versionA === versionB) {
    return (
      <section className="panel eval-comparison-panel">
        <div className="panel-header">
          <h2>A vs B comparison</h2>
        </div>
        <div className="panel-message">Choose two different published versions to compare.</div>
      </section>
    );
  }

  if (!comparisonResult) return null;
  if (!comparisonResult.ok) {
    return (
      <section className="panel eval-comparison-panel">
        <div className="panel-header">
          <h2>A vs B comparison</h2>
          <span>{labelA} → {labelB}</span>
        </div>
        <div className="panel-message">
          Run this dataset against both versions to generate a comparison.{' '}
          <span className="error-detail">{comparisonResult.error}</span>
        </div>
      </section>
    );
  }

  return (
    <ComparisonResult comparison={comparisonResult.data} labelA={labelA} labelB={labelB} />
  );
}

function ComparisonResult({
  comparison,
  labelA,
  labelB,
}: {
  comparison: EvalComparison;
  labelA: string;
  labelB: string;
}) {
  const hasRegressions = comparison.regressions.length > 0;
  return (
    <section className="panel eval-comparison-panel">
      <div className="panel-header">
        <h2>A vs B comparison</h2>
        <span>{formatCount(comparison.total_cases)} matched cases</span>
      </div>
      <div
        className={hasRegressions ? 'regression-banner has-regressions' : 'regression-banner'}
        role="status"
      >
        <strong>
          {hasRegressions
            ? `${formatCount(comparison.regressions.length)} regression${comparison.regressions.length === 1 ? '' : 's'} detected`
            : 'No regressions detected'}
        </strong>
        <span>
          {labelA} {formatRate(comparison.pass_rate_a)} → {labelB}{' '}
          {formatRate(comparison.pass_rate_b)} ({formatDelta(comparison.pass_rate_delta)})
        </span>
      </div>
      <div className="table-wrap">
        <table className="eval-comparison-table">
          <thead>
            <tr>
              <th>Case</th>
              <th>{labelA}</th>
              <th>{labelB}</th>
              <th>Change</th>
            </tr>
          </thead>
          <tbody>
            {comparison.cases.map((item) => (
              <ComparisonRow item={item} key={item.eval_case_id} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ComparisonRow({ item }: { item: EvalComparisonCase }) {
  return (
    <tr className={item.change === 'regression' ? 'eval-regression-row' : undefined}>
      <td>
        <strong>{formatScenario(item.scenario)}</strong>
        <small>{item.eval_case_id}</small>
      </td>
      <td>{passBadge(item.passed_a)}</td>
      <td>{passBadge(item.passed_b)}</td>
      <td>
        <span className={`eval-change eval-change-${item.change}`}>
          {item.change === 'regression' ? 'Regression' : formatScenario(item.change)}
        </span>
      </td>
    </tr>
  );
}

function ResultsPanel({
  resultsResult,
  resultsVersionId,
  versions,
}: {
  resultsResult: Awaited<ReturnType<typeof listEvalResults>> | null;
  resultsVersionId: string | undefined;
  versions: PublishedVersion[];
}) {
  const label = versionLabelById(versions, resultsVersionId);
  return (
    <section className="panel table-panel eval-results-panel">
      <div className="panel-header">
        <h2>Per-case results</h2>
        <span>{label}</span>
      </div>
      {!resultsResult ? (
        <div className="panel-message">Select a published version to inspect its results.</div>
      ) : !resultsResult.ok ? (
        <div className="panel-message error-detail">{resultsResult.error}</div>
      ) : resultsResult.data.results.length === 0 ? (
        <div className="panel-message">No persisted results for this dataset and version.</div>
      ) : (
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
                <th>Estimated cost</th>
                <th>Trace</th>
              </tr>
            </thead>
            <tbody>
              {resultsResult.data.results.map((result) => (
                <ResultRow key={result.id} result={result} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ResultRow({ result }: { result: EvalResult }) {
  return (
    <tr>
      <td>
        <strong>{formatScenario(result.scenario)}</strong>
        {result.failure_reasons.length > 0 ? (
          <small className="eval-result-failure">{result.failure_reasons.join(' · ')}</small>
        ) : (
          <small>All scoring gates passed.</small>
        )}
      </td>
      <td>{passBadge(result.passed)}</td>
      <td>{formatPercent(result.root_cause_score * 100)}</td>
      <td>{formatPercent(result.citation_quality_score * 100)}</td>
      <td>{formatPercent(result.action_safety_score * 100)}</td>
      <td>{formatCount(result.latency_ms)} ms</td>
      <td>{formatUsd(result.cost_estimate_usd)}</td>
      <td>{traceLink(result)}</td>
    </tr>
  );
}

async function loadPublishedVersions(): Promise<PublishedVersion[]> {
  const agentsResult = await listAgents({ limit: 100 });
  if (!agentsResult.ok) return [];

  const versionLists = await Promise.all(
    agentsResult.data.agents.map(async (agent) => ({
      agent,
      result: await listAgentVersions(agent.id, { limit: 100 }),
    })),
  );

  return versionLists.flatMap(({ agent, result }) =>
    result.ok
      ? result.data.versions
          .filter((version) => version.status === 'published')
          .map((version) => ({ agent, version }))
      : [],
  );
}

function selectKnownId(candidate: string | undefined, knownIds: string[]) {
  if (candidate && knownIds.includes(candidate)) return candidate;
  return knownIds[0];
}

function copyQueryParam(params: EvalSearchParams, target: URLSearchParams, key: keyof EvalSearchParams) {
  const value = params[key];
  if (value) target.set(key, value);
}

function versionLabel(item: PublishedVersion) {
  return `${item.agent.name} · ${item.version.semantic_version ?? `v${item.version.version_number ?? 'draft'}`}`;
}

function versionLabelById(versions: PublishedVersion[], versionId: string | undefined) {
  const item = versions.find(({ version }) => version.id === versionId);
  return item ? versionLabel(item) : 'Version unavailable';
}

function passBadge(passed: boolean) {
  return (
    <span className={passed ? 'run-status run-status-succeeded' : 'run-status run-status-failed'}>
      {passed ? 'Passed' : 'Failed'}
    </span>
  );
}

function formatRate(value: number) {
  return formatPercent(value * 100);
}

function formatDelta(value: number) {
  const prefix = value > 0 ? '+' : '';
  return `${prefix}${formatPercent(value * 100)}`;
}

function traceLink(result: EvalResult) {
  return (
    <TraceLink
      traceUrl={result.trace_url}
      traceId={result.trace_id}
      externalLabel={result.trace_provider ?? 'trace'}
      fallback={result.trace_id ?? result.trace_provider ?? 'local'}
    />
  );
}
