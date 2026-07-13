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
from decimal import Decimal
from typing import Sequence

import beangulp
from beancount.core import data, flags
from beancount.core.amount import Amount

from .client import ScaChallenge, WiseClient, WiseError, sign_sca_token  # re-export

__all__ = ["Importer", "merge_conversions", "WiseClient", "WiseError", "ScaChallenge", "sign_sca_token"]


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _payee(details: dict) -> str:
    kind = details.get("type", "")
    if kind == "CARD":
        merchant = details.get("merchant") or {}
        if merchant.get("name"):
            return merchant["name"]
    if kind == "TRANSFER":
        recipient = details.get("recipient") or {}
        name = recipient.get("name") or details.get("senderName")
        if name:
            return name
    if kind in ("DEPOSIT", "MONEY_ADDED") and details.get("senderName"):
        return details["senderName"]
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

    def _load(self, filepath: str) -> dict | None:
        try:
            with open(filepath, encoding="utf-8") as f:
                statement = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(statement, dict) or "transactions" not in statement:
            return None
        if (statement.get("query") or {}).get("currency") not in self._accounts:
            return None
        if self._holder:
            holder = statement.get("accountHolder") or {}
            name = holder.get("businessName") or holder.get("fullName")
            if name != self._holder:
                return None
        return statement

    def identify(self, filepath: str) -> bool:
        return self._load(filepath) is not None

    def account(self, filepath: str) -> str:
        statement = self._load(filepath)
        return self._accounts[statement["query"]["currency"]] if statement else ""

    def date(self, filepath: str):
        statement = self._load(filepath)
        if statement and (statement.get("query") or {}).get("intervalEnd"):
            return dt.datetime.fromisoformat(
                statement["query"]["intervalEnd"].replace("Z", "+00:00")
            ).date()
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
                    meta, date, flag, payee, narration,
                    data.EMPTY_SET, data.EMPTY_SET, postings,
                )
            )

        entries.sort(key=lambda e: e.date)
        closing = statement.get("endOfStatementBalance")
        end = (statement.get("query") or {}).get("intervalEnd")
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


def merge_conversions(entries: data.Entries, meta_key: str = "wise_id") -> data.Entries:
    """Join the two single-currency legs of each Wise conversion.

    Legs share a ``referenceNumber`` across two statement files. The merged
    transaction keeps both cash postings and any fee postings; the **incoming**
    leg gets an ``@@`` total price of what actually left the other balance net
    of fees, so the transaction balances exactly and the effective rate is the
    booked one.
    """
    by_ref: dict[str, list[int]] = {}
    for i, entry in enumerate(entries):
        if isinstance(entry, data.Transaction) and entry.meta.get("conversion"):
            ref = entry.meta.get(meta_key)
            if ref:
                by_ref.setdefault(ref, []).append(i)

    merged: data.Entries = []
    drop: set[int] = set()
    for ref, indexes in by_ref.items():
        if len(indexes) != 2:
            continue  # counterpart not in this batch; leave the leg for review
        a, b = (entries[i] for i in indexes)
        out_leg, in_leg = (a, b) if a.postings[0].units.number < 0 else (b, a)
        out_cash = out_leg.postings[0]
        fees = [p for p in out_leg.postings[1:] + in_leg.postings[1:]]
        fee_total = sum(
            (p.units.number for p in fees if p.units.currency == out_cash.units.currency),
            Decimal("0"),
        )
        in_cash = in_leg.postings[0]
        priced_in = data.Posting(
            in_cash.account,
            in_cash.units,
            None,
            Amount(-out_cash.units.number - fee_total, out_cash.units.currency),
            None,
            None,
        )
        meta = dict(out_leg.meta)
        meta.pop("conversion", None)
        merged.append(
            data.Transaction(
                meta, out_leg.date, "*", "Wise",
                out_leg.narration or in_leg.narration,
                data.EMPTY_SET, data.EMPTY_SET,
                [out_cash, priced_in, *fees],
            )
        )
        drop.update(indexes)

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
