---
source_id: kb-product-note-billing-events
title: Product Note - Billing Events and Retry Jobs
document_type: product_note
owner: Billing Engineering
tags: billing, events, retry, jobs
updated_at: 2026-05-28
---
# Product Note - Billing Events and Retry Jobs

Billing retry jobs are created from invoice failure webhooks. A healthy retry flow records the failure, schedules the retry, and logs the charge attempt result.

## Regression Clues

If the failure webhook is recorded but no retry job exists, suspect retry dispatch. If retry jobs exist and fail with card expiration, classify the root cause as payment method expiration instead.
