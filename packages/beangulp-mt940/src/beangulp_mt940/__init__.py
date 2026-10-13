"""beangulp importer for SWIFT MT940 bank statements.

Parses MT940 (via the ``mt-940`` library) into beancount transaction drafts:

- Every draft carries the bank's unique entry reference (``:61:`` subfield
  after ``//``) as metadata (``mt940_ref:`` by default). ``extract()`` skips
  references already present anywhere in the existing ledger, so re-running an
  import is safe. A row with no reference (empty, or SWIFT's ``NONREF``
  filler) gets no key and is never deduplicated: re-importing it drafts it
  again, which the closing ``balance`` assertion then flags.
- A file holding statements for more than one account (``:25:``) is refused:
  split it per account.
- Reversals are signed as what they are: ``RC`` (reversal of credit) is a
  debit, ``RD`` (reversal of debit) a credit.
- The statement's closing balance (``:62F:``) becomes a ``balance`` assertion
  dated the day after (beancount balance semantics are beginning-of-day).
- Zero-amount entries (e.g. UBS "Balance closing of service prices") are noise
  and skipped.
- ``payee_rules`` — an iterable of ``(regex, account, flag)`` — draft the
  counter leg when the payee/details match; anything unmatched is emitted with
  the review flag and the cash leg only, for a human (or agent) to complete.

The importer is entity-agnostic: account names, rules, and the IBAN filter are
constructor parameters.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

import beangulp
import mt940
from beancount.core import data, flags
from beancount.core.amount import Amount

__all__ = ["Importer"]

_TAGS = re.compile(r":(20|25|28C?|60F|61|62F):")
_ACCOUNT = re.compile(r"^:25:([^\r\n]+)", re.MULTILINE)
_NO_REFERENCE = "NONREF"  # SWIFT's filler for "no reference" — shared, not an id


def _accounts(text: str) -> set[str]:
    """Distinct account identifications (``:25:``), whitespace-insensitive."""
    return {re.sub(r"\s", "", m).upper() for m in _ACCOUNT.findall(text)}


def _reference(d: dict[str, object]) -> str:
    """The row's own reference, or "" when it has none worth deduplicating on."""
    for key in ("bank_reference", "customer_reference"):
        value = d.get(key)
        ref = value.strip() if isinstance(value, str) else ""
        if ref and ref.upper() != _NO_REFERENCE:
            return ref
    return ""


def _payee(details: str) -> str:
    """Payee from an MT940 ``:86:`` details blob.

    UBS formats details as ``<code>?<counterparty>\\n<more>`` — take the first
    line after the first ``?``; without a ``?``, the first line as-is.
    """
    first = details.split("?", 1)[-1] if "?" in details else details
    return first.splitlines()[0].strip() if first else ""


class Importer(beangulp.Importer):
    """MT940 statement importer for one bank account."""

    def __init__(
        self,
        account: str,
        *,
        iban: str | None = None,
        currency: str | None = None,
        meta_key: str = "mt940_ref",
        payee_rules: Sequence[tuple[str, str, str]] = (),
        review_flag: str = flags.FLAG_WARNING,
    ):
        self._account = account
        self._iban = re.sub(r"\s", "", iban).upper() if iban else None
        self._currency = currency
        self._meta_key = meta_key
        self._rules = [
            (re.compile(pattern, re.IGNORECASE), acct, flag) for pattern, acct, flag in payee_rules
        ]
        self._review_flag = review_flag

    # ── beangulp interface ───────────────────────────────────────────────────

    def identify(self, filepath: str) -> bool:
        try:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                head = f.read(65536)
        except (OSError, UnicodeError):
            return False
        if len(_TAGS.findall(head)) < 3 or ":61:" not in head:
            return False
        if self._iban:
            # Any statement in the file: a multi-account file that holds ours
            # is claimed here and then refused loudly by ``_parse``.
            return any(self._iban in account for account in _accounts(head))
        return True

    def account(self, filepath: str) -> str:
        return self._account

    def date(self, filepath: str):
        statements = _parse(filepath)
        closing = statements.data.get("final_closing_balance")
        return closing.date if closing else None

    def filename(self, filepath: str) -> str:
        return "statement.mt940"

    def extract(self, filepath: str, existing: data.Entries) -> data.Entries:
        statements = _parse(filepath)
        seen = _existing_references(existing, self._meta_key)

        entries: data.Entries = []
        for index, txn in enumerate(statements):
            d = txn.data
            number = Decimal(str(d["amount"].amount))
            if d.get("status") == "RC":
                # The library negates only "D"; a reversal of credit is a debit.
                # "RD" (reversal of debit) is a credit and already positive.
                number = -number
            if not number:
                continue  # zero-amount service/fee closings are noise
            ref = _reference(d)
            if ref and ref in seen:
                continue

            payee = _payee(d.get("transaction_details") or "")
            narration = (d.get("extra_details") or "").strip()
            meta = data.new_metadata(filepath, index)
            if ref:
                meta[self._meta_key] = ref

            currency = d.get("currency") or self._currency
            if not currency:
                raise ValueError(
                    f"{filepath}: statement has no balance tag to take the currency "
                    "from; pass currency= to the importer"
                )
            postings = [
                data.Posting(self._account, Amount(number, currency), None, None, None, None)
            ]
            flag = self._review_flag
            haystack = f"{payee}\n{d.get('transaction_details') or ''}"
            for pattern, counter, rule_flag in self._rules:
                if pattern.search(haystack):
                    postings.append(
                        data.Posting(counter, Amount(-number, currency), None, None, None, None)
                    )
                    flag = rule_flag
                    break

            entries.append(
                data.Transaction(
                    meta,
                    d["date"],
                    flag,
                    payee,
                    narration,
                    data.EMPTY_SET,
                    data.EMPTY_SET,
                    postings,
                )
            )

        entries.sort(key=lambda e: e.date)
        closing = statements.data.get("final_closing_balance")
        if closing is not None:
            entries.append(
                data.Balance(
                    data.new_metadata(filepath, len(entries)),
                    closing.date + timedelta(days=1),
                    self._account,
                    Amount(Decimal(str(closing.amount.amount)), closing.amount.currency),
                    None,
                    None,
                )
            )
        return entries


def _parse(filepath: str) -> mt940.models.Transactions:
    """Parse the file, refusing one that mixes accounts.

    ``mt940.parse`` merges every statement in a file into one: rows of a
    second account would be booked onto ours and only the last closing
    balance survives.
    """
    with open(filepath, encoding="utf-8", errors="replace") as f:
        accounts = _accounts(f.read())
    if len(accounts) > 1:
        raise ValueError(
            f"{filepath}: statements for {len(accounts)} accounts "
            f"({', '.join(sorted(accounts))}); export one file per account"
        )
    return mt940.parse(filepath)


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
