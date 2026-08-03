import type { NextConfig } from 'next';
import { initOpenNextCloudflareForDev } from '@opennextjs/cloudflare';

const nextConfig: NextConfig = {
  outputFileTracingRoot: process.cwd(),
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=63072000; includeSubDomains; preload',
          },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
          {
            key: 'Content-Security-Policy',
            value:
              "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self' http://localhost:8000 https:; form-action 'self'",
          },
        ],
      },
    ];
  },
};

export default nextConfig;

// The OpenNext Cloudflare dev integration spins up a local workerd instance to
// provide the Cloudflare runtime context. It is only needed by `next dev`; in
// production (`next build` / `next start`) workerd must not be spawned, because
// the standard Node server serves the app and the binary is not available on
// every runtime (e.g. Alpine/musl Docker images).
if (process.env.NODE_ENV === 'development') {
  initOpenNextCloudflareForDev();
}
