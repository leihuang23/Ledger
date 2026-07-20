import Link from 'next/link';

import { localTraceRunPath } from '@/lib/traceLink';

type TraceLinkProps = {
  traceUrl: string | null;
  // Local trace URLs keep the trace id as the link text so it stays copyable.
  traceId: string | null;
  // Label for hosted-provider (http) links; matches each surface's previous text.
  externalLabel: string;
  // Plain-text fallback when there is no linkable URL.
  fallback: string;
  // List pages open hosted traces in a new tab; detail pages navigate in place.
  externalNewTab?: boolean;
};

export function TraceLink({
  traceUrl,
  traceId,
  externalLabel,
  fallback,
  externalNewTab = false,
}: TraceLinkProps) {
  const localPath = localTraceRunPath(traceUrl);
  if (localPath) {
    return <Link href={localPath}>{traceId ?? traceUrl}</Link>;
  }
  if (traceUrl && traceUrl.startsWith('http')) {
    if (externalNewTab) {
      return (
        <a href={traceUrl} target="_blank" rel="noreferrer">
          {externalLabel}
        </a>
      );
    }
    return <a href={traceUrl}>{externalLabel}</a>;
  }
  return <>{fallback}</>;
}
