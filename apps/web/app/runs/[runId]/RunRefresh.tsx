'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

const REFRESH_INTERVAL_MS = 2500;
const MAX_REFRESH_DURATION_MS = 10 * 60 * 1000;

export function RunRefresh({ active }: { active: boolean }) {
  const router = useRouter();

  useEffect(() => {
    if (!active) {
      return undefined;
    }

    const startedAt = Date.now();
    const interval = window.setInterval(() => {
      if (Date.now() - startedAt >= MAX_REFRESH_DURATION_MS) {
        window.clearInterval(interval);
      }
      router.refresh();
    }, REFRESH_INTERVAL_MS);

    return () => window.clearInterval(interval);
  }, [active, router]);

  if (!active) {
    return null;
  }

  return (
    <section className="panel anomaly-panel" aria-live="polite">
      <div className="panel-message">
        Control-plane run is still running. This page refreshes automatically.
      </div>
    </section>
  );
}
