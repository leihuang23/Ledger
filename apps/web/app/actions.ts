'use server';

import { redirect } from 'next/navigation';

import { createIncidentFromAnomaly } from '@/lib/api';

export async function openIncidentFromAnomaly(formData: FormData) {
  const anomalyId = formData.get('anomaly_id');
  if (typeof anomalyId !== 'string' || anomalyId.length === 0) {
    throw new Error('Missing anomaly id');
  }

  const incident = await createIncidentFromAnomaly(anomalyId);
  if (!incident.ok) {
    redirect(`/?incident_error=${encodeURIComponent(incident.error)}`);
  }

  redirect(`/incidents/${incident.data.id}`);
}
