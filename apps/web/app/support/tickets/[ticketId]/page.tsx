import Link from 'next/link';
import { getSupportTicket } from '@/lib/api';
import { formatDateTime } from '@/lib/format';

export const dynamic = 'force-dynamic';

export default async function SupportTicketDetailPage({
  params,
}: {
  params: Promise<{ ticketId: string }>;
}) {
  const { ticketId } = await params;
  const result = await getSupportTicket(ticketId);

  if (!result.ok) {
    return (
      <main className="dashboard-shell">
        <section className="panel anomaly-panel">
          <div className="panel-message error-detail">
            Failed to load ticket: {result.error}
          </div>
        </section>
      </main>
    );
  }

  const ticket = result.data;

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Support ticket</p>
          <h1>{ticket.subject}</h1>
        </div>
      </header>

      <section className="panel">
        <div className="panel-header">
          <h2>Details</h2>
        </div>
        <div className="report-body">
          <p>{ticket.description}</p>
          <dl className="anomaly-facts" style={{ marginTop: '16px' }}>
            <div>
              <dt>Account</dt>
              <dd>
                <Link href={`/accounts/${ticket.account_id}`}>{ticket.account_name}</Link>
              </dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{ticket.status}</dd>
            </div>
            <div>
              <dt>Priority</dt>
              <dd>{ticket.priority}</dd>
            </div>
            <div>
              <dt>Category</dt>
              <dd>{ticket.category}</dd>
            </div>
            <div>
              <dt>Sentiment</dt>
              <dd>{ticket.sentiment}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{formatDateTime(ticket.created_at)}</dd>
            </div>
            {ticket.resolved_at ? (
              <div>
                <dt>Resolved</dt>
                <dd>{formatDateTime(ticket.resolved_at)}</dd>
              </div>
            ) : null}
          </dl>
        </div>
      </section>
    </main>
  );
}
