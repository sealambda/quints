"""beangulp importer for Stripe balance transactions.

Input is a JSON file of ``/v1/balance_transactions`` data — either the raw
Stripe list response, a bare array, or the wrapper the fetch helper writes:

.. code-block:: json

    {
      "account": {"id": "acct_...", ...},          // optional, from /v1/account
      "balance": {"as_of": "YYYY-MM-DD", ...},     // optional, from /v1/balance
      "data": [ {"object": "balance_transaction", ...}, ... ]
    }

One beancount transaction is drafted per balance transaction:

- the ``txn_...`` id becomes metadata (``stripe_id:`` by default) and is the
  idempotency key — ``extract()`` skips ids already present anywhere in the
  existing ledger.
- Stripe amounts are **minor units**; conversion honours zero- and
  three-decimal currencies (:func:`major_units`).
- the cash leg books the **net**; a non-zero per-transaction ``fee`` becomes
  an explicit posting to ``fees_account`` so the counter leg is the gross.
  ``fee_details`` of type ``tax`` (VAT Stripe charges on its own fees) are
  split out to ``tax_account`` when configured.
- ``stripe_fee`` transactions (separately billed fees, debited from the
  balance) draft the counter leg to ``fees_account`` (net of the split-out
  tax), review-flagged: the monthly tax invoice documents them and the VAT
  conversion happens at review time.
- ``payee_rules`` (``(regex, account, flag)``) draft the counter leg for
  everything else; anything unmatched keeps the ``!`` review flag and the
  cash leg only.
- a ``balance`` snapshot in the wrapper becomes a ``balance`` assertion per
  mapped currency (``available`` + ``pending``), dated the day after
  ``as_of``.

The API client lives in :mod:`beangulp_stripe.client`; the importer itself
only reads files, so fetching stays auditable and replayable.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from typing import Sequence

import beangulp
from beancount.core import data, flags
from beancount.core.amount import Amount

from .client import StripeClient, StripeError  # re-export

__all__ = ["Importer", "major_units", "StripeClient", "StripeError"]

# Currencies whose minor unit is not 1/100 (Stripe docs: currencies guide).
ZERO_DECIMAL = frozenset(
    "bif clp djf gnf jpy kmf krw mga pyg rwf ugx vnd vuv xaf xof xpf".split()
)
THREE_DECIMAL = frozenset("bhd jod kwd omr tnd".split())

# Balance-transaction types whose counter leg IS the fee account.
FEE_TYPES = frozenset({"stripe_fee"})


def major_units(amount: int, currency: str) -> Decimal:
    """Stripe minor units → decimal amount (``14900, "eur"`` → ``149.00``)."""
    code = currency.lower()
    exponent = 0 if code in ZERO_DECIMAL else 3 if code in THREE_DECIMAL else 2
    return Decimal(amount).scaleb(-exponent)


def _payee(txn: dict) -> str:
    if txn.get("type") in FEE_TYPES:
        return "Stripe"
    source = txn.get("source")
    if isinstance(source, dict):
        billing = source.get("billing_details") or {}
        name = billing.get("name") or billing.get("email")
        if name:
            return name
    if txn.get("type") == "payout":
        return "Stripe"
    return (txn.get("description") or "").strip()


class Importer(beangulp.Importer):
    """Importer for Stripe balance-transaction JSON files."""

    def __init__(
        self,
        account_map: dict[str, str],
        *,
        fees_account: str | None = None,
        tax_account: str | None = None,
        account_id: str | None = None,
        meta_key: str = "stripe_id",
        payee_rules: Sequence[tuple[str, str, str]] = (),
        review_flag: str = flags.FLAG_WARNING,
    ):
        self._accounts = {ccy.upper(): acct for ccy, acct in account_map.items()}
        self._fees_account = fees_account
        self._tax_account = tax_account
        self._account_id = account_id
        self._meta_key = meta_key
        self._rules = [
            (re.compile(pattern, re.IGNORECASE), account, flag)
            for pattern, account, flag in payee_rules
        ]
        self._review_flag = review_flag

    # ── beangulp interface ───────────────────────────────────────────────────

    def _load(self, filepath: str) -> dict | None:
        try:
            with open(filepath, encoding="utf-8") as f:
                document = json.load(f)
        except (OSError, ValueError):
            return None
        if isinstance(document, list):
            document = {"data": document}
        if not isinstance(document, dict) or not isinstance(document.get("data"), list):
            return None
        transactions = document["data"]
        if not all(
            isinstance(t, dict) and t.get("object", "balance_transaction") == "balance_transaction"
            for t in transactions
        ):
            return None
        currencies = {(t.get("currency") or "").upper() for t in transactions}
        snapshot = document.get("balance") or {}
        for bucket in ("available", "pending"):
            currencies |= {
                (p.get("currency") or "").upper() for p in snapshot.get(bucket) or []
            }
        if not currencies & self._accounts.keys():
            return None
        if self._account_id:
            held = (document.get("account") or {}).get("id")
            if held and held != self._account_id:
                return None
        return document

    def identify(self, filepath: str) -> bool:
        return self._load(filepath) is not None

    def account(self, filepath: str) -> str:
        document = self._load(filepath)
        if not document:
            return ""
        for txn in document["data"]:
            mapped = self._accounts.get((txn.get("currency") or "").upper())
            if mapped:
                return mapped
        return next(iter(self._accounts.values()), "")

    def date(self, filepath: str):
        document = self._load(filepath)
        if document and document["data"]:
            newest = max(t.get("created") or 0 for t in document["data"])
            if newest:
                return dt.datetime.fromtimestamp(newest, tz=dt.timezone.utc).date()
        return None

    def filename(self, filepath: str) -> str:
        return "balance-transactions.json"

    def extract(self, filepath: str, existing: data.Entries) -> data.Entries:
        document = self._load(filepath)
        if document is None:
            return []
        seen = _existing_references(existing, self._meta_key)

        entries: data.Entries = []
        for index, txn in enumerate(document["data"]):
            currency = (txn.get("currency") or "").upper()
            cash_account = self._accounts.get(currency)
            if cash_account is None:
                continue
            net = major_units(txn.get("net") or 0, currency)
            fee = major_units(txn.get("fee") or 0, currency)
            if not net and not fee:
                continue
            ref = txn.get("id") or ""
            if ref and ref in seen:
                continue

            meta = data.new_metadata(filepath, index)
            if ref:
                meta[self._meta_key] = ref

            postings = [
                data.Posting(cash_account, Amount(net, currency), None, None, None, None)
            ]
            if fee and self._fees_account:
                # ``fee_details`` of type "tax" (VAT Stripe charges on its own
                # fees) go to ``tax_account`` when configured; the rest is fees.
                tax = major_units(
                    sum(
                        d.get("amount") or 0
                        for d in txn.get("fee_details") or []
                        if d.get("type") == "tax"
                    ),
                    currency,
                )
                if not self._tax_account:
                    tax = Decimal(0)
                if fee - tax:
                    postings.append(
                        data.Posting(
                            self._fees_account, Amount(fee - tax, currency), None, None, None, None
                        )
                    )
                if tax:
                    postings.append(
                        data.Posting(
                            self._tax_account, Amount(tax, currency), None, None, None, None
                        )
                    )

            payee = _payee(txn)
            narration = (txn.get("description") or "").strip()
            flag = self._review_flag
            if txn.get("type") in FEE_TYPES and self._fees_account:
                # Monthly-billed fees: the counter leg is the fee net of the
                # split-out tax; the VAT conversion still needs review.
                postings.append(
                    data.Posting(
                        self._fees_account, Amount(-net - fee, currency), None, None, None, None
                    )
                )
            else:
                haystack = f"{payee}\n{narration}\n{txn.get('type') or ''}"
                for pattern, counter, rule_flag in self._rules:
                    if pattern.search(haystack):
                        postings.append(
                            data.Posting(
                                counter, Amount(-net - fee, currency), None, None, None, None
                            )
                        )
                        flag = rule_flag
                        break

            date = dt.datetime.fromtimestamp(
                txn.get("created") or 0, tz=dt.timezone.utc
            ).date()
            entries.append(
                data.Transaction(
                    meta, date, flag, payee, narration,
                    data.EMPTY_SET, data.EMPTY_SET, postings,
                )
            )

        entries.sort(key=lambda e: e.date)
        entries.extend(self._balance_assertions(document, filepath, len(entries)))
        return entries

    def _balance_assertions(self, document: dict, filepath: str, offset: int) -> data.Entries:
        snapshot = document.get("balance") or {}
        as_of = snapshot.get("as_of")
        if not as_of:
            return []
        date = dt.date.fromisoformat(as_of[:10]) + dt.timedelta(days=1)
        totals: dict[str, Decimal] = {}
        for bucket in ("available", "pending"):
            for part in snapshot.get(bucket) or []:
                currency = (part.get("currency") or "").upper()
                if currency in self._accounts:
                    totals[currency] = totals.get(currency, Decimal(0)) + major_units(
                        part.get("amount") or 0, currency
                    )
        return [
            data.Balance(
                data.new_metadata(filepath, offset + i),
                date,
                self._accounts[currency],
                Amount(total, currency),
                None,
                None,
            )
            for i, (currency, total) in enumerate(sorted(totals.items()))
        ]


def _existing_references(existing: data.Entries, meta_key: str) -> set[str]:
    refs: set[str] = set()
    for entry in existing or []:
        meta = getattr(entry, "meta", None) or {}
        value = meta.get(meta_key)
        if isinstance(value, str):
            refs.add(value)
        for posting in getattr(entry, "postings", ()) or ():
            value = (posting.meta or {}).get(meta_key)
            if isinstance(value, str):
                refs.add(value)
    return refs
