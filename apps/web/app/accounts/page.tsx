import Link from 'next/link';
import { listAccounts } from '@/lib/api';
import { formatScenario } from '@/lib/format';

export const dynamic = 'force-dynamic';

export default async function AccountsPage() {
  const result = await listAccounts();

  if (!result.ok) {
    return (
      <main className="dashboard-shell">
        <section className="panel anomaly-panel">
          <div className="panel-message error-detail">
            Failed to load accounts: {result.error}
          </div>
        </section>
      </main>
    );
  }

  const { total, accounts } = result.data;

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Operational workspace</p>
          <h1>Accounts</h1>
        </div>
      </header>

      <section className="panel table-panel">
        <div className="panel-header">
          <h2>All accounts</h2>
          <span>{total} total</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Segment</th>
                <th>Industry</th>
                <th>Region</th>
                <th>Health</th>
                <th>Status</th>
                <th>Scenario</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.id}>
                  <td>
                    <Link href={`/accounts/${account.id}`}>{account.name}</Link>
                  </td>
                  <td>{account.segment}</td>
                  <td>{account.industry}</td>
                  <td>{account.region}</td>
                  <td>{account.health_score}</td>
                  <td>{account.is_active ? 'Active' : 'Inactive'}</td>
                  <td>{formatScenario(account.source_scenario)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
