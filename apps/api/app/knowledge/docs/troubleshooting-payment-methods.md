---
source_id: kb-troubleshooting-payment-methods
title: Troubleshooting Payment Method Updates
document_type: troubleshooting_note
owner: Billing Operations
tags: payment, card, billing, troubleshooting
updated_at: 2026-05-21
---
# Troubleshooting Payment Method Updates

Payment method updates should generate a billing event and make the next retry eligible. If a customer updated the card but the invoice still failed, compare the failure reason against card expiration and retry webhook regression.

## Evidence

- Billing settings update event.
- Failed invoice ID and failure reason.
- Support ticket subject and description.
- Active subscription status.
