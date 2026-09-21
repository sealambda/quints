"""Thin Stripe API client — just what balance-transaction imports need.

The official ``stripe`` SDK is large and moves fast; the import workflow
needs a handful of GET endpoints, so this stays deliberately small:

- ``balance_transactions(...)``  — GET /v1/balance_transactions (paginated)
- ``balance()``                  — GET /v1/balance
- ``account()``                  — GET /v1/account
- ``invoices(...)``              — GET /v1/invoices (paginated)
- ``document(url)``              — the customer-facing PDF behind a document URL

Authenticate with a **restricted** API key (``rk_live_...``): *Balance
transaction sources: Read* (covers /v1/balance and /v1/balance_transactions)
plus *Charges: Read* so ``expand[]=data.source`` can resolve payee names, plus
*Invoices: Read* for ``invoices()``. No write scopes are needed — the importer
only ever reads.
"""

from __future__ import annotations

from urllib.parse import urljoin

import requests

API_HOST = "https://api.stripe.com"

# Document URLs bounce through a signing redirect or two before the file;
# bounded so a redirect loop fails loudly instead of hanging.
MAX_REDIRECTS = 5
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


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
        self._session.headers.update({"User-Agent": "beangulp-stripe"})
        # Auth rides on each API request rather than on the session: document
        # URLs are signed and need no key, and a key on the session would
        # follow their redirects to whatever host they name.
        self._auth = {"Authorization": f"Bearer {api_key}"}

    def _get(self, path: str, params: dict[str, str | int] | None = None) -> dict[str, object]:
        response = self._session.get(
            self._host + path, params=params, headers=self._auth, timeout=30
        )
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

    def invoices(
        self,
        created_gte: int | None = None,
        created_lte: int | None = None,
        *,
        page_size: int = 100,
    ) -> list[dict[str, object]]:
        """All invoices created in the window, oldest first.

        Needs *Invoices: Read* on the key. Each invoice carries ``invoice_pdf``
        — the customer-facing invoice PDF. Those URLs are short-lived, so hand
        one to :meth:`document` right away rather than storing it.
        """
        params: dict[str, str | int] = {"limit": page_size}
        if created_gte is not None:
            params["created[gte]"] = created_gte
        if created_lte is not None:
            params["created[lte]"] = created_lte

        invoices: list[dict[str, object]] = []
        while True:
            page = self._get("/v1/invoices", params)
            batch = page.get("data")
            if isinstance(batch, list):
                invoices.extend(i for i in batch if isinstance(i, dict))
            if not page.get("has_more"):
                break
            last_id = invoices[-1].get("id") if invoices else None
            if not isinstance(last_id, str):
                raise StripeError("invoice page without string ids")
            params = dict(params, starting_after=last_id)
        invoices.sort(key=_created_key)
        return invoices

    def document(self, url: str, *, max_redirects: int = MAX_REDIRECTS) -> bytes:
        """Download the document behind a Stripe document URL, as bytes.

        ``invoice_pdf`` is a signed, short-lived URL that redirects to storage,
        so the hops are followed here rather than delegated: the count stays
        bounded, and each hop is visible. No ``Authorization`` is sent — the
        signed URL needs none, and the key must not travel to whatever host
        the redirect names.
        """
        for _ in range(max_redirects + 1):
            response = self._session.get(url, timeout=60, allow_redirects=False)
            if response.status_code in REDIRECT_CODES:
                location = response.headers.get("Location")
                if not location:
                    raise StripeError(f"GET {url} → {response.status_code} without a Location")
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                raise StripeError(f"GET {url} → {response.status_code}")
            return response.content
        raise StripeError(f"more than {max_redirects} redirects fetching {url}")

    def balance(self) -> dict[str, object]:
        """Current balance (``available`` + ``pending`` per currency)."""
        return self._get("/v1/balance")

    def account(self) -> dict[str, object]:
        """The account the key belongs to (id, business profile)."""
        return self._get("/v1/account")
