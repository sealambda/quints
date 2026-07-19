"""Thin Stripe API client — just what balance-transaction imports need.

The official ``stripe`` SDK is large and moves fast; the import workflow
needs exactly three GET endpoints, so this stays deliberately small:

- ``balance_transactions(...)``  — GET /v1/balance_transactions (paginated)
- ``balance()``                  — GET /v1/balance
- ``account()``                  — GET /v1/account

Authenticate with a **restricted** API key (``rk_live_...``): *Balance
transaction sources: Read* (covers /v1/balance and /v1/balance_transactions)
plus *Charges: Read* so ``expand[]=data.source`` can resolve payee names.
No write scopes are needed — the importer only ever reads.
"""

from __future__ import annotations

import requests

API_HOST = "https://api.stripe.com"


class StripeError(RuntimeError):
    """Unexpected Stripe API response."""


def _created_key(txn: dict[str, object]) -> tuple[int, str]:
    created = txn.get("created")
    txn_id = txn.get("id")
    return (
        created if isinstance(created, int) else 0,
        txn_id if isinstance(txn_id, str) else "",
    )


class StripeClient:
    def __init__(
        self,
        api_key: str,
        *,
        host: str = API_HOST,
        session: requests.Session | None = None,
    ):
        self._host = host
        self._session = session or requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {api_key}", "User-Agent": "beangulp-stripe"}
        )

    def _get(self, path: str, params: dict[str, str | int] | None = None) -> dict[str, object]:
        response = self._session.get(self._host + path, params=params, timeout=30)
        if response.status_code != 200:
            try:
                message = response.json()["error"]["message"]
            except Exception:
                message = response.text[:200]
            raise StripeError(f"GET {path} → {response.status_code}: {message}")
        return response.json()

    def balance_transactions(
        self,
        created_gte: int | None = None,
        created_lte: int | None = None,
        *,
        expand_source: bool = True,
        page_size: int = 100,
    ) -> list[dict[str, object]]:
        """All balance transactions in the window, oldest first.

        ``created_gte``/``created_lte`` are unix timestamps. Stripe pages
        newest-first with ``starting_after`` cursors; the result is re-sorted
        ascending so drafts read chronologically.
        """
        params: dict[str, str | int] = {"limit": page_size}
        if created_gte is not None:
            params["created[gte]"] = created_gte
        if created_lte is not None:
            params["created[lte]"] = created_lte
        if expand_source:
            params["expand[]"] = "data.source"

        transactions: list[dict[str, object]] = []
        while True:
            page = self._get("/v1/balance_transactions", params)
            batch = page.get("data")
            if isinstance(batch, list):
                transactions.extend(t for t in batch if isinstance(t, dict))
            if not page.get("has_more"):
                break
            last_id = transactions[-1].get("id") if transactions else None
            if not isinstance(last_id, str):
                raise StripeError("balance transaction page without string ids")
            params = dict(params, starting_after=last_id)
        transactions.sort(key=_created_key)
        return transactions

    def balance(self) -> dict[str, object]:
        """Current balance (``available`` + ``pending`` per currency)."""
        return self._get("/v1/balance")

    def account(self) -> dict[str, object]:
        """The account the key belongs to (id, business profile)."""
        return self._get("/v1/account")
