"""beangulp importer for Yapeal CSV bank statements.

Parses Yapeal CSV exports into beancount transaction drafts following the
``csvbase`` pattern (see :mod:`beangulp.importers.csvbase` and
``beangulp/examples/importers/csvbank.py``):

- Every draft carries the bank's transaction ID as metadata (``bank_ref:`` by
  default). ``extract()`` skips references already present anywhere in the
  existing ledger, so re-running an import is always safe.
- ``payee_rules`` — an iterable of ``(regex, account, flag)`` — draft the
  counter leg when the payee/narration matches; anything unmatched is emitted
  with the review flag and the cash leg only, for a human (or agent) to
  complete.

The importer is entity-agnostic: account names and rules are constructor
parameters.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from beancount.core import data, flags
from beancount.core.amount import Amount
from beangulp import mimetypes
from beangulp.importers import csvbase

__all__ = ["Importer"]

_IDENTIFY_HEADER = "Transaktions-ID,Belastung oder Gutschrift,Buchungs-Datum"


class _YapealAmount(csvbase.Column):
    """Amount from either Gutschrift (credit), Belastung (debit), or Betrag+indicator."""

    def parse(self, gutschrift: str, belastung: str, betrag: str, indicator: str) -> Decimal:  # type: ignore[override]
        if gutschrift:
            return Decimal(gutschrift)
        if belastung:
            return -Decimal(belastung)
        if betrag:
            amount = Decimal(betrag)
            if indicator == "DEBIT":
                amount = -amount
            return amount
        raise ValueError("no amount in row")


class Importer(csvbase.Importer):
    """Yapeal CSV statement importer for one bank account."""

    ref = csvbase.Column("Transaktions-ID")
    date = csvbase.Date("Transaktions-Datum", "%d.%m.%Y")  # type: ignore[assignment]
    payee = csvbase.Column("Gegenpartei")
    narration = csvbase.Column("Transaktions-Info")
    amount = _YapealAmount(
        "Gutschrift (CHF)", "Belastung (CHF)", "Betrag", "Belastung oder Gutschrift"
    )

    def __init__(
        self,
        account: str,
        *,
        iban: str | None = None,
        currency: str = "CHF",
        meta_key: str = "bank_ref",
        payee_rules: Sequence[tuple[str, str, str]] = (),
        review_flag: str = flags.FLAG_WARNING,
    ):
        super().__init__(account, currency, flag=review_flag)
        self._iban = re.sub(r"\s", "", iban).upper() if iban else None
        self._meta_key = meta_key
        self._rules = [
            (re.compile(pattern, re.IGNORECASE), acct, flag) for pattern, acct, flag in payee_rules
        ]

    def identify(self, filepath: str) -> bool:
        try:
            mimetype, _ = mimetypes.guess_type(filepath)
            if mimetype != "text/csv":
                return False
            with open(filepath, encoding="utf-8") as f:
                head = f.read(65536)
            if _IDENTIFY_HEADER not in head:
                return False
            if self._iban:
                return self._iban in re.sub(r"\s", "", head).upper()
            return True
        except (OSError, UnicodeError):
            return False

    def account(self, filepath: str) -> str:
        return self.importer_account

    def filename(self, filepath: str) -> str:
        return "statement.csv"

    def metadata(self, filepath: str, lineno: int, row: object) -> dict[str, object]:
        meta = data.new_metadata(filepath, lineno)
        ref = getattr(row, "ref", None)
        if ref:
            meta[self._meta_key] = ref
        return meta

    def extract(self, filepath: str, existing: data.Entries) -> data.Entries:
        seen = _existing_references(existing, self._meta_key)
        offset = int(self.skiplines) + int(self.names) + 1

        entries: data.Entries = []
        for lineno, row in enumerate(self.read(filepath), offset):
            if not row:
                continue

            ref = getattr(row, "ref", None)
            if not ref or ref in seen:
                continue

            try:
                amount_val = row.amount  # type: ignore[attr-defined]
            except (ValueError, InvalidOperation):
                continue

            meta = self.metadata(filepath, lineno, row)
            payee = row.payee  # type: ignore[attr-defined]
            narration = row.narration  # type: ignore[attr-defined]
            date_val = row.date  # type: ignore[attr-defined]
            units = Amount(amount_val, self.currency)
            postings = [data.Posting(self.importer_account, units, None, None, None, None)]

            flag = self.flag
            haystack = f"{payee or ''}\n{narration or ''}"
            for pattern, counter, rule_flag in self._rules:
                if pattern.search(haystack):
                    postings.append(
                        data.Posting(
                            counter, Amount(-amount_val, self.currency), None, None, None, None
                        )
                    )
                    flag = rule_flag
                    break

            entries.append(
                data.Transaction(
                    meta,
                    date_val,
                    flag,
                    payee,
                    narration,
                    data.EMPTY_SET,
                    data.EMPTY_SET,
                    postings,
                )
            )

        entries.sort(key=lambda e: e.date)
        return entries


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
