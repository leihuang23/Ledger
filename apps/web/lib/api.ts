import { resolveApiBaseUrl } from './resolveApiBaseUrl';

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

export type MetricEvidence = {
  metric_name: string;
  current_window_start: string;
  current_window_end: string;
  previous_window_start: string;
  previous_window_end: string;
  current_value_cents: number;
  previous_value_cents: number;
  delta_cents: number;
  delta_percent: number;
  failed_invoice_cents: number;
  failed_invoice_count: number;
  invoice_ids: string[];
};

export type AffectedAccount = {
  account_id: string;
  account_name: string;
  segment: string;
  health_score: number;
  failed_invoice_cents: number;
  failed_invoice_count: number;
  failed_invoice_ids: string[];
  source_scenario: string | null;
};

export type AccountSubscription = {
  id: string;
  plan: string;
  status: string;
  mrr_cents: number;
  seats: number;
  started_at: string;
  canceled_at: string | null;
  cancellation_reason: string | null;
};

export type AccountInvoice = {
  id: string;
  invoice_date: string;
  due_date: string;
  amount_cents: number;
  status: string;
  failure_reason: string | null;
  paid_at: string | null;
  source_scenario: string | null;
};

export type AccountInvoiceSummary = {
  total_invoices: number;
  paid_invoices: number;
  failed_invoices: number;
  void_invoices: number;
  failed_amount_cents: number;
};

export type AccountTicket = {
  id: string;
  created_at: string;
  status: string;
  priority: string;
  category: string;
  subject: string;
  sentiment: string;
  source_scenario: string | null;
};

export type AccountProductEventSummary = {
  event_name: string;
  event_count: number;
  latest_event_at: string;
  source_scenario: string | null;
};

export type SupportTicket = {
  id: string;
  account_id: string;
  account_name: string;
  user_id: string | null;
  created_at: string;
  resolved_at: string | null;
  status: string;
  priority: string;
  category: string;
  subject: string;
  description: string;
  sentiment: string;
  source_scenario: string | null;
};

export type SupportTicketList = {
  total: number;
  tickets: SupportTicket[];
};

export type AccountDetail = {
  id: string;
  name: string;
  segment: string;
  industry: string;
  region: string;
  health_score: number;
  source_scenario: string | null;
  is_active: boolean;
  subscription: AccountSubscription | null;
  users: {
    id: string;
    email: string;
    full_name: string;
    role: string;
    last_seen_at: string | null;
    is_active: boolean;
  }[];
  invoice_summary: AccountInvoiceSummary;
  recent_invoices: AccountInvoice[];
  recent_tickets: AccountTicket[];
  product_event_summary: AccountProductEventSummary[];
};

export type AccountListItem = {
  id: string;
  name: string;
  segment: string;
  industry: string;
  region: string;
  health_score: number;
  source_scenario: string | null;
  is_active: boolean;
};

export type AccountList = {
  total: number;
  accounts: AccountListItem[];
};

export type SupportSignal = {
  ticket_id: string;
  account_id: string;
  account_name: string;
  created_at: string;
  status: string;
  priority: string;
  category: string;
  subject: string;
  sentiment: string;
  source_scenario: string | null;
};

export type ProductSignal = {
  event_name: string;
  event_count: number;
  affected_accounts: number;
  latest_event_at: string;
  source_scenario: string | null;
};

export type RevenueAnomaly = {
  id: string;
  title: string;
  anomaly_type: string;
  severity: string;
  detected_at: string;
  summary: string;
  metric_evidence: MetricEvidence;
  affected_accounts: AffectedAccount[];
  support_signals: SupportSignal[];
  product_signals: ProductSignal[];
  incident_id: string | null;
};

export type IncidentSummary = {
  id: string;
  title: string;
  status: string;
  severity: string;
  anomaly_type: string;
  detected_at: string;
  summary: string;
  affected_account_count: number;
};

export type IncidentDetail = {
  id: string;
  title: string;
  status: string;
  severity: string;
  anomaly_type: string;
  detected_at: string;
  summary: string;
  source_scenario: string | null;
  metric_evidence: MetricEvidence;
  affected_accounts: AffectedAccount[];
  support_signals: SupportSignal[];
  product_signals: ProductSignal[];
  evidence: {
    anomaly_id?: string;
    source_queries?: string[];
    [key: string]: unknown;
  };
};

export type DashboardMetricsResult =
  | { ok: true; data: DashboardMetrics }
  | { ok: false; error: string };

export type RevenueAnomaliesResult =
  | { ok: true; data: RevenueAnomaly[] }
  | { ok: false; error: string };

export type IncidentDetailResult =
  | { ok: true; data: IncidentDetail }
  | { ok: false; error: string };

export type AccountDetailResult =
  | { ok: true; data: AccountDetail }
  | { ok: false; error: string };

export type AccountListResult =
  | { ok: true; data: AccountList }
  | { ok: false; error: string };

export type IncidentListResult =
  | { ok: true; data: IncidentSummary[] }
  | { ok: false; error: string };

export type CreateIncidentFromAnomalyResult =
  | { ok: true; data: IncidentDetail }
  | { ok: false; error: string };

export type KnowledgeCitation = {
  source_id: string;
  chunk_id: string;
  title: string;
  document_type: string;
  heading_path: string;
  source_path: string;
  source_uri: string | null;
  chunk_index: number;
  tags: string[];
};

export type KnowledgeSearchItem = {
  source_id: string;
  title: string;
  snippet: string;
  score: number;
  citation: KnowledgeCitation;
};

export type KnowledgeSearchResponse = {
  query: string;
  results: KnowledgeSearchItem[];
};

export type KnowledgeSearchResult =
  | { ok: true; data: KnowledgeSearchResponse }
  | { ok: false; error: string };

export type ReportAffectedAccount = {
  account_id: string;
  account_name: string;
  segment: string;
  health_score: number;
  failed_invoice_cents: number;
  failed_invoice_ids: string[];
  ticket_ids: string[];
};

export type ReportEvidence = {
  kind: 'sql' | 'document' | 'ticket';
  title: string;
  summary: string;
  reference_id: string;
  source_query: string | null;
  citation: Record<string, unknown>;
};

export type ReportClaim = {
  category: 'root_cause' | 'impact' | 'recommendation' | 'uncertainty';
  text: string;
  citation_refs: string[];
};

export type InvestigationReport = {
  root_cause: string;
  summary: string;
  affected_accounts: ReportAffectedAccount[];
  cited_evidence: ReportEvidence[];
  claims: ReportClaim[];
  confidence: 'low' | 'medium' | 'high';
  next_actions: string[];
  generated_at: string;
};

export type ActionType =
  | 'draft_slack_message'
  | 'draft_customer_email'
  | 'create_task'
  | 'update_account_note';

export type RiskLevel = 'low' | 'high';

export type MockActionStatus = 'pending_approval' | 'executed' | 'rejected';

export type ApprovalStatus = 'pending' | 'approved' | 'rejected';

export type AuditEventType = 'proposed' | 'approved' | 'rejected' | 'executed';

export type ActionAuditEvent = {
  id: string;
  run_id: string;
  action_id: string;
  approval_request_id: string | null;
  event_type: AuditEventType;
  actor: string;
  notes: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type ApprovalRequestSummary = {
  id: string;
  run_id: string;
  action_id: string;
  status: ApprovalStatus;
  risk_level: RiskLevel;
  reason: string;
  requested_by: string;
  decided_by: string | null;
  decision_notes: string | null;
  created_at: string;
  decided_at: string | null;
};

export type MockAction = {
  id: string;
  run_id: string;
  action_type: ActionType;
  risk_level: RiskLevel;
  status: MockActionStatus;
  title: string;
  description: string;
  target: string;
  payload: Record<string, unknown>;
  created_by: string;
  created_at: string;
  updated_at: string;
  executed_at: string | null;
  approval_request: ApprovalRequestSummary | null;
  audit_events: ActionAuditEvent[];
};

export type ApprovalRequest = ApprovalRequestSummary & {
  action: MockAction;
};

export type AgentRunStep = {
  id: string;
  run_id: string;
  sequence: number;
  stage: string;
  tool_name: string | null;
  status: 'running' | 'succeeded' | 'failed';
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown> | null;
  error: string | null;
  started_at: string;
  completed_at: string | null;
};

export type AgentRunSummary = {
  id: string;
  incident_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  trace_id: string | null;
  trace_url: string | null;
  trace_provider: 'langfuse' | 'langsmith' | 'local' | null;
  token_estimate: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_estimate_usd: number;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentRunDetail = {
  id: string;
  incident_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  is_stale: boolean;
  trace_id: string | null;
  trace_url: string | null;
  trace_provider: 'langfuse' | 'langsmith' | 'local' | null;
  trace_metadata: Record<string, unknown>;
  token_estimate: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_estimate_usd: number;
  input_payload: Record<string, unknown>;
  final_report: InvestigationReport | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  steps: AgentRunStep[];
  mock_actions: MockAction[];
};

export type StartInvestigationResult =
  | { ok: true; data: AgentRunDetail }
  | { ok: false; error: string };

export type AgentRunDetailResult =
  | { ok: true; data: AgentRunDetail }
  | { ok: false; error: string };

export type AgentRunListResult =
  | { ok: true; data: AgentRunSummary[] }
  | { ok: false; error: string };

export type EvalStatus = 'passed' | 'failed';

export type EvalResult = {
  id: string;
  eval_run_id: string;
  eval_case_id: string;
  agent_run_id: string;
  scenario: string;
  status: EvalStatus;
  passed: boolean;
  root_cause_score: number;
  citation_quality_score: number;
  action_safety_score: number;
  latency_ms: number;
  expected_root_cause: string;
  actual_root_cause: string | null;
  expected_evidence_types: string[];
  observed_evidence_types: string[];
  failure_reasons: string[];
  example_output: Record<string, unknown>;
  trace_id: string | null;
  trace_url: string | null;
  trace_provider: 'langfuse' | 'langsmith' | 'local' | null;
  started_at: string;
  completed_at: string;
  created_at: string;
};

export type EvalRunSummary = {
  eval_run_id: string;
  status: EvalStatus;
  total_scenarios: number;
  passed_scenarios: number;
  failed_scenarios: number;
  started_at: string;
  completed_at: string;
  results: EvalResult[];
};

export type EvalResultsReport = {
  latest_eval_run_id: string | null;
  total_scenarios: number;
  passed_scenarios: number;
  failed_scenarios: number;
  results: EvalResult[];
};

export type EvalRunResult =
  | { ok: true; data: EvalRunSummary }
  | { ok: false; error: string };

export type EvalResultsReportResult =
  | { ok: true; data: EvalResultsReport }
  | { ok: false; error: string };

export type ApprovalDecisionResult =
  | { ok: true; data: ApprovalRequest }
  | { ok: false; error: string };

type DemoOperatorOptions = {
  demoOperatorToken?: string;
};

async function readErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string' && body.detail.length > 0) {
      return body.detail;
    }
  } catch {}

  return fallback;
}

function demoOperatorHeaders(token: string | undefined): Record<string, string> {
  if (!token) {
    return {};
  }
  return {
    'X-Demo-Operator-Token': token,
  };
}

export async function getHealth(): Promise<HealthResponse> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/health`, {
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
    const response = await fetch(`${resolveApiBaseUrl()}/metrics/dashboard`, {
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

export async function getRevenueAnomalies(): Promise<RevenueAnomaliesResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/metrics/anomalies`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Anomalies endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as RevenueAnomaly[],
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Anomalies endpoint unavailable',
    };
  }
}

export async function createIncidentFromAnomaly(
  anomalyId: string,
  options: DemoOperatorOptions = {},
): Promise<CreateIncidentFromAnomalyResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/incidents`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...demoOperatorHeaders(options.demoOperatorToken),
      },
      body: JSON.stringify({ anomaly_id: anomalyId }),
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Incident creation returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as IncidentDetail,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Incident creation unavailable',
    };
  }
}

export async function listIncidents(): Promise<IncidentListResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/incidents`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Incidents endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as IncidentSummary[],
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Incidents endpoint unavailable',
    };
  }
}

export async function getIncident(incidentId: string): Promise<IncidentDetailResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/incidents/${incidentId}`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Incident endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as IncidentDetail,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Incident endpoint unavailable',
    };
  }
}

export async function getAccount(accountId: string): Promise<AccountDetailResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/accounts/${encodeURIComponent(accountId)}`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Account endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AccountDetail,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Account endpoint unavailable',
    };
  }
}

export async function listAccounts(): Promise<AccountListResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/accounts`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Accounts endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AccountList,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Accounts endpoint unavailable',
    };
  }
}

export async function startInvestigation(
  incidentId: string,
  options: { runInline?: boolean } & DemoOperatorOptions = {},
): Promise<StartInvestigationResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/agent/investigations`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...demoOperatorHeaders(options.demoOperatorToken),
      },
      body: JSON.stringify({
        incident_id: incidentId,
        run_inline: options.runInline ?? false,
      }),
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Investigation start returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentRunDetail,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Investigation start unavailable',
    };
  }
}

export async function listAgentRuns(): Promise<AgentRunListResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/agent/runs`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Agent runs endpoint returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentRunSummary[],
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Agent runs endpoint unavailable',
    };
  }
}

export async function getAgentRun(runId: string): Promise<AgentRunDetailResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/agent/runs/${runId}`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Agent run endpoint returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentRunDetail,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Agent run endpoint unavailable',
    };
  }
}

export async function runEvalSuite(): Promise<EvalRunResult> {
  try {
    const headers: HeadersInit = {};
    if (process.env.EVAL_RUN_TOKEN) {
      headers['X-Eval-Run-Token'] = process.env.EVAL_RUN_TOKEN;
    }

    const response = await fetch(`${resolveApiBaseUrl()}/evals/run`, {
      method: 'POST',
      headers,
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Eval suite returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as EvalRunSummary,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Eval suite unavailable',
    };
  }
}

export async function getEvalResults(): Promise<EvalResultsReportResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/evals/results`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Eval results endpoint returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as EvalResultsReport,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Eval results unavailable',
    };
  }
}

export type ApprovalRequestListResult =
  | { ok: true; data: ApprovalRequest[] }
  | { ok: false; error: string };

export type SupportTicketListResult =
  | { ok: true; data: SupportTicketList }
  | { ok: false; error: string };

export type SupportTicketDetailResult =
  | { ok: true; data: SupportTicket }
  | { ok: false; error: string };

export async function listApprovalRequests(
  status?: ApprovalStatus,
): Promise<ApprovalRequestListResult> {
  try {
    const params = status ? `?status=${encodeURIComponent(status)}` : '';
    const response = await fetch(`${resolveApiBaseUrl()}/approvals${params}`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Approvals endpoint returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as ApprovalRequest[],
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Approvals endpoint unavailable',
    };
  }
}

export async function getSupportTickets(options: {
  account_id?: string;
  status?: string;
  category?: string;
} = {}): Promise<SupportTicketListResult> {
  try {
    const searchParams = new URLSearchParams();
    if (options.account_id) searchParams.set('account_id', options.account_id);
    if (options.status) searchParams.set('status', options.status);
    if (options.category) searchParams.set('category', options.category);
    const query = searchParams.toString();
    const response = await fetch(`${resolveApiBaseUrl()}/support/tickets${query ? `?${query}` : ''}`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Support tickets endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as SupportTicketList,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Support tickets endpoint unavailable',
    };
  }
}

export async function getSupportTicket(ticketId: string): Promise<SupportTicketDetailResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/support/tickets/${encodeURIComponent(ticketId)}`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Support ticket endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as SupportTicket,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Support ticket endpoint unavailable',
    };
  }
}

export async function approveApprovalRequest(
  approvalId: string,
  notes?: string,
  options: DemoOperatorOptions = {},
): Promise<ApprovalDecisionResult> {
  return submitApprovalDecision(approvalId, 'approve', notes, options);
}

export async function rejectApprovalRequest(
  approvalId: string,
  notes?: string,
  options: DemoOperatorOptions = {},
): Promise<ApprovalDecisionResult> {
  return submitApprovalDecision(approvalId, 'reject', notes, options);
}

async function submitApprovalDecision(
  approvalId: string,
  decision: 'approve' | 'reject',
  notes?: string,
  options: DemoOperatorOptions = {},
): Promise<ApprovalDecisionResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/approvals/${approvalId}/${decision}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...demoOperatorHeaders(options.demoOperatorToken),
      },
      body: JSON.stringify({ notes }),
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Approval ${decision} returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as ApprovalRequest,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : `Approval ${decision} unavailable`,
    };
  }
}

export async function searchKnowledge(query: string): Promise<KnowledgeSearchResult> {
  const trimmedQuery = query.trim();
  if (!trimmedQuery) {
    return {
      ok: true,
      data: {
        query: '',
        results: [],
      },
    };
  }

  try {
    const response = await fetch(`${resolveApiBaseUrl()}/documents/search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ query: trimmedQuery, limit: 8 }),
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Knowledge search returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as KnowledgeSearchResponse,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Knowledge search unavailable',
    };
  }
}
