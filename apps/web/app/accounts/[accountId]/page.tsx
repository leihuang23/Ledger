import Link from 'next/link';
import { getAccount } from '@/lib/api';
import { formatDate, formatDateTime, formatMoney } from '@/lib/format';

export const dynamic = 'force-dynamic';

export default async function AccountDetailPage({
  params,
}: {
  params: Promise<{ accountId: string }>;
}) {
  const { accountId } = await params;
  const result = await getAccount(accountId);

  if (!result.ok) {
    return (
      <main className="dashboard-shell">
        <section className="panel anomaly-panel">
          <div className="panel-message error-detail">
            Failed to load account: {result.error}
          </div>
        </section>
      </main>
    );
  }

  const account = result.data;
  const summary = account.invoice_summary;

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Account</p>
          <h1>{account.name}</h1>
        </div>
      </header>

      <section className="snapshot-bar">
        <div>
          <span className="label">Segment</span>
          <strong>{account.segment}</strong>
        </div>
        <div>
          <span className="label">Industry</span>
          <strong>{account.industry}</strong>
        </div>
        <div>
          <span className="label">Region</span>
          <strong>{account.region}</strong>
        </div>
        <div>
          <span className="label">Health score</span>
          <strong>{account.health_score}</strong>
        </div>
        <div>
          <span className="label">Status</span>
          <strong>{account.is_active ? 'Active' : 'Inactive'}</strong>
        </div>
      </section>

      <div className="content-grid">
        <section className="panel">
          <div className="panel-header">
            <h2>Subscription</h2>
          </div>
          <div className="panel-message">
            {account.subscription ? (
              <dl className="stat-list">
                <div>
                  <dt>Plan</dt>
                  <dd>{account.subscription.plan}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd>{account.subscription.status}</dd>
                </div>
                <div>
                  <dt>MRR</dt>
                  <dd>{formatMoney(account.subscription.mrr_cents)}</dd>
                </div>
                <div>
                  <dt>Seats</dt>
                  <dd>{account.subscription.seats}</dd>
                </div>
              </dl>
            ) : (
              <p>No subscription found.</p>
            )}
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Invoice summary</h2>
          </div>
          <div className="panel-message">
            <dl className="stat-list">
              <div>
                <dt>Total</dt>
                <dd>{summary.total_invoices}</dd>
              </div>
              <div>
                <dt>Paid</dt>
                <dd>{summary.paid_invoices}</dd>
              </div>
              <div>
                <dt>Failed</dt>
                <dd>{summary.failed_invoices}</dd>
              </div>
              <div>
                <dt>Failed amount</dt>
                <dd>{formatMoney(summary.failed_amount_cents)}</dd>
              </div>
            </dl>
          </div>
        </section>

        <section className="panel table-panel">
          <div className="panel-header">
            <h2>Recent invoices</h2>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Date</th>
                  <th>Amount</th>
                  <th>Status</th>
                  <th>Failure reason</th>
                </tr>
              </thead>
              <tbody>
                {account.recent_invoices.map((invoice) => (
                  <tr key={invoice.id}>
                    <td>{invoice.id}</td>
                    <td>{formatDate(invoice.invoice_date)}</td>
                    <td>{formatMoney(invoice.amount_cents)}</td>
                    <td>{invoice.status}</td>
                    <td>{invoice.failure_reason ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Recent tickets</h2>
          </div>
          <div className="signal-stack">
            {account.recent_tickets.map((ticket) => (
              <div key={ticket.id} className="signal-row">
                <strong>
                  <Link href={`/support/tickets/${ticket.id}`}>{ticket.subject}</Link>
                </strong>
                <span>
                  {ticket.category} · {ticket.priority} · {ticket.status} ·{' '}
                  {formatDateTime(ticket.created_at)}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Product events</h2>
          </div>
          <div className="signal-stack">
            {account.product_event_summary.map((event) => (
              <div key={event.event_name} className="signal-row">
                <strong>{event.event_name}</strong>
                <span>
                  {event.event_count} events · latest {formatDateTime(event.latest_event_at)}
                </span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
