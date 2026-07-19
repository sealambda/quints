"""beangulp importer for Wise balance statements (``statement.json``).

One statement file covers **one currency balance**; the importer drafts one
beancount transaction per statement entry:

- ``referenceNumber`` (``CARD-…``, ``TRANSFER-…``, …) becomes metadata
  (``wise_id:`` by default) and is the idempotency key — ``extract()`` skips
  references already present anywhere in the existing ledger.
- Payee comes from the entry's ``details`` by type: merchant name for card
  payments, recipient/sender for transfers, the description otherwise.
- ``totalFees`` > 0 becomes an explicit posting to ``fees_account`` — Wise
  fees stay visible instead of disappearing into the counter leg.
- ``payee_rules`` (``(regex, account, flag)``) draft the counter leg; anything
  unmatched keeps the ``!`` review flag and the cash leg only.
- ``endOfStatementBalance`` becomes a ``balance`` assertion dated the day
  after the statement interval ends.

**Conversions** appear once per balance — two statement files, two entries,
one shared ``referenceNumber``. :func:`merge_conversions` joins them into a
single two-currency transaction (``@@`` price on the incoming leg, fee kept
explicit), which is where realized FX differences become visible.

The API client (SCA-capable) lives in :mod:`beangulp_wise.client`; the
importer itself only reads files, so fetching stays auditable and replayable.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Sequence
from decimal import Decimal
from typing import TypedDict, TypeGuard

import beangulp
from beancount.core import data, flags
from beancount.core.amount import Amount

from .client import ScaChallenge, WiseClient, WiseError, sign_sca_token  # re-export

__all__ = [
    "Importer",
    "ScaChallenge",
    "WiseClient",
    "WiseError",
    "merge_conversions",
    "sign_sca_token",
]


# ── statement shapes (the fields the importer reads; everything defensive) ───


class _Money(TypedDict):
    value: float | str
    currency: str


class _Fees(TypedDict, total=False):
    value: float | str | None
    currency: str | None


class _Merchant(TypedDict, total=False):
    name: str | None


class _Recipient(TypedDict, total=False):
    name: str | None


class _Details(TypedDict, total=False):
    type: str
    merchant: _Merchant | None
    recipient: _Recipient | None
    senderName: str | None
    description: str | None
    paymentReference: str | None


class _TxnOptional(TypedDict, total=False):
    referenceNumber: str | None
    details: _Details | None
    totalFees: _Fees | None


class _Txn(_TxnOptional):
    amount: _Money
    date: str


class _QueryOptional(TypedDict, total=False):
    intervalEnd: str | None


class _Query(_QueryOptional):
    currency: str


class _Holder(TypedDict, total=False):
    businessName: str | None
    fullName: str | None


class _StatementOptional(TypedDict, total=False):
    accountHolder: _Holder | None
    endOfStatementBalance: _Money | None


class _Statement(_StatementOptional):
    transactions: list[_Txn]
    query: _Query


def _is_statement(raw: object) -> TypeGuard[_Statement]:
    return isinstance(raw, dict) and "transactions" in raw


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _payee(details: _Details) -> str:
    kind = details.get("type", "")
    if kind == "CARD":
        merchant = details.get("merchant") or {}
        name = merchant.get("name")
        if name:
            return name
    if kind == "TRANSFER":
        recipient = details.get("recipient") or {}
        name = recipient.get("name") or details.get("senderName")
        if name:
            return name
    if kind in ("DEPOSIT", "MONEY_ADDED"):
        sender = details.get("senderName")
        if sender:
            return sender
    if kind == "CONVERSION":
        return "Wise"
    return (details.get("description") or "").strip()


class Importer(beangulp.Importer):
    """Importer for Wise balance-statement JSON files."""

    def __init__(
        self,
        account_map: dict[str, str],
        *,
        fees_account: str | None = None,
        holder: str | None = None,
        meta_key: str = "wise_id",
        payee_rules: Sequence[tuple[str, str, str]] = (),
        review_flag: str = flags.FLAG_WARNING,
    ):
        self._accounts = dict(account_map)
        self._fees_account = fees_account
        self._holder = holder
        self._meta_key = meta_key
        self._rules = [
            (re.compile(pattern, re.IGNORECASE), account, flag)
            for pattern, account, flag in payee_rules
        ]
        self._review_flag = review_flag

    # ── beangulp interface ───────────────────────────────────────────────────

    def _load(self, filepath: str) -> _Statement | None:
        try:
            with open(filepath, encoding="utf-8") as f:
                raw: object = json.load(f)
        except (OSError, ValueError):
            return None
        if not _is_statement(raw):
            return None
        query = raw.get("query")
        if not query or query.get("currency") not in self._accounts:
            return None
        if self._holder:
            holder = raw.get("accountHolder") or {}
            name = holder.get("businessName") or holder.get("fullName")
            if name != self._holder:
                return None
        return raw

    def identify(self, filepath: str) -> bool:
        return self._load(filepath) is not None

    def account(self, filepath: str) -> str:
        statement = self._load(filepath)
        return self._accounts[statement["query"]["currency"]] if statement else ""

    def date(self, filepath: str) -> dt.date | None:
        statement = self._load(filepath)
        end = statement["query"].get("intervalEnd") if statement else None
        if end:
            return dt.datetime.fromisoformat(end.replace("Z", "+00:00")).date()
        return None

    def filename(self, filepath: str) -> str:
        return "statement.json"

    def extract(self, filepath: str, existing: data.Entries) -> data.Entries:
        statement = self._load(filepath)
        if statement is None:
            return []
        currency = statement["query"]["currency"]
        cash_account = self._accounts[currency]
        seen = _existing_references(existing, self._meta_key)

        entries: data.Entries = []
        for index, txn in enumerate(statement["transactions"]):
            number = _decimal(txn["amount"]["value"])
            if not number:
                continue
            ref = txn.get("referenceNumber") or ""
            if ref and ref in seen:
                continue

            details = txn.get("details") or {}
            meta = data.new_metadata(filepath, index)
            if ref:
                meta[self._meta_key] = ref
            if details.get("type") == "CONVERSION":
                meta["conversion"] = "true"  # consumed by merge_conversions

            postings = [
                data.Posting(cash_account, Amount(number, currency), None, None, None, None)
            ]
            fee = _decimal((txn.get("totalFees") or {}).get("value") or 0)
            if fee and self._fees_account:
                fee_ccy = (txn.get("totalFees") or {}).get("currency") or currency
                postings.append(
                    data.Posting(self._fees_account, Amount(fee, fee_ccy), None, None, None, None)
                )

            payee = _payee(details)
            narration = (details.get("description") or "").strip()
            flag = self._review_flag
            if details.get("type") != "CONVERSION":
                haystack = f"{payee}\n{narration}\n{details.get('paymentReference') or ''}"
                for pattern, counter, rule_flag in self._rules:
                    if pattern.search(haystack):
                        postings.append(
                            data.Posting(
                                counter, Amount(-number - fee, currency), None, None, None, None
                            )
                        )
                        flag = rule_flag
                        break

            date = dt.datetime.fromisoformat(txn["date"].replace("Z", "+00:00")).date()
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
        closing = statement.get("endOfStatementBalance")
        end = statement["query"].get("intervalEnd")
        if closing and end:
            end_date = dt.datetime.fromisoformat(end.replace("Z", "+00:00")).date()
            entries.append(
                data.Balance(
                    data.new_metadata(filepath, len(entries)),
                    end_date + dt.timedelta(days=1),
                    cash_account,
                    Amount(_decimal(closing["value"]), closing["currency"]),
                    None,
                    None,
                )
            )
        return entries


def _cash_value(txn: data.Transaction) -> tuple[Decimal, str] | None:
    """Number and currency of the drafted cash leg (first posting), if priced."""
    if not txn.postings:
        return None
    units = txn.postings[0].units
    if units is None or units.number is None:
        return None
    return units.number, units.currency


def merge_conversions(entries: data.Entries, meta_key: str = "wise_id") -> data.Entries:
    """Join the two single-currency legs of each Wise conversion.

    Legs share a ``referenceNumber`` across two statement files. The merged
    transaction keeps both cash postings and any fee postings; the **incoming**
    leg gets an ``@@`` total price of what actually left the other balance net
    of fees, so the transaction balances exactly and the effective rate is the
    booked one.
    """
    by_ref: dict[str, list[tuple[int, data.Transaction]]] = {}
    for i, entry in enumerate(entries):
        if isinstance(entry, data.Transaction) and entry.meta.get("conversion"):
            ref = entry.meta.get(meta_key)
            if ref:
                by_ref.setdefault(ref, []).append((i, entry))

    merged: data.Entries = []
    drop: set[int] = set()
    for legs in by_ref.values():
        if len(legs) != 2:
            continue  # counterpart not in this batch; leave the leg for review
        (index_a, a), (index_b, b) = legs
        a_cash = _cash_value(a)
        b_cash = _cash_value(b)
        if a_cash is None or b_cash is None:
            continue  # a leg has no cash amount; leave both for review
        out_leg, (out_number, out_currency) = (a, a_cash) if a_cash[0] < 0 else (b, b_cash)
        in_leg = b if out_leg is a else a
        out_cash = out_leg.postings[0]
        in_cash = in_leg.postings[0]
        fees = list(out_leg.postings[1:] + in_leg.postings[1:])
        fee_total = sum(
            (
                units.number
                for p in fees
                if (units := p.units) is not None
                and units.number is not None
                and units.currency == out_currency
            ),
            Decimal("0"),
        )
        priced_in = data.Posting(
            in_cash.account,
            in_cash.units,
            None,
            Amount(-out_number - fee_total, out_currency),
            None,
            None,
        )
        meta = dict(out_leg.meta)
        meta.pop("conversion", None)
        merged.append(
            data.Transaction(
                meta,
                out_leg.date,
                "*",
                "Wise",
                out_leg.narration or in_leg.narration,
                data.EMPTY_SET,
                data.EMPTY_SET,
                [out_cash, priced_in, *fees],
            )
        )
        drop.update((index_a, index_b))

    result = [e for i, e in enumerate(entries) if i not in drop]
    result.extend(merged)
    result.sort(key=lambda e: (e.date, isinstance(e, data.Balance)))
    return result


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
