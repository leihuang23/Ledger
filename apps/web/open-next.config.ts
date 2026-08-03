import { defineCloudflareConfig } from '@opennextjs/cloudflare';

// Ledger's public pages are dynamic and use no-store API reads, so no
// incremental cache binding is required for this deployment.
export default defineCloudflareConfig({});
