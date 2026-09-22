"""Explainable matching across the review queue (docs/plans/06 step 3).

Five deterministic match kinds, each with a human-readable reason list:

- ``payment→invoice`` — incoming staging drafts vs. open receivables, by
  QRR/SCOR/plain-number reference (exact, score 1.0) or payee similarity +
  amount equality.
- ``payment→payable`` — outgoing staging drafts vs. open supplier bills, by
  the supplier's own reference (exact, score 1.0) or payee similarity +
  amount equality inside a window after the bill date. An amount that fits
  several open bills identifies none of them, exactly as a colliding
  reference doesn't: it is reported, and it never scores 1.0.
- ``draft→inbox`` — outgoing staging drafts vs. inbox documents, by payee
  similarity + date proximity from the filename convention.
- ``inbox→booked`` — inbox documents vs. already-booked transactions that
  lack a ``document:`` link (evidence arriving after booking). Requires a
  date hint; payee-only matching is too noisy against recurring suppliers.
- ``inbox→payable`` — the same, for a booked supplier bill: the evidence a
  payables line is missing is the bill itself.

No AI here: scores are reproducible and auditable. The judgment layer
decides what to do with sub-1.0 candidates.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

from beancount.core import data
from beancount.core.amount import Amount
from beancount.parser import parser as raw_parser
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, ledger, payables, receivables, ui
from . import inbox as inbox_mod
from .invoice import reference as ref_mod

THRESHOLD = 0.5
_TOL = Decimal("0.005")
# How long after a supplier bill a bank payment can still be its payment.
# Wide enough for a bill paid late, narrow enough that last year's bill from
# the same supplier isn't offered as a candidate for this year's payment.
PAYMENT_WINDOW_DAYS = 180


@dataclass
class Match:
    kind: str  # payment→invoice | payment→payable | draft→inbox
    #            | inbox→booked | inbox→payable
    score: float
    source: dict[str, str | None]
    target: dict[str, str | None]
    reasons: list[str]


@dataclass(frozen=True)
class ReferenceIndex:
    """Every reference form an incoming payment may carry → invoice number.

    A key two open invoices share identifies neither of them, so it is held
    apart in `ambiguous` instead of silently resolving to whichever invoice
    was indexed last. That is not hypothetical: the legacy QR reference kept
    only an invoice number's digits, so `ACAD202608` and `ACAD202608B` map
    onto the same one. Such a payment is reported as unmatched with the
    reason, and a human picks the invoice."""

    by_key: dict[str, str]
    ambiguous: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ReferenceHit:
    """What a payment's text pointed at. `number` is None when it pointed at
    an ambiguous reference — found, but not identifying."""

    number: str | None
    reason: str


def reference_index(open_invoices: list[receivables.OpenInvoice]) -> ReferenceIndex:
    """Index the open invoices by every reference form a payment may quote."""
    return numbers_index(inv.number for inv in open_invoices)


def bill_reference_index(open_bills: Sequence[payables.OpenBill]) -> ReferenceIndex:
    """The same index over open *supplier* bills.

    A creditor reference (SCOR, ``RF…``) spells the issuer's own invoice
    number out, so a payment quoting the supplier's reference identifies the
    bill exactly — the receivables lookup, pointed the other way. Bills held
    together by the (payee, amount) fallback are left out: that key is ours,
    not something a payment could ever quote."""
    return numbers_index(b.number for b in open_bills if b.keyed_by != payables.KEY_FALLBACK)


def numbers_index(numbers: Iterable[str]) -> ReferenceIndex:
    """Index document numbers by every reference form a payment may quote."""
    keys: dict[str, set[str]] = {}
    for number in numbers:
        candidates = [number.upper(), ref_mod.compact_number(number)]
        for make in (ref_mod.make_scor, ref_mod.make_qrr, ref_mod.legacy_qrr):
            try:
                candidates.append(make(number))
            except ValueError:
                continue  # a number this scheme cannot carry — the others still can
        for key in candidates:
            if key:
                keys.setdefault(key, set()).add(number)
    return ReferenceIndex(
        by_key={k: next(iter(v)) for k, v in keys.items() if len(v) == 1},
        ambiguous={k: tuple(sorted(v)) for k, v in keys.items() if len(v) > 1},
    )


def payment_text(t: data.Transaction) -> list[str]:
    """The strings a payment can carry a reference in (not beancount's own
    `filename`/`lineno`, which are bookkeeping, not payment details)."""
    meta = {k: v for k, v in (t.meta or {}).items() if k not in ("filename", "lineno")}
    return [t.payee or "", t.narration or "", *(str(v) for v in meta.values())]


def find_invoice(index: ReferenceIndex, *texts: str | None) -> ReferenceHit | None:
    """The invoice a payment identifies — by exact reference, never substring.

    Structured references (SCOR `RF…`, 27-digit QRR) are read first, spacing
    and check digits verified; a QR reference is also decoded, so one minted
    with a bank identification quints never saw still resolves. Plain invoice
    numbers are then matched as whole tokens: a substring test credits a
    payment for `ACAD202608B` to `ACAD202608`, and a payment carrying the
    correct `RF80 ACAD 2026 08B` to both."""
    text = " ".join(t for t in texts if t)
    ambiguous: list[str] = []
    for ref in ref_mod.references_in(text):
        for key in (ref, ref_mod.decode_qrr(ref)):
            if not key:
                continue
            number = index.by_key.get(key)
            if number:
                return ReferenceHit(number, "invoice reference in payment details")
            if key in index.ambiguous:
                # A reference that fits two invoices is not decoded further:
                # whatever it decodes to would be a guess dressed as a match.
                ambiguous.append(key)
                break
    for token in ref_mod.numbers_in(text):
        number = index.by_key.get(token)
        if number:
            return ReferenceHit(number, "invoice number in payment details")
        if token in index.ambiguous:
            ambiguous.append(token)
    if ambiguous:
        key = ambiguous[0]
        return ReferenceHit(
            None,
            f"reference {key} fits {len(index.ambiguous[key])} open invoices "
            f"({', '.join(index.ambiguous[key])}) — cannot tell them apart",
        )
    return None


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def similarity(a: str | None, b: str | None) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    if ta <= tb or tb <= ta:  # one name contained in the other
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def date_score(a: Date, b: Date, window: int) -> float:
    return max(0.0, 1.0 - abs((a - b).days) / window)


def load_staging(staging_dir: Path) -> list[tuple[str, data.Transaction]]:
    """Raw-parse staging drafts (no booking — accounts aren't opened there)."""
    out = []
    for f in sorted(staging_dir.glob("*.bean")):
        entries, _errors, _ = raw_parser.parse_file(str(f))
        out += [(f.name, e) for e in entries if isinstance(e, data.Transaction)]
    return out


def _txn_dict(staging_file: str | None, t: data.Transaction) -> dict[str, str | None]:
    units = t.postings[0].units
    return {
        "staging_file": staging_file,
        "date": str(t.date),
        "payee": t.payee,
        "narration": t.narration,
        "amount": str(units.number)
        if isinstance(units, Amount) and units.number is not None
        else None,
        "currency": units.currency if isinstance(units, Amount) else None,
    }


def _bill_dict(b: payables.OpenBill) -> dict[str, str | None]:
    return {
        "bill": b.number,
        "keyed_by": b.keyed_by,
        "payee": b.payee,
        "date": str(b.bill_date),
        "due": str(b.due_date),
        "open": str(b.open_amount),
        "currency": b.currency,
    }


def payable_matches(
    t: data.Transaction,
    src: dict[str, str | None],
    open_bills: Sequence[payables.OpenBill],
    index: ReferenceIndex,
) -> list[Match]:
    """Score one outgoing draft against the open supplier bills.

    The supplier's reference decides it outright when the payment carries
    one. Otherwise the evidence is the amount, the payee and the timing —
    and the amount only counts when it *identifies*: a payment that fits two
    open bills of the same size is reported against both, with the reason,
    and neither reaches 1.0."""
    units = t.postings[0].units
    if not isinstance(units, Amount) or units.number is None:
        return []
    paid = -units.number  # positive: what left the account
    by_number = {b.number: b for b in open_bills}
    hit = find_invoice(index, *payment_text(t))
    if hit and hit.number and hit.number in by_number:
        return [Match("payment→payable", 1.0, src, _bill_dict(by_number[hit.number]), [hit.reason])]
    found = [hit.reason] if hit else []

    candidates = [b for b in open_bills if 0 <= (t.date - b.bill_date).days <= PAYMENT_WINDOW_DAYS]
    exact = [
        b for b in candidates if b.currency == units.currency and abs(b.open_amount - paid) <= _TOL
    ]
    exact_keys = {(b.number, b.currency) for b in exact}
    out: list[Match] = []
    for b in candidates:
        psim = similarity(t.payee, b.payee)
        is_exact = (b.number, b.currency) in exact_keys
        identifying = is_exact and len(exact) == 1
        score = round(0.6 * psim + 0.4 * identifying, 2)
        if score < THRESHOLD:
            continue
        if identifying:
            amount = f"amount equals open {b.open_amount} {b.currency}"
        elif is_exact:
            amount = (
                f"amount {paid} {units.currency} fits {len(exact)} open bills "
                f"({', '.join(sorted(x.number for x in exact))}) — cannot tell them apart"
            )
        else:
            amount = f"amount differs from open {b.open_amount} {b.currency}"
        out.append(
            Match(
                "payment→payable",
                score,
                src,
                _bill_dict(b),
                [
                    *found,
                    f"payee ≈ {psim:.2f}",
                    amount,
                    f"paid {(t.date - b.bill_date).days} d after the bill ({b.bill_date})",
                ],
            )
        )
    return out


def compute(
    ledger_path: Path,
    staging_dir: Path | None = None,
    today: Date | None = None,
    cfg: config.Config | None = None,
) -> list[Match]:
    cfg = cfg or config.get()
    today = today or datetime.now(timezone.utc).date()
    root = ledger_path.resolve().parent
    staging_dir = staging_dir or root / "staging"

    entries, _ = ledger.load_entries(ledger_path)
    opens = receivables.compute_from_entries(entries, today, cfg)
    bills = payables.compute_from_entries(entries, today, cfg)
    docs = [d for d in inbox_mod.scan(root, entries) if not d.duplicate_of and not d.linked]
    drafts = load_staging(staging_dir) if staging_dir.is_dir() else []

    matches: list[Match] = []
    ref_idx = reference_index(opens)
    bill_idx = bill_reference_index(bills)
    by_number = {o.number: o for o in opens}

    def inv_dict(o: receivables.OpenInvoice) -> dict[str, str | None]:
        return {
            "invoice": o.number,
            "payee": o.payee,
            "date": str(o.invoice_date),
            "open": str(o.open_amount),
            "currency": o.currency,
        }

    for fname, t in drafts:
        units = t.postings[0].units
        if not isinstance(units, Amount) or units.number is None:
            continue  # draft's first posting has no explicit amount — nothing to score
        src = _txn_dict(fname, t)

        if units.number > 0:  # incoming → open invoice
            hit = find_invoice(ref_idx, *payment_text(t))
            if hit and hit.number:
                matches.append(
                    Match(
                        "payment→invoice", 1.0, src, inv_dict(by_number[hit.number]), [hit.reason]
                    )
                )
                continue
            # An ambiguous reference is worth saying out loud: it rides along
            # with every payee/amount candidate below, so the table explains
            # why a payment that *does* quote a reference is still scored.
            found = [hit.reason] if hit else []
            for o in opens:
                psim = similarity(t.payee, o.payee)
                exact = o.currency == units.currency and abs(o.open_amount - units.number) <= _TOL
                score = round(0.6 * psim + 0.4 * exact, 2)
                if score >= THRESHOLD:
                    matches.append(
                        Match(
                            "payment→invoice",
                            score,
                            src,
                            inv_dict(o),
                            [
                                *found,
                                f"payee ≈ {psim:.2f}",
                                f"amount {'equals' if exact else 'differs from'} open "
                                f"{o.open_amount} {o.currency}",
                            ],
                        )
                    )
        else:  # outgoing → an open supplier bill, an inbox document, or both
            matches += payable_matches(t, src, bills, bill_idx)
            for d in docs:
                psim = similarity(t.payee, d.payee_hint or d.name)
                if d.date_hint:
                    dsc = date_score(t.date, Date.fromisoformat(d.date_hint), 14)
                    score = round(0.7 * psim + 0.3 * dsc, 2)
                    reasons = [f"payee ≈ {psim:.2f}", f"dated {d.date_hint} vs paid {t.date}"]
                else:
                    score = round(0.8 * psim, 2)
                    reasons = [f"payee ≈ {psim:.2f}", "no date in filename"]
                if score >= THRESHOLD:
                    matches.append(Match("draft→inbox", score, src, {"document": d.name}, reasons))

    # inbox document → booked transaction still missing its document link
    dated_docs = [(d, Date.fromisoformat(d.date_hint)) for d in docs if d.date_hint]
    if dated_docs:
        for e in entries:
            if not isinstance(e, data.Transaction):
                continue
            if any(k.startswith("document") for k in (e.meta or {})):
                continue
            # A booked supplier bill is reported as its own kind: what a
            # payables line is missing is the bill document itself.
            bill_key = payables.booked_bill(e, cfg)
            if bill_key is None and not any(
                p.account.startswith(("Expenses:", "Income:")) for p in e.postings
            ):
                continue
            for d, d_date in dated_docs:
                dsc = date_score(e.date, d_date, 7)
                if dsc == 0.0:
                    continue
                psim = similarity(e.payee, d.payee_hint or d.name)
                score = round(0.7 * psim + 0.3 * dsc, 2)
                if score >= THRESHOLD:
                    target = _txn_dict(None, e)
                    if bill_key is not None:
                        target["bill"] = bill_key
                    matches.append(
                        Match(
                            "inbox→payable" if bill_key is not None else "inbox→booked",
                            score,
                            {"document": d.name},
                            target,
                            [f"payee ≈ {psim:.2f}", f"dated {d.date_hint} vs booked {e.date}"],
                        )
                    )

    matches.sort(key=lambda m: -m.score)
    return matches


# ── render ────────────────────────────────────────────────────────────────────


def render(matches: list[Match], console: Console | None = None) -> None:
    console = console or ui.console
    console.print()
    console.rule("[bold]Match candidates[/]")
    if not matches:
        console.print("[muted]Nothing to match — no staging drafts or inbox documents pending.[/]")
        console.print()
        return
    t = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    t.add_column("Score", justify="right", no_wrap=True)
    t.add_column("Kind", no_wrap=True)
    t.add_column("Source")
    t.add_column("Target")
    t.add_column("Why")
    for m in matches:
        style = "ok" if m.score >= 0.9 else ("warn" if m.score >= 0.7 else "muted")
        src = m.source.get("document") or (
            f"{m.source['date']} {m.source['payee'] or '?'} "
            f"{m.source['amount']} {m.source['currency']}"
        )
        tgt = (
            m.target.get("invoice")
            or m.target.get("bill")
            or m.target.get("document")
            or (
                f"{m.target['date']} {m.target['payee'] or '?'} "
                f"{m.target['amount']} {m.target['currency']}"
            )
        )
        t.add_row(f"[{style}]{m.score:.2f}[/]", m.kind, src, tgt, "; ".join(m.reasons))
    console.print(t)
    console.print()
