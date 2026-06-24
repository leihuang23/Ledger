---
source_id: kb-incident-response-billing-webhook
title: Incident Response - Billing Webhook Regression
document_type: incident_response
owner: Engineering On-call
tags: incident, billing, webhook, retry
updated_at: 2026-06-06
---
# Incident Response - Billing Webhook Regression

A billing webhook regression is suspected when retry attempts are missing for failed renewal invoices, especially when customers state that payment details were updated before the retry window.

## Severity

Classify as high severity when failed renewal amount exceeds $25,000 or more than five active accounts are affected in a weekly MRR window.

## Containment

Pause automated dunning emails, repair retry dispatch, replay failed retry jobs, and keep all customer messaging approval-gated.
