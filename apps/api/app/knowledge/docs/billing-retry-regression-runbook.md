---
source_id: kb-runbook-billing-retry-regression
title: Billing Retry Regression Runbook
document_type: runbook
owner: Revenue Operations
tags: billing, retry, webhook, renewal, mrr
updated_at: 2026-06-06
---
# Billing Retry Regression Runbook

Use this runbook when paid MRR drops after failed renewals and support tickets mention retry behavior. The strongest signal is a group of active subscriptions with failed current-window invoices where the failure reason says the retry webhook suppressed a second charge attempt.

## Diagnosis

- Compare paid invoice MRR in the current seven-day window against the previous window.
- Filter active subscriptions with failed renewal invoices after June 5, 2026.
- Confirm whether the failed invoices share the retry webhook failure reason.
- Check billing tickets from the same accounts for updated card claims or finance follow-up requests.

## Operator Action

Do not send customer messages directly. Draft billing-owner follow-ups, attach invoice IDs, and create an approval request for each outbound message.
