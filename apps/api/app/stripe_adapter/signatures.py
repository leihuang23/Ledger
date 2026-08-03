"""Stripe webhook signature verification (stdlib only; no stripe SDK required)."""

from __future__ import annotations

import hashlib
import hmac
import time


class SignatureVerificationError(ValueError):
    """Raised when a Stripe-Signature header does not match the payload."""


def verify_stripe_signature(
    payload: bytes,
    signature_header: str | None,
    secret: str,
    *,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> int:
    """Verify a Stripe webhook signature against the raw body.

    Returns the signed timestamp (unix seconds) on success.
    """
    if not signature_header:
        raise SignatureVerificationError("Missing Stripe-Signature header")
    if not secret:
        raise SignatureVerificationError("Webhook secret is not configured")

    timestamp, signatures = _parse_signature_header(signature_header)
    current = int(time.time()) if now is None else now
    if tolerance_seconds >= 0 and abs(current - timestamp) > tolerance_seconds:
        raise SignatureVerificationError("Timestamp outside tolerance window")

    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise SignatureVerificationError("Signature mismatch")
    return timestamp


def sign_payload_for_tests(
    payload: bytes,
    secret: str,
    *,
    timestamp: int | None = None,
) -> str:
    """Build a Stripe-Signature header for unit tests."""
    ts = int(time.time()) if timestamp is None else timestamp
    signed_payload = f"{ts}.".encode("utf-8") + payload
    digest = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={ts},v1={digest}"


def _parse_signature_header(header: str) -> tuple[int, list[str]]:
    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise SignatureVerificationError("Invalid signature timestamp") from exc
        elif key == "v1":
            signatures.append(value)
    if timestamp is None:
        raise SignatureVerificationError("Missing signature timestamp")
    if not signatures:
        raise SignatureVerificationError("Missing v1 signature")
    return timestamp, signatures
