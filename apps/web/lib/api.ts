import { resolveApiBaseUrl } from './resolveApiBaseUrl.ts';

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

export type IncidentList = {
  total: number;
  incidents: IncidentSummary[];
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
  | { ok: true; data: IncidentList }
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
  kind: 'sql' | 'document' | 'ticket' | 'tool';
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
  agent_version_id: string;
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

export type ModelUsage = {
  id: string;
  run_id: string;
  step_id: string | null;
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_estimate_usd: number;
  latency_ms: number;
  used_llm: boolean;
  fallback_reason: string | null;
  recorded_at: string;
};

export type AgentRunStep = {
  id: string;
  run_id: string;
  sequence: number;
  stage: string;
  tool_name: string | null;
  status: 'running' | 'succeeded' | 'failed' | 'blocked';
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown> | null;
  error: string | null;
  blocked_reason: string | null;
  started_at: string;
  completed_at: string | null;
  // Wall-clock step duration in ms (null while running). Derived server-side
  // from completed_at - started_at (PRD FR-18).
  duration_ms: number | null;
  // LLM usage rows linked to this step (PRD FR-20). Empty for non-LLM steps.
  model_usage: ModelUsage[];
};

export type AgentRunSummary = {
  id: string;
  incident_id: string | null;
  agent_id: string;
  agent_version_id: string;
  status: 'queued' | 'running' | 'waiting_for_approval' | 'succeeded' | 'failed';
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
  incident_id: string | null;
  agent_id: string;
  agent_version_id: string;
  agent: {
    id: string;
    name: string;
    description: string;
  } | null;
  agent_version: {
    id: string;
    agent_id: string;
    version_number: number | null;
    semantic_version: string | null;
    status: AgentVersionStatus;
    model: string;
  } | null;
  status: 'queued' | 'running' | 'waiting_for_approval' | 'succeeded' | 'failed';
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

export type AgentVersionObservability = {
  agent_id: string;
  agent_version_id: string;
  agent_name: string;
  semantic_version: string | null;
  model: string;
  total_runs: number;
  successful_runs: number;
  success_rate: number;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  avg_cost_estimate_usd: number;
  total_cost_estimate_usd: number;
  last_run_at: string | null;
};

export type DashboardListResult =
  | { ok: true; data: AgentVersionObservability[] }
  | { ok: false; error: string };

export type EvalStatus = 'passed' | 'failed' | 'running';

export type EvalResult = {
  id: string;
  eval_run_id: string;
  eval_case_id: string;
  agent_run_id: string;
  agent_version_id: string | null;
  dataset_id: string | null;
  scenario: string;
  status: EvalStatus;
  passed: boolean;
  root_cause_score: number;
  citation_quality_score: number;
  action_safety_score: number;
  latency_ms: number;
  cost_estimate_usd: number;
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

export type EvalDatasetCase = {
  id: string;
  scenario: string;
  incident_id: string;
  title: string;
  expected_root_cause: string;
  expected_evidence_types: string[];
  expected_evidence: string[];
  false_leads: string[];
  recommended_actions: string[];
};

export type EvalDatasetSummary = {
  id: string;
  name: string;
  description: string;
  case_count: number;
  created_at: string;
  updated_at: string;
};

export type EvalDatasetDetail = Omit<EvalDatasetSummary, 'case_count'> & {
  cases: EvalDatasetCase[];
};

export type EvalComparisonOutcome = 'unchanged' | 'regression' | 'improvement';

export type EvalComparisonCase = {
  eval_case_id: string;
  scenario: string;
  result_a_id: string;
  result_b_id: string;
  passed_a: boolean;
  passed_b: boolean;
  change: EvalComparisonOutcome;
};

export type EvalComparison = {
  dataset_id: string;
  version_a: string;
  version_b: string;
  run_a_id: string;
  run_b_id: string;
  pass_rate_a: number;
  pass_rate_b: number;
  pass_rate_delta: number;
  total_cases: number;
  regressions: EvalComparisonCase[];
  improvements: EvalComparisonCase[];
  cases: EvalComparisonCase[];
};

export type EvalDatasetRunAccepted = {
  eval_run_id: string;
  dataset_id: string;
  agent_version_id: string;
  status: 'queued';
};

export type EvalRunSummary = {
  eval_run_id: string;
  status: EvalStatus;
  total_scenarios: number;
  passed_scenarios: number;
  failed_scenarios: number;
  started_at: string;
  completed_at: string | null;
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

export type EvalDatasetListResult =
  | { ok: true; data: { datasets: EvalDatasetSummary[]; total: number } }
  | { ok: false; error: string };

export type EvalDatasetDetailResult =
  | { ok: true; data: EvalDatasetDetail }
  | { ok: false; error: string };

export type EvalResultListResult =
  | { ok: true; data: { results: EvalResult[]; total: number } }
  | { ok: false; error: string };

export type EvalDatasetRunResult =
  | { ok: true; data: EvalDatasetRunAccepted }
  | { ok: false; error: string };

export type EvalComparisonResult =
  | { ok: true; data: EvalComparison }
  | { ok: false; error: string };

export type EvalResultsReportResult =
  | { ok: true; data: EvalResultsReport }
  | { ok: false; error: string };

export type ApprovalDecisionResult =
  | { ok: true; data: ApprovalRequest }
  | { ok: false; error: string };

export type AgentVersionStatus = 'draft' | 'published';

export type AgentVersionSummary = {
  id: string;
  version_number: number | null;
  semantic_version: string | null;
  status: AgentVersionStatus;
  model: string;
  created_at: string;
  published_at: string | null;
  forked_from_version_id: string | null;
};

export type AgentVersionDetail = AgentVersionSummary & {
  system_prompt: string;
  temperature: number;
  max_tokens: number;
  enabled_tool_ids: string[];
  allowed_scopes: string[];
  published_by: string | null;
};

export type AgentSummary = {
  id: string;
  name: string;
  description: string;
  default_model: string;
  created_at: string;
  updated_at: string;
  latest_published_version: AgentVersionSummary | null;
  current_draft_version: AgentVersionSummary | null;
  version_count: number;
};

export type AgentDetail = AgentSummary & {
  versions: AgentVersionSummary[];
};

export type AgentList = {
  total: number;
  agents: AgentSummary[];
};

export type AgentVersionList = {
  total: number;
  versions: AgentVersionSummary[];
};

export type PublishResult = {
  version: AgentVersionDetail;
};

export type AgentCreateInput = {
  id: string;
  name: string;
  description?: string;
  default_model?: string;
  system_prompt?: string;
};

export type AgentVersionCreateInput = {
  fork_from_version_id?: string;
  system_prompt?: string;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  enabled_tool_ids?: string[];
  allowed_scopes?: string[];
};

export type AgentVersionUpdateInput = {
  system_prompt?: string;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  enabled_tool_ids?: string[];
  allowed_scopes?: string[];
};

export type AgentListResult =
  | { ok: true; data: AgentList }
  | { ok: false; error: string };

export type AgentDetailResult =
  | { ok: true; data: AgentDetail }
  | { ok: false; error: string };

export type AgentVersionListResult =
  | { ok: true; data: AgentVersionList }
  | { ok: false; error: string };

export type AgentVersionDetailResult =
  | { ok: true; data: AgentVersionDetail }
  | { ok: false; error: string };

export type ToolPermissionScope =
  | 'read_data'
  | 'write_mock_action'
  | 'request_approval'
  | 'run_eval';

export type RegisteredTool = {
  id: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  permission_scope: ToolPermissionScope;
  implementation_ref: string;
};

export type ToolList = {
  total: number;
  tools: RegisteredTool[];
};

export type ToolListResult =
  | { ok: true; data: ToolList }
  | { ok: false; error: string };

export type PublishVersionResult =
  | { ok: true; data: PublishResult }
  | { ok: false; error: string };

type DemoOperatorOptions = {
  demoOperatorToken?: string;
};

type EvalRunOptions = DemoOperatorOptions & {
  evalRunToken?: string;
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

export async function listIncidents(
  options: { limit?: number; offset?: number } = {},
): Promise<IncidentListResult> {
  try {
    const params = new URLSearchParams();
    if (options.limit !== undefined) {
      params.set('limit', String(options.limit));
    }
    if (options.offset !== undefined) {
      params.set('offset', String(options.offset));
    }
    const query = params.toString();
    const url = `${resolveApiBaseUrl()}/incidents${query ? `?${query}` : ''}`;
    const response = await fetch(url, {
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
      data: (await response.json()) as IncidentList,
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
  options: { runInline?: boolean; agentVersionId?: string } & DemoOperatorOptions = {},
): Promise<StartInvestigationResult> {
  try {
    const body: Record<string, unknown> = {
      incident_id: incidentId,
      run_inline: options.runInline ?? false,
    };
    if (options.agentVersionId) {
      body.agent_version_id = options.agentVersionId;
    }
    const response = await fetch(`${resolveApiBaseUrl()}/agent/investigations`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...demoOperatorHeaders(options.demoOperatorToken),
      },
      body: JSON.stringify(body),
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

export async function listRuns(
  options: { agent_version_id?: string; status?: string } = {},
): Promise<AgentRunListResult> {
  try {
    const params = new URLSearchParams();
    if (options.agent_version_id) {
      params.set('agent_version_id', options.agent_version_id);
    }
    if (options.status) {
      params.set('status', options.status);
    }
    const query = params.toString();
    const url = `${resolveApiBaseUrl()}/runs${query ? `?${query}` : ''}`;
    const response = await fetch(url, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Runs endpoint returned HTTP ${response.status}`,
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
      error: error instanceof Error ? error.message : 'Runs endpoint unavailable',
    };
  }
}

export async function getDashboardAgents(): Promise<DashboardListResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/dashboard/agents`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Dashboard endpoint returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentVersionObservability[],
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Dashboard endpoint unavailable',
    };
  }
}

export async function getDashboardAgent(agentId: string): Promise<DashboardListResult> {
  try {
    const response = await fetch(
      `${resolveApiBaseUrl()}/dashboard/agents/${encodeURIComponent(agentId)}`,
      {
        cache: 'no-store',
      },
    );

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Dashboard endpoint returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentVersionObservability[],
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Dashboard endpoint unavailable',
    };
  }
}

export async function getRun(runId: string): Promise<AgentRunDetailResult> {
  try {
    const response = await fetch(
      `${resolveApiBaseUrl()}/runs/${encodeURIComponent(runId)}`,
      {
        cache: 'no-store',
      },
    );

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Run endpoint returned HTTP ${response.status}`,
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
      error: error instanceof Error ? error.message : 'Run endpoint unavailable',
    };
  }
}

export async function launchRun(
  body: {
    agent_version_id: string;
    input_payload?: Record<string, unknown>;
    incident_id?: string | null;
    run_inline?: boolean;
  },
  options: DemoOperatorOptions = {},
): Promise<StartInvestigationResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/runs`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...demoOperatorHeaders(options.demoOperatorToken),
      },
      body: JSON.stringify({
        agent_version_id: body.agent_version_id,
        input_payload: body.input_payload ?? {},
        incident_id: body.incident_id ?? null,
        run_inline: body.run_inline ?? false,
      }),
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Run launch returned HTTP ${response.status}`,
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
      error: error instanceof Error ? error.message : 'Run launch unavailable',
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

export async function listEvalDatasets(): Promise<EvalDatasetListResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/eval-datasets`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Eval datasets endpoint returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as { datasets: EvalDatasetSummary[]; total: number },
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Eval datasets unavailable',
    };
  }
}

export async function getEvalDataset(
  datasetId: string,
): Promise<EvalDatasetDetailResult> {
  try {
    const response = await fetch(
      `${resolveApiBaseUrl()}/eval-datasets/${encodeURIComponent(datasetId)}`,
      { cache: 'no-store' },
    );

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Eval dataset endpoint returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as EvalDatasetDetail,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Eval dataset unavailable',
    };
  }
}

export async function runEvalDataset(
  datasetId: string,
  agentVersionId: string,
  options: EvalRunOptions = {},
): Promise<EvalDatasetRunResult> {
  try {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...demoOperatorHeaders(options.demoOperatorToken),
    };
    if (options.evalRunToken) {
      headers['X-Eval-Run-Token'] = options.evalRunToken;
    }

    const response = await fetch(
      `${resolveApiBaseUrl()}/eval-datasets/${encodeURIComponent(datasetId)}/run`,
      {
        method: 'POST',
        headers,
        body: JSON.stringify({ agent_version_id: agentVersionId }),
        cache: 'no-store',
      },
    );

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Eval dataset run returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as EvalDatasetRunAccepted,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Eval dataset run unavailable',
    };
  }
}

export async function listEvalResults(
  options: { agent_version_id?: string; dataset_id?: string } = {},
): Promise<EvalResultListResult> {
  try {
    const params = new URLSearchParams();
    if (options.agent_version_id) {
      params.set('agent_version_id', options.agent_version_id);
    }
    if (options.dataset_id) {
      params.set('dataset_id', options.dataset_id);
    }
    const query = params.toString();
    const response = await fetch(
      `${resolveApiBaseUrl()}/eval-results${query ? `?${query}` : ''}`,
      { cache: 'no-store' },
    );

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
      data: (await response.json()) as { results: EvalResult[]; total: number },
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Eval results unavailable',
    };
  }
}

export async function compareEvalResults(options: {
  version_a: string;
  version_b: string;
  dataset_id?: string;
}): Promise<EvalComparisonResult> {
  try {
    const params = new URLSearchParams({
      version_a: options.version_a,
      version_b: options.version_b,
    });
    if (options.dataset_id) {
      params.set('dataset_id', options.dataset_id);
    }
    const response = await fetch(
      `${resolveApiBaseUrl()}/eval-results/compare?${params.toString()}`,
      { cache: 'no-store' },
    );

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Eval comparison endpoint returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as EvalComparison,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Eval comparison unavailable',
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
  options: {
    status?: ApprovalStatus;
    agent_version_id?: string;
    risk_level?: RiskLevel;
  } = {},
): Promise<ApprovalRequestListResult> {
  try {
    const params = new URLSearchParams();
    if (options.status) {
      params.set('status', options.status);
    }
    if (options.agent_version_id) {
      params.set('agent_version_id', options.agent_version_id);
    }
    if (options.risk_level) {
      params.set('risk_level', options.risk_level);
    }
    const query = params.toString();
    const response = await fetch(`${resolveApiBaseUrl()}/approvals${query ? `?${query}` : ''}`, {
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

export async function listAgents(
  options: { limit?: number; offset?: number } = {},
): Promise<AgentListResult> {
  try {
    const params = new URLSearchParams();
    if (options.limit !== undefined) {
      params.set('limit', String(options.limit));
    }
    if (options.offset !== undefined) {
      params.set('offset', String(options.offset));
    }
    const query = params.toString();
    const url = `${resolveApiBaseUrl()}/agents${query ? `?${query}` : ''}`;
    const response = await fetch(url, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Agents endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentList,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Agents endpoint unavailable',
    };
  }
}

export async function listTools(): Promise<ToolListResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/tools`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Tools endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as ToolList,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Tool registry unavailable',
    };
  }
}

export async function getAgent(agentId: string): Promise<AgentDetailResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/agents/${encodeURIComponent(agentId)}`, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Agent endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentDetail,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Agent endpoint unavailable',
    };
  }
}

export async function listAgentVersions(
  agentId: string,
  options: { limit?: number; offset?: number } = {},
): Promise<AgentVersionListResult> {
  try {
    const params = new URLSearchParams();
    if (options.limit !== undefined) {
      params.set('limit', String(options.limit));
    }
    if (options.offset !== undefined) {
      params.set('offset', String(options.offset));
    }
    const query = params.toString();
    const url = `${resolveApiBaseUrl()}/agents/${encodeURIComponent(agentId)}/versions${query ? `?${query}` : ''}`;
    const response = await fetch(url, {
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: `Versions endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentVersionList,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Versions endpoint unavailable',
    };
  }
}

export async function getAgentVersion(
  agentId: string,
  versionId: string,
): Promise<AgentVersionDetailResult> {
  try {
    const response = await fetch(
      `${resolveApiBaseUrl()}/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(versionId)}`,
      {
        cache: 'no-store',
      },
    );

    if (!response.ok) {
      return {
        ok: false,
        error: `Version endpoint returned HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentVersionDetail,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Version endpoint unavailable',
    };
  }
}

export async function createAgent(
  input: AgentCreateInput,
  options: DemoOperatorOptions = {},
): Promise<AgentDetailResult> {
  try {
    const response = await fetch(`${resolveApiBaseUrl()}/agents`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...demoOperatorHeaders(options.demoOperatorToken),
      },
      body: JSON.stringify(input),
      cache: 'no-store',
    });

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(response, `Agent creation returned HTTP ${response.status}`),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentDetail,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Agent creation unavailable',
    };
  }
}

export async function createAgentVersion(
  agentId: string,
  input: AgentVersionCreateInput,
  options: DemoOperatorOptions = {},
): Promise<AgentVersionDetailResult> {
  try {
    const response = await fetch(
      `${resolveApiBaseUrl()}/agents/${encodeURIComponent(agentId)}/versions`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...demoOperatorHeaders(options.demoOperatorToken),
        },
        body: JSON.stringify(input),
        cache: 'no-store',
      },
    );

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Version creation returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentVersionDetail,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Version creation unavailable',
    };
  }
}

export async function updateAgentVersion(
  agentId: string,
  versionId: string,
  input: AgentVersionUpdateInput,
  options: DemoOperatorOptions = {},
): Promise<AgentVersionDetailResult> {
  try {
    const response = await fetch(
      `${resolveApiBaseUrl()}/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(versionId)}`,
      {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          ...demoOperatorHeaders(options.demoOperatorToken),
        },
        body: JSON.stringify(input),
        cache: 'no-store',
      },
    );

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Version update returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as AgentVersionDetail,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Version update unavailable',
    };
  }
}

export async function publishAgentVersion(
  agentId: string,
  versionId: string,
  options: DemoOperatorOptions = {},
): Promise<PublishVersionResult> {
  try {
    const response = await fetch(
      `${resolveApiBaseUrl()}/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(versionId)}/publish`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...demoOperatorHeaders(options.demoOperatorToken),
        },
        cache: 'no-store',
      },
    );

    if (!response.ok) {
      return {
        ok: false,
        error: await readErrorMessage(
          response,
          `Version publish returned HTTP ${response.status}`,
        ),
      };
    }

    return {
      ok: true,
      data: (await response.json()) as PublishResult,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Version publish unavailable',
    };
  }
}
