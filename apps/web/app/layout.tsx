import type { Metadata } from 'next';
import './globals.css';
import { Nav } from './Nav';

export const metadata: Metadata = {
  title: 'Ops Agent',
  description: 'SaaS revenue and support operations agent workspace',
  icons: [{ rel: 'icon', url: '/favicon.svg', type: 'image/svg+xml' }],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main-content">
          Skip to content
        </a>
        <Nav />
        <div id="main-content" tabIndex={-1}>
          {children}
        </div>
      </body>
    </html>
  );
}
