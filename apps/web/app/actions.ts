'use server';

import { redirect } from 'next/navigation';

import {
  approveApprovalRequest,
  createAgentVersion,
  createIncidentFromAnomaly,
  launchRun,
  publishAgentVersion as apiPublishAgentVersion,
  rejectApprovalRequest,
  runEvalDataset,
  runEvalSuite,
  startInvestigation,
  updateAgentVersion,
} from '@/lib/api';
import { requireOperatorMutationsEnabled } from '@/lib/operatorMutations';

export async function openIncidentFromAnomaly(formData: FormData) {
  requireOperatorMutationsEnabled();
  const anomalyId = formData.get('anomaly_id');
  if (typeof anomalyId !== 'string' || anomalyId.length === 0) {
    throw new Error('Missing anomaly id');
  }

  const incident = await createIncidentFromAnomaly(anomalyId, demoOperatorOptions());
  if (!incident.ok) {
    redirect(`/?incident_error=${encodeURIComponent(incident.error)}`);
  }

  redirect(`/incidents/${encodeURIComponent(incident.data.id)}`);
}

export async function startInvestigationFromIncident(formData: FormData) {
  requireOperatorMutationsEnabled();
  const incidentId = readRequiredFormValue(formData, 'incident_id');
  const agentVersionId = formData.get('agent_version_id');

  const encodedIncidentId = encodeURIComponent(incidentId);
  const options: { runInline: boolean; demoOperatorToken?: string; agentVersionId?: string } = {
    runInline: true,
    ...demoOperatorOptions(),
  };
  if (typeof agentVersionId === 'string' && agentVersionId.length > 0) {
    options.agentVersionId = agentVersionId;
  }
  const run = await startInvestigation(incidentId, options);
  if (!run.ok) {
    redirect(`/incidents/${encodedIncidentId}?investigation_error=${encodeURIComponent(run.error)}`);
  }

  redirect(`/agent/runs/${encodeURIComponent(run.data.id)}`);
}

export async function approveApprovalFromRun(formData: FormData) {
  requireOperatorMutationsEnabled();
  const approvalId = readRequiredFormValue(formData, 'approval_id');
  const runId = readRequiredFormValue(formData, 'run_id');
  const surface = readRunSurface(formData);
  const result = await approveApprovalRequest(
    approvalId,
    'Approved from the investigation approval queue.',
    demoOperatorOptions(),
  );

  redirectToRun(runId, result.ok ? undefined : result.error, surface);
}

export async function rejectApprovalFromRun(formData: FormData) {
  requireOperatorMutationsEnabled();
  const approvalId = readRequiredFormValue(formData, 'approval_id');
  const runId = readRequiredFormValue(formData, 'run_id');
  const surface = readRunSurface(formData);
  const result = await rejectApprovalRequest(
    approvalId,
    'Rejected from the investigation approval queue.',
    demoOperatorOptions(),
  );

  redirectToRun(runId, result.ok ? undefined : result.error, surface);
}

export async function approveApprovalFromQueue(formData: FormData) {
  requireOperatorMutationsEnabled();
  const approvalId = readRequiredFormValue(formData, 'approval_id');
  const result = await approveApprovalRequest(
    approvalId,
    'Approved from the global approvals queue.',
    demoOperatorOptions(),
  );

  redirectToApprovals(formData, result.ok ? undefined : result.error);
}

export async function rejectApprovalFromQueue(formData: FormData) {
  requireOperatorMutationsEnabled();
  const approvalId = readRequiredFormValue(formData, 'approval_id');
  const result = await rejectApprovalRequest(
    approvalId,
    'Rejected from the global approvals queue.',
    demoOperatorOptions(),
  );

  redirectToApprovals(formData, result.ok ? undefined : result.error);
}

export async function runEvalDatasetFromStudio(formData: FormData) {
  requireOperatorMutationsEnabled();
  const datasetId = readRequiredFormValue(formData, 'dataset_id');
  const agentVersionId = readRequiredFormValue(formData, 'agent_version_id');
  const params = new URLSearchParams({
    dataset_id: datasetId,
    results_version_id: agentVersionId,
  });
  copySafeQueryValue(formData, params, 'version_a');
  copySafeQueryValue(formData, params, 'version_b');

  const result = await runEvalDataset(datasetId, agentVersionId, {
    ...demoOperatorOptions(),
    evalRunToken: process.env.EVAL_RUN_TOKEN,
  });

  if (!result.ok) {
    params.set('eval_error', result.error);
  } else {
    params.set(
      'eval_notice',
      `Eval run ${result.data.eval_run_id} queued for the selected agent version.`,
    );
  }
  redirect(`/evals?${params.toString()}`);
}

export async function runEvalSuiteFromReport() {
  requireOperatorMutationsEnabled();
  const result = await runEvalSuite();
  if (!result.ok) {
    redirect(`/evals?eval_error=${encodeURIComponent(result.error)}`);
  }

  if (result.data.status === 'running') {
    redirect(
      `/evals?eval_notice=${encodeURIComponent(
        'Eval suite enqueued — results will be available shortly.',
      )}`,
    );
  }

  redirect('/evals');
}

export async function launchControlPlaneRun(formData: FormData) {
  requireOperatorMutationsEnabled();
  const agentVersionId = readRequiredFormValue(formData, 'agent_version_id');
  const agentId = readRequiredFormValue(formData, 'agent_id');
  const incidentId = readRequiredFormValue(formData, 'incident_id');

  const run = await launchRun(
    {
      agent_version_id: agentVersionId,
      input_payload: {},
      incident_id: incidentId,
      run_inline: true,
    },
    demoOperatorOptions(),
  );

  const versionPath = `/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(agentVersionId)}`;
  if (!run.ok) {
    redirect(`${versionPath}?launch_error=${encodeURIComponent(run.error)}`);
  }

  redirect(`/runs/${encodeURIComponent(run.data.id)}`);
}

export async function saveAgentVersionDraft(formData: FormData) {
  requireOperatorMutationsEnabled();
  const agentId = readRequiredFormValue(formData, 'agent_id');
  const versionId = formData.get('version_id');
  const baseVersionId = formData.get('base_version_id');
  const systemPrompt = formData.get('system_prompt');
  const model = formData.get('model');
  const temperatureRaw = formData.get('temperature');
  const maxTokensRaw = formData.get('max_tokens');
  const toolsPresent = formData.get('enabled_tool_ids_present');
  const scopesPresent = formData.get('allowed_scopes_present');
  const returnTo = formData.get('return_to');

  const returnPath =
    typeof returnTo === 'string' && returnTo.startsWith('/') && !returnTo.startsWith('//')
      ? returnTo
      : `/agents/${encodeURIComponent(agentId)}`;
  const enabledToolIds: string[] =
    toolsPresent === '1'
      ? formData.getAll('enabled_tool_ids').filter((v): v is string => typeof v === 'string')
      : [];
  const shouldSetEnabledTools = toolsPresent === '1';
  const allowedScopes: string[] =
    scopesPresent === '1'
      ? formData.getAll('allowed_scopes').filter((v): v is string => typeof v === 'string')
      : [];
  const shouldSetAllowedScopes = scopesPresent === '1';

  let temperature: number | undefined;
  if (typeof temperatureRaw === 'string' && temperatureRaw.length > 0) {
    temperature = Number(temperatureRaw);
    if (Number.isNaN(temperature)) {
      redirect(`${returnPath}?version_error=${encodeURIComponent('Invalid temperature value')}`);
    }
  }

  let maxTokens: number | undefined;
  if (typeof maxTokensRaw === 'string' && maxTokensRaw.length > 0) {
    maxTokens = Number(maxTokensRaw);
    if (Number.isNaN(maxTokens) || maxTokens <= 0) {
      redirect(`${returnPath}?version_error=${encodeURIComponent('Invalid max tokens value')}`);
    }
  }

  const versionInput = {
    system_prompt: typeof systemPrompt === 'string' && systemPrompt.length > 0 ? systemPrompt : undefined,
    model: typeof model === 'string' && model.length > 0 ? model : undefined,
    temperature,
    max_tokens: maxTokens,
    enabled_tool_ids: shouldSetEnabledTools ? enabledToolIds : undefined,
    allowed_scopes: shouldSetAllowedScopes ? allowedScopes : undefined,
  };

  let result;
  if (typeof versionId === 'string' && versionId.length > 0) {
    result = await updateAgentVersion(agentId, versionId, versionInput, demoOperatorOptions());
  } else {
    result = await createAgentVersion(
      agentId,
      {
        fork_from_version_id:
          typeof baseVersionId === 'string' && baseVersionId.length > 0 ? baseVersionId : undefined,
        ...versionInput,
      },
      demoOperatorOptions(),
    );
  }

  if (!result.ok) {
    redirect(`${returnPath}?version_error=${encodeURIComponent(result.error)}`);
  }

  if (result.data.version_number === null) {
    redirect(`/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(result.data.id)}?draft_saved=1`);
  }

  redirect(returnPath);
}

export async function publishAgentVersion(formData: FormData) {
  requireOperatorMutationsEnabled();
  const versionId = readRequiredFormValue(formData, 'version_id');
  const agentId = readRequiredFormValue(formData, 'agent_id');

  const result = await apiPublishAgentVersion(agentId, versionId, demoOperatorOptions());

  if (!result.ok) {
    redirect(
      `/agents/${encodeURIComponent(agentId)}/versions/${encodeURIComponent(versionId)}?publish_error=${encodeURIComponent(result.error)}`,
    );
  }

  redirect(`/agents/${encodeURIComponent(agentId)}?version_published=${encodeURIComponent(versionId)}`);
}

function readRequiredFormValue(formData: FormData, key: string) {
  const value = formData.get(key);
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`Missing ${key}`);
  }
  return value;
}

function demoOperatorOptions() {
  return {
    demoOperatorToken: process.env.DEMO_OPERATOR_TOKEN,
  };
}

function redirectToRun(
  runId: string,
  error?: string,
  surface: 'agent' | 'control-plane' = 'agent',
) {
  const encodedRunId = encodeURIComponent(runId);
  const basePath = surface === 'control-plane' ? '/runs' : '/agent/runs';
  if (error) {
    redirect(`${basePath}/${encodedRunId}?approval_error=${encodeURIComponent(error)}`);
  }
  redirect(`${basePath}/${encodedRunId}`);
}

function readRunSurface(formData: FormData): 'agent' | 'control-plane' {
  return formData.get('surface') === 'control-plane' ? 'control-plane' : 'agent';
}

function redirectToApprovals(formData: FormData, error?: string) {
  const params = new URLSearchParams();
  copySafeQueryValue(formData, params, 'status');
  copySafeQueryValue(formData, params, 'agent_version_id');
  copySafeQueryValue(formData, params, 'risk_level');
  copySafeQueryValue(formData, params, 'include_decided');
  if (error) {
    params.set('approval_error', error);
  }
  const query = params.toString();
  redirect(`/approvals${query ? `?${query}` : ''}`);
}

function copySafeQueryValue(formData: FormData, params: URLSearchParams, key: string) {
  const value = formData.get(key);
  if (typeof value === 'string' && value.length > 0) {
    params.set(key, value);
  }
}
