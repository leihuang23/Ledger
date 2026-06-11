export type HealthResponse = {
  status: string;
  service: string;
  version: string;
};

export type MrrMetrics = {
  current_mrr_cents: number;
  previous_mrr_cents: number;
  delta_cents: number;
  delta_percent: number;
  active_subscriptions: number;
  churned_mrr_cents: number;
};

export type ChurnMetrics = {
  churned_accounts_30d: number;
  active_accounts: number;
  churn_rate_30d: number;
  churned_mrr_cents_30d: number;
};

export type FailedInvoiceSample = {
  invoice_id: string;
  account_name: string;
  invoice_date: string;
  amount_cents: number;
  failure_reason: string | null;
  source_scenario: string | null;
};

export type FailedInvoiceMetrics = {
  failed_count_30d: number;
  failed_amount_cents_30d: number;
  unresolved_count_30d: number;
  recent_failures: FailedInvoiceSample[];
};

export type CategoryCount = {
  category: string;
  count: number;
};

export type TicketVolumeMetrics = {
  total_tickets_30d: number;
  open_tickets: number;
  high_priority_open_tickets: number;
  by_category_30d: CategoryCount[];
};

export type ActiveUserMetrics = {
  active_users_7d: number;
  active_users_30d: number;
  event_count_7d: number;
  event_count_30d: number;
};

export type DashboardMetrics = {
  as_of: string;
  mrr: MrrMetrics;
  churn: ChurnMetrics;
  failed_invoices: FailedInvoiceMetrics;
  ticket_volume: TicketVolumeMetrics;
  active_users: ActiveUserMetrics;
};

export type DashboardMetricsResult =
  | { ok: true; data: DashboardMetrics }
  | { ok: false; error: string };

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

export async function getHealth(): Promise<HealthResponse> {
  try {
    const response = await fetch(`${apiBaseUrl}/health`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      throw new Error(`Health check failed with status ${response.status}`);
    }

    return response.json() as Promise<HealthResponse>;
  } catch {
    return {
      status: 'unavailable',
      service: 'ops-agent-api',
      version: 'unknown',
    };
  }
}

export async function getDashboardMetrics(): Promise<DashboardMetricsResult> {
  try {
    const response = await fetch(`${apiBaseUrl}/metrics/dashboard`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Metrics endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as DashboardMetrics,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Metrics endpoint unavailable',
    };
  }
}
