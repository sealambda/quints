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

from typing import cast
from urllib.parse import urljoin

import requests

API_HOST = "https://api.stripe.com"

# Document URLs bounce through a signing redirect or two before the file;
# bounded so a redirect loop fails loudly instead of hanging.
MAX_REDIRECTS = 5
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

# Most objects one paginated listing may return. Stripe's own SDKs set no
# page cap: stripe-python's auto_paging_iter loops ``while True``, and
# stripe-node's iterators recurse while ``has_more``. The one bound Stripe
# ships is stripe-node's ``autoPagingToArray``, which refuses a limit above
# 10,000 items ("use autoPagingEach to iterate through longer lists"):
# https://github.com/stripe/stripe-node/blob/master/src/autoPagination.ts
# At the API maximum of 100 objects per page (``limit`` ranges 1–100, default
# 10: https://docs.stripe.com/api/pagination) that is 100 requests. A window
# with more than that is split by the caller (a narrower created range).
MAX_ITEMS = 10_000

# Largest invoice PDF ``document`` accepts. Stripe renders these itself: at
# most 250 invoice items (https://docs.stripe.com/changelog/2016-02-22/
# no-more-than-250-invoice-items) plus a branding logo and icon of < 512 KB
# each (https://docs.stripe.com/invoicing/customize) — ~1 MiB of images. The
# bytes per rendered line item and embedded fonts are not documented, so the
# remaining 7 MiB of headroom is PENDING CONFIRMATION against real PDFs.
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024  # read buffer only; max_bytes is the bound


class StripeError(RuntimeError):
    """Unexpected Stripe API response."""


def _created_key(txn: dict[str, object]) -> tuple[int, str]:
    created = txn.get("created")
    txn_id = txn.get("id")
    return (
        created if isinstance(created, int) else 0,
        txn_id if isinstance(txn_id, str) else "",
    )


def _read_capped(response: requests.Response, url: str, max_bytes: int) -> bytes:
    """The streamed body, refusing more than ``max_bytes`` (declared or sent)."""
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise StripeError(f"GET {url}: larger than {max_bytes} bytes ({declared} declared)")
    body = bytearray()
    for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
        body += chunk
        if len(body) > max_bytes:
            raise StripeError(f"GET {url}: larger than {max_bytes} bytes")
    return bytes(body)


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
            except (ValueError, KeyError, TypeError):  # not Stripe's JSON error shape
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
        max_items: int = MAX_ITEMS,
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

        transactions = self._list("/v1/balance_transactions", params, page_size, max_items)
        transactions.sort(key=_created_key)
        return transactions

    def invoices(
        self,
        created_gte: int | None = None,
        created_lte: int | None = None,
        *,
        page_size: int = 100,
        max_items: int = MAX_ITEMS,
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

        invoices = self._list("/v1/invoices", params, page_size, max_items)
        invoices.sort(key=_created_key)
        return invoices

    def _list(
        self, path: str, params: dict[str, str | int], page_size: int, max_items: int
    ) -> list[dict[str, object]]:
        """Every object of a ``starting_after``-paginated listing, page order.

        Each page's cursor is its own last id; a page that claims more but is
        empty, or names the cursor it was asked for, would repeat forever.
        """
        objects: list[dict[str, object]] = []
        cursor: str | None = None
        for _ in range(-(-max_items // page_size)):  # ceil: pages to max_items
            page = self._get(path, params)
            batch = page.get("data")
            if not isinstance(batch, list) or not all(isinstance(i, dict) for i in batch):
                raise StripeError(f"GET {path}: data is not a list of objects")
            items = cast("list[dict[str, object]]", batch)
            objects.extend(items)
            if not page.get("has_more"):
                return objects
            if not items:
                raise StripeError(f"GET {path}: empty page with has_more after {cursor!r}")
            last_id = items[-1].get("id")
            if not isinstance(last_id, str):
                raise StripeError(f"GET {path}: page without string ids")
            if last_id == cursor:
                raise StripeError(f"GET {path}: cursor did not advance past {cursor!r}")
            cursor = last_id
            params = dict(params, starting_after=cursor)
        raise StripeError(
            f"GET {path}: more than {max_items} objects in one listing; "
            "narrow the window (created range)"
        )

    def document(
        self,
        url: str,
        *,
        max_redirects: int = MAX_REDIRECTS,
        max_bytes: int = MAX_DOCUMENT_BYTES,
    ) -> bytes:
        """Download the document behind a Stripe document URL, as bytes.

        ``invoice_pdf`` is a signed, short-lived URL that redirects to storage,
        so the hops are followed here rather than delegated: the count stays
        bounded, and each hop is visible. No ``Authorization`` is sent — the
        signed URL needs none, and the key must not travel to whatever host
        the redirect names.
        """
        for _ in range(max_redirects + 1):
            with self._session.get(url, timeout=60, allow_redirects=False, stream=True) as response:
                if response.status_code in REDIRECT_CODES:
                    location = response.headers.get("Location")
                    if not location:
                        raise StripeError(f"GET {url} → {response.status_code} without a Location")
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise StripeError(f"GET {url} → {response.status_code}")
                return _read_capped(response, url, max_bytes)
        raise StripeError(f"more than {max_redirects} redirects fetching {url}")

    def balance(self) -> dict[str, object]:
        """Current balance (``available`` + ``pending`` per currency)."""
        return self._get("/v1/balance")

    def account(self) -> dict[str, object]:
        """The account the key belongs to (id, business profile)."""
        return self._get("/v1/account")
