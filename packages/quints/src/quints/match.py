"""Explainable matching across the review queue (docs/plans/06 step 3).

Three deterministic match kinds, each with a human-readable reason list:

- ``payment→invoice`` — incoming staging drafts vs. open receivables, by
  QRR/SCOR/plain-number reference (exact, score 1.0) or payee similarity +
  amount equality.
- ``draft→inbox`` — outgoing staging drafts vs. inbox documents, by payee
  similarity + date proximity from the filename convention.
- ``inbox→booked`` — inbox documents vs. already-booked transactions that
  lack a ``document:`` link (evidence arriving after booking). Requires a
  date hint; payee-only matching is too noisy against recurring suppliers.

No AI here: scores are reproducible and auditable. The judgment layer
decides what to do with sub-1.0 candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

from beancount.core import data
from beancount.parser import parser as raw_parser
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, ledger, receivables, ui
from . import inbox as inbox_mod
from .invoice.model import make_qrr, make_scor

THRESHOLD = 0.5
_TOL = Decimal("0.005")


@dataclass
class Match:
    kind: str  # payment→invoice | draft→inbox | inbox→booked
    score: float
    source: dict
    target: dict
    reasons: list


def reference_index(open_invoices) -> dict[str, str]:
    """Every reference form an incoming payment may carry → invoice number."""
    idx: dict[str, str] = {}
    for inv in open_invoices:
        idx[inv.number.upper()] = inv.number
        idx[make_qrr(inv.number)] = inv.number  # QRR — QR-IBAN payments
        idx[make_scor(inv.number)] = inv.number  # SCOR/RF — SEPA transfers
    return idx


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


def _txn_dict(staging_file: str | None, t: data.Transaction) -> dict:
    units = t.postings[0].units
    return {
        "staging_file": staging_file,
        "date": str(t.date),
        "payee": t.payee,
        "narration": t.narration,
        "amount": str(units.number),
        "currency": units.currency,
    }


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
    docs = [d for d in inbox_mod.scan(root, entries) if not d.duplicate_of and not d.linked]
    drafts = load_staging(staging_dir) if staging_dir.is_dir() else []

    matches: list[Match] = []
    ref_idx = reference_index(opens)
    by_number = {o.number: o for o in opens}

    def inv_dict(o) -> dict:
        return {
            "invoice": o.number,
            "payee": o.payee,
            "date": str(o.invoice_date),
            "open": str(o.open_amount),
            "currency": o.currency,
        }

    for fname, t in drafts:
        units = t.postings[0].units
        src = _txn_dict(fname, t)

        if units.number > 0:  # incoming → open invoice
            blob = " ".join(
                [t.payee or "", t.narration or ""] + [str(v) for v in (t.meta or {}).values()]
            ).upper()
            compact = re.sub(r"[^A-Z0-9]", "", blob)
            hit = next((n for ref, n in ref_idx.items() if ref in compact), None)
            if hit:
                matches.append(
                    Match(
                        "payment→invoice",
                        1.0,
                        src,
                        inv_dict(by_number[hit]),
                        ["invoice reference in payment details"],
                    )
                )
                continue
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
                                f"payee ≈ {psim:.2f}",
                                f"amount {'equals' if exact else 'differs from'} open "
                                f"{o.open_amount} {o.currency}",
                            ],
                        )
                    )
        else:  # outgoing → inbox document
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
    dated_docs = [d for d in docs if d.date_hint]
    if dated_docs:
        for e in entries:
            if not isinstance(e, data.Transaction):
                continue
            if any(k.startswith("document") for k in (e.meta or {})):
                continue
            if not any(p.account.startswith(("Expenses:", "Income:")) for p in e.postings):
                continue
            for d in dated_docs:
                dsc = date_score(e.date, Date.fromisoformat(d.date_hint), 7)
                if dsc == 0.0:
                    continue
                psim = similarity(e.payee, d.payee_hint or d.name)
                score = round(0.7 * psim + 0.3 * dsc, 2)
                if score >= THRESHOLD:
                    matches.append(
                        Match(
                            "inbox→booked",
                            score,
                            {"document": d.name},
                            {**_txn_dict(None, e), "staging_file": None},
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
            or m.target.get("document")
            or (
                f"{m.target['date']} {m.target['payee'] or '?'} "
                f"{m.target['amount']} {m.target['currency']}"
            )
        )
        t.add_row(f"[{style}]{m.score:.2f}[/]", m.kind, src, tgt, "; ".join(m.reasons))
    console.print(t)
    console.print()
