import Link from 'next/link';
import { getSupportTickets } from '@/lib/api';
import { formatDateTime } from '@/lib/format';

export const dynamic = 'force-dynamic';

export default async function SupportTicketsPage({
  searchParams,
}: {
  searchParams: Promise<{ account_id?: string; status?: string; category?: string }>;
}) {
  const { account_id, status, category } = await searchParams;
  const result = await getSupportTickets({ account_id, status, category });

  if (!result.ok) {
    return (
      <main className="dashboard-shell">
        <section className="panel anomaly-panel">
          <div className="panel-message error-detail">
            Failed to load tickets: {result.error}
          </div>
        </section>
      </main>
    );
  }

  const { total, tickets } = result.data;

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Operational workspace</p>
          <h1>Support tickets</h1>
        </div>
      </header>

      <section className="panel table-panel">
        <div className="panel-header">
          <h2>All tickets</h2>
          <span>{total} total</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Subject</th>
                <th>Account</th>
                <th>Category</th>
                <th>Priority</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((ticket) => (
                <tr key={ticket.id}>
                  <td>
                    <Link href={`/support/tickets/${ticket.id}`}>{ticket.subject}</Link>
                  </td>
                  <td>
                    <Link href={`/accounts/${ticket.account_id}`}>{ticket.account_name}</Link>
                  </td>
                  <td>{ticket.category}</td>
                  <td>{ticket.priority}</td>
                  <td>{ticket.status}</td>
                  <td>{formatDateTime(ticket.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
