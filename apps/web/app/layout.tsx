import type { Metadata } from 'next';
import { operatorMutationsEnabled } from '@/lib/operatorMutations';
import './globals.css';
import { Nav } from './Nav';

export const metadata: Metadata = {
  title: 'Ops Agent',
  description:
    'Evidence-backed SaaS operations agent with a governed control plane and evaluation studio',
  icons: [{ rel: 'icon', url: '/favicon.svg', type: 'image/svg+xml' }],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const mutationsEnabled = operatorMutationsEnabled();

  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main-content">
          Skip to content
        </a>
        <Nav />
        {!mutationsEnabled ? (
          <aside className="read-only-banner" role="status">
            Public read-only demo — operator mutations require a protected deployment.
          </aside>
        ) : null}
        <div id="main-content" tabIndex={-1}>
          {children}
        </div>
      </body>
    </html>
  );
}
