'use server';

import { redirect } from 'next/navigation';

import {
  approveApprovalRequest,
  createIncidentFromAnomaly,
  rejectApprovalRequest,
  startInvestigation,
} from '@/lib/api';

export async function openIncidentFromAnomaly(formData: FormData) {
  const anomalyId = formData.get('anomaly_id');
  if (typeof anomalyId !== 'string' || anomalyId.length === 0) {
    throw new Error('Missing anomaly id');
  }

  const incident = await createIncidentFromAnomaly(anomalyId);
  if (!incident.ok) {
    redirect(`/?incident_error=${encodeURIComponent(incident.error)}`);
  }

  redirect(`/incidents/${encodeURIComponent(incident.data.id)}`);
}

export async function startInvestigationFromIncident(formData: FormData) {
  const incidentId = formData.get('incident_id');
  if (typeof incidentId !== 'string' || incidentId.length === 0) {
    throw new Error('Missing incident id');
  }

  const encodedIncidentId = encodeURIComponent(incidentId);
  const run = await startInvestigation(incidentId);
  if (!run.ok) {
    redirect(`/incidents/${encodedIncidentId}?investigation_error=${encodeURIComponent(run.error)}`);
  }

  redirect(`/agent/runs/${encodeURIComponent(run.data.id)}`);
}

export async function approveApprovalFromRun(formData: FormData) {
  const approvalId = readRequiredFormValue(formData, 'approval_id');
  const runId = readRequiredFormValue(formData, 'run_id');
  const result = await approveApprovalRequest(
    approvalId,
    'Approved from the investigation approval queue.',
  );

  redirectToRun(runId, result.ok ? undefined : result.error);
}

export async function rejectApprovalFromRun(formData: FormData) {
  const approvalId = readRequiredFormValue(formData, 'approval_id');
  const runId = readRequiredFormValue(formData, 'run_id');
  const result = await rejectApprovalRequest(
    approvalId,
    'Rejected from the investigation approval queue.',
  );

  redirectToRun(runId, result.ok ? undefined : result.error);
}

function readRequiredFormValue(formData: FormData, key: string) {
  const value = formData.get(key);
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`Missing ${key}`);
  }
  return value;
}

function redirectToRun(runId: string, error?: string) {
  const encodedRunId = encodeURIComponent(runId);
  if (error) {
    redirect(`/agent/runs/${encodedRunId}?approval_error=${encodeURIComponent(error)}`);
  }
  redirect(`/agent/runs/${encodedRunId}`);
}
