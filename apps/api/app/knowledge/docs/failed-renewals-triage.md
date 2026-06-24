---
source_id: kb-runbook-failed-renewals-triage
title: Failed Renewals Triage
document_type: runbook
owner: Billing Operations
tags: billing, renewals, invoices, dunning
updated_at: 2026-05-29
---
# Failed Renewals Triage

Failed renewals are revenue-impacting only when the subscription is still active and the invoice is inside the active renewal window. Ignore recovered historical card declines unless they remain unpaid.

## Checks

- Join invoices to subscriptions so canceled accounts are not counted as recoverable failed renewals.
- Group failed amounts by account and segment.
- Read the latest support ticket for each account before drafting outreach.
- Inspect failure reason text for payment method expiration, retry webhook suppression, or procurement hold.

## False Leads

A high failed invoice count is not always the root cause. Some failed invoices recover manually, and some void invoices belong to canceled enterprise contracts.
