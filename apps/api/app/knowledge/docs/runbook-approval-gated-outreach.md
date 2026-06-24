---
source_id: kb-runbook-approval-gated-outreach
title: Approval-Gated Outreach Runbook
document_type: runbook
owner: Operations
tags: approvals, outreach, mock-actions, safety
updated_at: 2026-05-15
---
# Approval-Gated Outreach Runbook

All Slack, email, CRM, and task actions are mock actions in the first version of Ops Agent. The agent may draft a follow-up, but it must never mark it as sent.

## Required Fields

Every proposed outreach action needs recipient role, account ID, cited evidence, draft body, risk level, and pending approval status.

## Rejection Handling

If an approver rejects a draft, record the rejection and do not regenerate the same draft without new evidence.
