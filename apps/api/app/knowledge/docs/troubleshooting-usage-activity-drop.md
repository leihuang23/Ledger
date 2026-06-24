---
source_id: kb-troubleshooting-usage-activity-drop
title: Troubleshooting Usage Activity Drops
document_type: troubleshooting_note
owner: Product Analytics
tags: usage, active-users, product-events, investigation
updated_at: 2026-05-26
---
# Troubleshooting Usage Activity Drops

Usage drops should be diagnosed from product events and user last-seen timestamps before making a revenue claim. An import outage can reduce activity without immediately lowering paid MRR.

## Checks

- Compare seven-day active users with 30-day active users.
- Group product events by event name and source scenario.
- Look for missing workflow runs or repeated import failures.
- Cite event groups and affected account counts.
