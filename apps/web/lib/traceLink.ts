// The API's local trace provider writes trace URLs of the form
//   local://agent-runs/{run_id}/traces/{trace_id}
// (apps/api/app/agent/tracing.py). They carry no resolvable host, so the web
// app renders them as internal links to the run detail page, which shows the
// full step timeline for the run. Hosted providers (langfuse/langsmith)
// return http(s) URLs and are linked externally, unchanged.
const LOCAL_TRACE_URL_PREFIX = 'local://agent-runs/';

export function localTraceRunPath(traceUrl: string | null | undefined): string | null {
  if (!traceUrl || !traceUrl.startsWith(LOCAL_TRACE_URL_PREFIX)) {
    return null;
  }
  const [runId, marker] = traceUrl.slice(LOCAL_TRACE_URL_PREFIX.length).split('/');
  if (!runId || (marker !== undefined && marker !== 'traces')) {
    return null;
  }
  return `/agent/runs/${encodeURIComponent(runId)}`;
}
