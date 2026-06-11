import type { DashboardMetrics } from '@/lib/api';
import { getDashboardMetrics, getHealth } from '@/lib/api';

const moneyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});

const numberFormatter = new Intl.NumberFormat('en-US');

function formatMoney(cents: number) {
  return moneyFormatter.format(cents / 100);
}

function formatPercent(value: number) {
  return `${value.toFixed(1)}%`;
}

function formatCount(value: number) {
  return numberFormatter.format(value);
}

function formatDateTime(value: string) {
  const hasTimezone = /(?:[zZ]|[+-]\d{2}:\d{2})$/.test(value);
  const timestamp = hasTimezone ? value : `${value}Z`;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZone: 'UTC',
  }).format(new Date(timestamp));
}

function formatScenario(value: string | null) {
  return value ? value.replaceAll('_', ' ') : 'routine failure';
}

export default async function Home() {
  const [health, dashboardResult] = await Promise.all([getHealth(), getDashboardMetrics()]);
  const apiOnline = health.status === 'ok';

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Ops Agent</p>
          <h1>SaaS revenue and support dashboard</h1>
        </div>
        <div className={`status-pill ${apiOnline ? 'status-ok' : 'status-error'}`}>
          <span aria-hidden="true" />
          {apiOnline ? 'API online' : 'API unavailable'}
        </div>
      </header>

      {dashboardResult.ok ? (
        <Dashboard data={dashboardResult.data} />
      ) : (
        <UnavailablePanel error={dashboardResult.error} />
      )}
    </main>
  );
}

function Dashboard({ data }: { data: DashboardMetrics }) {
  const categoryMax = Math.max(
    ...data.ticket_volume.by_category_30d.map((category) => category.count),
    1,
  );

  return (
    <>
      <section className="snapshot-bar">
        <div>
          <span className="label">Dataset as of</span>
          <strong>{formatDateTime(data.as_of)}</strong>
        </div>
        <div>
          <span className="label">Active subscriptions</span>
          <strong>{formatCount(data.mrr.active_subscriptions)}</strong>
        </div>
        <div>
          <span className="label">30d churned MRR</span>
          <strong>{formatMoney(data.churn.churned_mrr_cents_30d)}</strong>
        </div>
        <div>
          <span className="label">30d ticket volume</span>
          <strong>{formatCount(data.ticket_volume.total_tickets_30d)}</strong>
        </div>
      </section>

      <section className="metric-grid" aria-label="Business metrics">
        <MetricCard
          label="Current MRR"
          value={formatMoney(data.mrr.current_mrr_cents)}
          detail={`${formatMoney(Math.abs(data.mrr.delta_cents))} vs previous window`}
          tone={data.mrr.delta_cents < 0 ? 'danger' : 'good'}
        />
        <MetricCard
          label="MRR delta"
          value={formatPercent(data.mrr.delta_percent)}
          detail={`${formatMoney(data.mrr.previous_mrr_cents)} previous MRR`}
          tone={data.mrr.delta_cents < 0 ? 'danger' : 'good'}
        />
        <MetricCard
          label="Failed invoices"
          value={formatCount(data.failed_invoices.failed_count_30d)}
          detail={`${formatMoney(data.failed_invoices.failed_amount_cents_30d)} at risk`}
          tone="warning"
        />
        <MetricCard
          label="Active users"
          value={formatCount(data.active_users.active_users_7d)}
          detail={`${formatCount(data.active_users.active_users_30d)} active in 30d`}
          tone="info"
        />
        <MetricCard
          label="Open tickets"
          value={formatCount(data.ticket_volume.open_tickets)}
          detail={`${formatCount(data.ticket_volume.high_priority_open_tickets)} high priority`}
          tone="warning"
        />
        <MetricCard
          label="30d churn"
          value={formatPercent(data.churn.churn_rate_30d * 100)}
          detail={`${formatCount(data.churn.churned_accounts_30d)} accounts churned`}
          tone="danger"
        />
      </section>

      <section className="content-grid">
        <div className="panel table-panel">
          <div className="panel-header">
            <h2>Recent failed invoices</h2>
            <span>
              {formatCount(data.failed_invoices.unresolved_count_30d)} unresolved failures
            </span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Date</th>
                  <th>Amount</th>
                  <th>Signal</th>
                </tr>
              </thead>
              <tbody>
                {data.failed_invoices.recent_failures.map((invoice) => (
                  <tr key={invoice.invoice_id}>
                    <td>{invoice.account_name}</td>
                    <td>{invoice.invoice_date}</td>
                    <td>{formatMoney(invoice.amount_cents)}</td>
                    <td>{formatScenario(invoice.source_scenario)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h2>Ticket volume by category</h2>
            <span>Last 30 days</span>
          </div>
          <div className="category-stack">
            {data.ticket_volume.by_category_30d.map((category) => (
              <div className="category-row" key={category.category}>
                <div className="category-label">
                  <span>{category.category}</span>
                  <strong>{formatCount(category.count)}</strong>
                </div>
                <div className="bar-track">
                  <span
                    className="bar-fill"
                    style={{ width: `${(category.count / categoryMax) * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h2>Usage activity</h2>
            <span>Product events</span>
          </div>
          <dl className="stat-list">
            <div>
              <dt>7d events</dt>
              <dd>{formatCount(data.active_users.event_count_7d)}</dd>
            </div>
            <div>
              <dt>30d events</dt>
              <dd>{formatCount(data.active_users.event_count_30d)}</dd>
            </div>
            <div>
              <dt>7d active users</dt>
              <dd>{formatCount(data.active_users.active_users_7d)}</dd>
            </div>
            <div>
              <dt>30d active users</dt>
              <dd>{formatCount(data.active_users.active_users_30d)}</dd>
            </div>
          </dl>
        </div>
      </section>
    </>
  );
}

function MetricCard({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone: 'danger' | 'good' | 'info' | 'warning';
}) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function UnavailablePanel({ error }: { error: string }) {
  return (
    <section className="empty-state">
      <h2>Dashboard data unavailable</h2>
      <p>The metrics endpoint did not return seeded SaaS data.</p>
      <p className="error-detail">{error}</p>
    </section>
  );
}
