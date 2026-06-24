---
source_id: kb-incident-response-import-outage
title: Incident Response - CSV Import Outage
document_type: incident_response
owner: Engineering On-call
tags: incident, csv, import, usage
updated_at: 2026-06-02
---
# Incident Response - CSV Import Outage

CSV import instability can depress weekly active usage without immediately affecting invoice MRR. Treat it as a product disruption and cite product events rather than billing records.

## Signals

- Spike in `import_failed` events across multiple accounts.
- Integration support tickets from admins.
- Lower recent active users for affected workspaces.
- No matching wave of failed renewal invoices.
