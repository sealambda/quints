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

:func:`correlate_invoices` pairs charge balance transactions with the invoices
that document them, so a caller can file each invoice PDF against the entry it
belongs to. Stripe does not always link the two objects, so the pairing falls
back to a heuristic and reports ties rather than guessing.

The API client lives in :mod:`beangulp_stripe.client`; the importer itself
only reads files, so fetching stays auditable and replayable.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict, TypeGuard, cast

import beangulp
from beancount.core import data, flags
from beancount.core.amount import Amount

from .client import StripeClient, StripeError  # re-export

__all__ = [
    "CHARGE_TYPES",
    "CREATED_TOLERANCE_SECONDS",
    "FEE_TYPES",
    "Importer",
    "InvoiceMatch",
    "StripeClient",
    "StripeError",
    "correlate_invoices",
    "major_units",
]


class _FeeDetail(TypedDict, total=False):
    amount: int
    type: str


class _Billing(TypedDict, total=False):
    name: str | None
    email: str | None


class _Source(TypedDict, total=False):
    id: str
    payment_intent: str | None
    billing_details: _Billing


class _Txn(TypedDict, total=False):
    id: str
    object: str
    type: str
    currency: str
    created: int
    amount: int  # gross, minor units — what an invoice total is compared against
    net: int
    fee: int
    description: str | None
    source: _Source | str | None
    fee_details: list[_FeeDetail]


class _BalanceFunds(TypedDict, total=False):
    amount: int
    currency: str


class _BalanceSnapshot(TypedDict, total=False):
    as_of: str
    available: list[_BalanceFunds]
    pending: list[_BalanceFunds]


class _AccountInfo(TypedDict, total=False):
    id: str


class _Document(TypedDict, total=False):
    data: list[_Txn]
    balance: _BalanceSnapshot
    account: _AccountInfo


def _is_document(obj: object) -> TypeGuard[_Document]:
    if not isinstance(obj, dict):
        return False
    transactions = obj.get("data")
    if not isinstance(transactions, list):
        return False
    return all(
        isinstance(t, dict) and t.get("object", "balance_transaction") == "balance_transaction"
        for t in transactions
    )


# Currencies whose minor unit is not 1/100 (Stripe docs: currencies guide).
ZERO_DECIMAL = frozenset(
    [
        "bif",
        "clp",
        "djf",
        "gnf",
        "jpy",
        "kmf",
        "krw",
        "mga",
        "pyg",
        "rwf",
        "ugx",
        "vnd",
        "vuv",
        "xaf",
        "xof",
        "xpf",
    ]
)
THREE_DECIMAL = frozenset(["bhd", "jod", "kwd", "omr", "tnd"])

# Balance-transaction types whose counter leg IS the fee account.
FEE_TYPES = frozenset({"stripe_fee"})


def major_units(amount: int, currency: str) -> Decimal:
    """Stripe minor units → decimal amount (``14900, "eur"`` → ``149.00``)."""
    code = currency.lower()
    exponent = 0 if code in ZERO_DECIMAL else 3 if code in THREE_DECIMAL else 2
    return Decimal(amount).scaleb(-exponent)


def _payee(txn: _Txn) -> str:
    if txn.get("type") in FEE_TYPES:
        return "Stripe"
    source = txn.get("source")
    if isinstance(source, dict):
        billing: _Billing = source.get("billing_details") or {}
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

    def _load(self, filepath: str) -> _Document | None:
        try:
            with open(filepath, encoding="utf-8") as f:
                document = json.load(f)
        except (OSError, ValueError):
            return None
        if isinstance(document, list):
            document = {"data": document}
        if not _is_document(document):
            return None
        transactions = document.get("data") or []
        currencies = {(t.get("currency") or "").upper() for t in transactions}
        snapshot: _BalanceSnapshot = document.get("balance") or {}
        for parts in (snapshot.get("available"), snapshot.get("pending")):
            currencies |= {(p.get("currency") or "").upper() for p in parts or []}
        if not currencies & self._accounts.keys():
            return None
        if self._account_id:
            account: _AccountInfo = document.get("account") or {}
            held = account.get("id")
            if held and held != self._account_id:
                return None
        return document

    def identify(self, filepath: str) -> bool:
        return self._load(filepath) is not None

    def account(self, filepath: str) -> str:
        document = self._load(filepath)
        if not document:
            return ""
        for txn in document.get("data") or []:
            mapped = self._accounts.get((txn.get("currency") or "").upper())
            if mapped:
                return mapped
        return next(iter(self._accounts.values()), "")

    def date(self, filepath: str) -> dt.date | None:
        document = self._load(filepath)
        transactions = document.get("data") if document else None
        if transactions:
            newest = max(t.get("created") or 0 for t in transactions)
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
        for index, txn in enumerate(document.get("data") or []):
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

            postings = [data.Posting(cash_account, Amount(net, currency), None, None, None, None)]
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
                if tax and self._tax_account:
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

            date = dt.datetime.fromtimestamp(txn.get("created") or 0, tz=dt.timezone.utc).date()
            entries.append(
                data.Transaction(
                    meta,
                    date,
                    flag,
                    payee,
                    narration,
                    data.EMPTY_SET,
                    data.EMPTY_SET,
                    postings,
                )
            )

        entries.sort(key=lambda e: e.date)
        entries.extend(self._balance_assertions(document, filepath, len(entries)))
        return entries

    def _balance_assertions(self, document: _Document, filepath: str, offset: int) -> data.Entries:
        snapshot: _BalanceSnapshot = document.get("balance") or {}
        as_of = snapshot.get("as_of")
        if not as_of:
            return []
        date = dt.date.fromisoformat(as_of[:10]) + dt.timedelta(days=1)
        totals: dict[str, Decimal] = {}
        for parts in (snapshot.get("available"), snapshot.get("pending")):
            for part in parts or []:
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


# ── invoice ↔ balance-transaction correlation ────────────────────────────────

# Balance-transaction types that can carry a customer invoice. Payouts,
# refunds and monthly ``stripe_fee`` debits never do.
CHARGE_TYPES = frozenset({"charge", "payment"})

# Stripe stamps an invoice and the balance transaction that settles it within
# seconds of each other. When no id links the two objects that gap is the only
# temporal signal, so the window stays tight.
CREATED_TOLERANCE_SECONDS = 120


class _Invoice(TypedDict, total=False):
    id: str
    number: str | None
    status: str | None
    created: int
    total: int
    currency: str
    customer_name: str | None
    customer_email: str | None
    invoice_pdf: str | None
    charge: str | None
    payment_intent: str | None
    payments: object  # a list, or a {"object": "list", "data": [...]} wrapper


@dataclass(frozen=True)
class InvoiceMatch:
    """A charge balance transaction paired with the invoice documenting it.

    Only the fields a caller needs to file the document: the raw invoice stays
    inside this module, so the pairing contract is explicit.
    """

    txn_id: str
    invoice_id: str
    number: str  # the invoice number, else the invoice id
    customer: str  # customer_name, else customer_email, else ""
    created: int  # invoice creation timestamp (unix seconds)
    pdf_url: str  # ``invoice_pdf`` — short-lived, download it now
    matched_by: str  # "id" (authoritative) | "amount+time" (heuristic)


def _number(invoice: _Invoice) -> str:
    return invoice.get("number") or invoice.get("id") or "?"


def _numbers(invoices: Iterable[_Invoice]) -> str:
    return ", ".join(_number(i) for i in invoices)


def _payment_ids(invoice: _Invoice) -> set[str]:
    """Charge / payment-intent ids the invoice itself names, if any.

    Newer API versions dropped the legacy ``charge`` and ``payment_intent``
    fields in favour of a ``payments`` list, so both shapes are read. When
    either yields an id the pairing is authoritative and needs no heuristic.
    """
    ids: set[str] = set()
    for value in (invoice.get("charge"), invoice.get("payment_intent")):
        if isinstance(value, str):
            ids.add(value)
    payments = invoice.get("payments")
    if isinstance(payments, dict):
        payments = payments.get("data")
    if isinstance(payments, list):
        for payment in payments:
            if not isinstance(payment, dict):
                continue
            reference = payment.get("payment")
            if not isinstance(reference, dict):
                continue
            for value in (reference.get("charge"), reference.get("payment_intent")):
                if isinstance(value, str):
                    ids.add(value)
    return ids


def _source_ids(txn: _Txn) -> set[str]:
    """Charge / payment-intent ids the balance transaction names."""
    ids: set[str] = set()
    source = txn.get("source")
    if isinstance(source, str):
        ids.add(source)
    elif isinstance(source, dict):
        for value in (source.get("id"), source.get("payment_intent")):
            if isinstance(value, str):
                ids.add(value)
    return ids


def _compatible(txn: _Txn, invoice: _Invoice) -> bool:
    """The heuristic rule: same gross amount, same currency, same moment."""
    amount = txn.get("amount") or 0
    if not amount or amount != (invoice.get("total") or 0):
        return False
    if (txn.get("currency") or "").lower() != (invoice.get("currency") or "").lower():
        return False
    created = txn.get("created") or 0
    return abs(created - (invoice.get("created") or 0)) <= CREATED_TOLERANCE_SECONDS


def _match(txn_id: str, invoice: _Invoice, matched_by: str) -> InvoiceMatch:
    return InvoiceMatch(
        txn_id=txn_id,
        invoice_id=invoice.get("id") or "",
        number=_number(invoice),
        customer=invoice.get("customer_name") or invoice.get("customer_email") or "",
        created=invoice.get("created") or 0,
        pdf_url=invoice.get("invoice_pdf") or "",
        matched_by=matched_by,
    )


def correlate_invoices(
    transactions: Sequence[dict[str, object]], invoices: Sequence[dict[str, object]]
) -> tuple[list[InvoiceMatch], list[str]]:
    """Pair charge balance transactions with the invoices documenting them.

    Returns ``(matches, ambiguities)``. An id named by both objects wins
    outright. Failing that the pair must agree on currency and **gross** amount
    and have been created within :data:`CREATED_TOLERANCE_SECONDS` of each
    other — and the agreement must be mutual: one invoice for the transaction
    *and* one transaction for the invoice.

    A transaction with no candidate is neither an error nor reported — payouts,
    refunds and monthly fee debits have no customer invoice. Only a tie lands
    in ``ambiguities``, and the caller is expected to stop there rather than
    file a PDF against the wrong entry.

    Invoices without an ``invoice_pdf`` (drafts) are ignored: nothing could be
    filed for them, and counting them would only manufacture ties.
    """
    charges = [
        t
        for t in cast("Sequence[_Txn]", transactions)
        if t.get("type") in CHARGE_TYPES and isinstance(t.get("id"), str)
    ]
    downloadable = [i for i in cast("Sequence[_Invoice]", invoices) if i.get("invoice_pdf")]
    matches: list[InvoiceMatch] = []
    ambiguities: list[str] = []

    # Pass 1 — an id shared by both objects is authoritative.
    named = [(invoice, _payment_ids(invoice)) for invoice in downloadable]
    paired: set[int] = set()
    unpaired: list[_Txn] = []
    for txn in charges:
        txn_id = txn.get("id") or ""
        source_ids = _source_ids(txn)
        hits = [index for index, (_, ids) in enumerate(named) if ids and ids & source_ids]
        if len(hits) == 1:
            matches.append(_match(txn_id, named[hits[0]][0], "id"))
            paired.add(hits[0])
        elif hits:
            ambiguities.append(
                f"{txn_id} shares an id with {len(hits)} invoices "
                f"({_numbers(named[i][0] for i in hits)})"
            )
        else:
            unpaired.append(txn)

    # Pass 2 — the heuristic, over invoices that name no id at all. One that
    # does and matched nothing above settles a charge outside this window.
    pool = [
        invoice for index, (invoice, ids) in enumerate(named) if not ids and index not in paired
    ]
    for txn in unpaired:
        txn_id = txn.get("id") or ""
        candidates = [invoice for invoice in pool if _compatible(txn, invoice)]
        if not candidates:
            continue
        if len(candidates) > 1:
            ambiguities.append(
                f"{txn_id} matches {len(candidates)} invoices on amount and time "
                f"({_numbers(candidates)}) — no id links them"
            )
            continue
        invoice = candidates[0]
        back = [t for t in unpaired if _compatible(t, invoice)]
        if len(back) > 1:
            ambiguities.append(
                f"invoice {_number(invoice)} matches {len(back)} balance transactions "
                f"({', '.join(str(t.get('id')) for t in back)}) — no id links them"
            )
            continue
        matches.append(_match(txn_id, invoice, "amount+time"))
    return matches, list(dict.fromkeys(ambiguities))
