import { getHealth } from '@/lib/api';

export default async function Home() {
  const health = await getHealth();

  return (
    <main
      style={{
        display: 'grid',
        minHeight: '100vh',
        gridTemplateColumns: 'minmax(0, 1fr)',
        alignItems: 'center',
        padding: '48px 24px',
      }}
    >
      <section
        style={{
          width: '100%',
          maxWidth: 960,
          margin: '0 auto',
        }}
      >
        <p
          style={{
            margin: '0 0 12px',
            color: 'var(--accent)',
            fontSize: 14,
            fontWeight: 700,
            letterSpacing: 0,
            textTransform: 'uppercase',
          }}
        >
          Ops Agent
        </p>
        <h1
          style={{
            maxWidth: 720,
            margin: '0 0 16px',
            fontSize: 44,
            lineHeight: 1.08,
            letterSpacing: 0,
          }}
        >
          Revenue and support incident investigation workspace
        </h1>
        <p
          style={{
            maxWidth: 680,
            margin: '0 0 28px',
            color: 'var(--muted)',
            fontSize: 18,
            lineHeight: 1.6,
          }}
        >
          Initial monorepo scaffold is online. The next useful milestone is the
          seeded incident dataset and evidence-backed investigation flow.
        </p>

        <div
          style={{
            display: 'grid',
            gap: 12,
            maxWidth: 520,
            padding: 20,
            background: 'var(--panel)',
            border: '1px solid var(--border)',
            borderRadius: 8,
          }}
        >
          <strong>Backend health</strong>
          <code
            style={{
              overflowWrap: 'anywhere',
              color: health.status === 'ok' ? 'var(--accent)' : '#9f1d1d',
            }}
          >
            {JSON.stringify(health)}
          </code>
        </div>
      </section>
    </main>
  );
}

