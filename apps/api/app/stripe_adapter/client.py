"""Thin Stripe REST client for test-mode object re-fetch and reconciliation.

Uses httpx only so the adapter stays optional: when no API key is configured
the webhook path still works from embedded event payloads (with stale-snapshot
guards). Live mode is rejected at the call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

STRIPE_API_BASE = "https://api.stripe.com/v1"


class StripeClient(Protocol):
    def retrieve_customer(self, customer_id: str) -> dict[str, Any]: ...

    def retrieve_subscription(self, subscription_id: str) -> dict[str, Any]: ...

    def retrieve_invoice(self, invoice_id: str) -> dict[str, Any]: ...

    def list_customers(self, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def list_subscriptions(self, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def list_invoices(self, *, limit: int = 100) -> list[dict[str, Any]]: ...


@dataclass
class HttpStripeClient:
    """Minimal test-mode Stripe client over the REST API."""

    api_key: str
    base_url: str = STRIPE_API_BASE
    timeout_seconds: float = 15.0
    _client: httpx.Client | None = field(default=None, repr=False)

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                auth=(self.api_key, ""),
                timeout=self.timeout_seconds,
                headers={"Stripe-Version": "2024-06-20"},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._http().get(path, params=params)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Stripe response for {path}")
        return data

    def _list(self, path: str, *, limit: int) -> list[dict[str, Any]]:
        data = self._get(path, params={"limit": min(limit, 100)})
        items = data.get("data") or []
        return [item for item in items if isinstance(item, dict)]

    def retrieve_customer(self, customer_id: str) -> dict[str, Any]:
        return self._get(f"/customers/{customer_id}")

    def retrieve_subscription(self, subscription_id: str) -> dict[str, Any]:
        return self._get(f"/subscriptions/{subscription_id}")

    def retrieve_invoice(self, invoice_id: str) -> dict[str, Any]:
        return self._get(f"/invoices/{invoice_id}")

    def list_customers(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self._list("/customers", limit=limit)

    def list_subscriptions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self._list("/subscriptions", limit=limit)

    def list_invoices(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self._list("/invoices", limit=limit)


@dataclass
class InMemoryStripeClient:
    """Fixture-backed client for CI and high-fidelity sandbox simulation."""

    customers: dict[str, dict[str, Any]] = field(default_factory=dict)
    subscriptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    invoices: dict[str, dict[str, Any]] = field(default_factory=dict)

    def retrieve_customer(self, customer_id: str) -> dict[str, Any]:
        try:
            return self.customers[customer_id]
        except KeyError as exc:
            raise LookupError(f"Unknown customer {customer_id}") from exc

    def retrieve_subscription(self, subscription_id: str) -> dict[str, Any]:
        try:
            return self.subscriptions[subscription_id]
        except KeyError as exc:
            raise LookupError(f"Unknown subscription {subscription_id}") from exc

    def retrieve_invoice(self, invoice_id: str) -> dict[str, Any]:
        try:
            return self.invoices[invoice_id]
        except KeyError as exc:
            raise LookupError(f"Unknown invoice {invoice_id}") from exc

    def list_customers(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.customers.values())[:limit]

    def list_subscriptions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.subscriptions.values())[:limit]

    def list_invoices(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.invoices.values())[:limit]


def build_stripe_client(api_key: str | None) -> HttpStripeClient | None:
    if not api_key:
        return None
    if not api_key.startswith("sk_test_"):
        raise ValueError(
            "Only Stripe test-mode secret keys (sk_test_...) are permitted"
        )
    return HttpStripeClient(api_key=api_key)
