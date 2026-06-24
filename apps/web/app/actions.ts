'use server';

import { redirect } from 'next/navigation';

import { createIncidentFromAnomaly, startInvestigation } from '@/lib/api';

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
