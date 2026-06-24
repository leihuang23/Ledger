---
source_id: kb-troubleshooting-csv-imports
title: Troubleshooting CSV Imports
document_type: troubleshooting_note
owner: Product Support
tags: csv, import, integration, troubleshooting
updated_at: 2026-05-25
---
# Troubleshooting CSV Imports

CSV imports can fail because of file size, invalid headers, date parsing, or transient worker backlog. The seeded incident pattern is intermittent failures across multiple accounts with `import_failed` product events.

## Checks

- Confirm the uploaded file is UTF-8 encoded.
- Verify required headers are present.
- Retry files under 20,000 rows.
- Look for repeated failure timestamps across accounts, which suggests platform instability.
