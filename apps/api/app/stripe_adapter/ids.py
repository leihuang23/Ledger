"""Deterministic Ledger primary keys for Stripe sandbox objects."""

from __future__ import annotations

import hashlib


def ledger_account_id(stripe_customer_id: str) -> str:
    digest = hashlib.sha256(stripe_customer_id.encode("utf-8")).hexdigest()[:16]
    return f"acc_s_{digest}"


def ledger_subscription_id(stripe_subscription_id: str) -> str:
    digest = hashlib.sha256(stripe_subscription_id.encode("utf-8")).hexdigest()[:16]
    return f"sub_s_{digest}"


def ledger_invoice_id(stripe_invoice_id: str) -> str:
    digest = hashlib.sha256(stripe_invoice_id.encode("utf-8")).hexdigest()[:16]
    return f"inv_s_{digest}"


def ingestion_log_id(prefix: str, material: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{material}".encode("utf-8")).hexdigest()[:20]
    return f"sil_{digest}"


def reconciliation_run_id(material: str) -> str:
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"srr_{digest}"
