"""Optional Stripe test-mode evidence adapter.

Normalizes sandbox billing events into existing Ledger account / subscription /
invoice records. Live credentials, Checkout, refunds, Connect, and OAuth are
out of scope. See ``prd.md`` Stripe evidence boundary.
"""

from app.stripe_adapter.ingest import process_stripe_event
from app.stripe_adapter.reconcile import reconcile_stripe_sandbox
from app.stripe_adapter.signatures import SignatureVerificationError, verify_stripe_signature

__all__ = [
    "SignatureVerificationError",
    "process_stripe_event",
    "reconcile_stripe_sandbox",
    "verify_stripe_signature",
]
