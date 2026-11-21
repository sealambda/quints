"""The state of the books on one page: `quints digest`.

VAT periods still to settle or pay, the receivables' aging, the inbox
backlog, and the hygiene of the books. Advisory: it never fails, and the
gate is `quints check`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core import data

from . import closing, config, diagnostics, inbox, ledger, mwst, receivables, settlement

FILING_DAYS = 60  # a return is due 60 days after its period ends (Art. 71 Abs. 1 MWSTG)
VAT_WARN_DAYS = 21  # a due date closer than this needs attention
RECEIVABLE_WARN_DAYS = 45  # an invoice open longer than this needs attention
MAX_LISTED = 10


@dataclass
class Unsettled:
    """A VAT period that has ended and has no settlement transaction."""

    period: str
    due: str
    days_left: int


@dataclass
class Undocumented:
    date: str
    payee: str
    narration: str
    file: str | None
    line: int | None


@dataclass
class Running:
    """The VAT period holding today, as it stands."""

    period: str
    z200: Decimal
    z400: Decimal
    z500: Decimal


@dataclass
class Digest:
    today: str
    unsettled: list[Unsettled] = field(default_factory=list)
    running: Running | None = None
    unpaid: list[settlement.Liability] = field(default_factory=list)
    receivables: list[receivables.OpenInvoice] = field(default_factory=list)
    inbox: list[str] = field(default_factory=list)  # still to book
    undocumented: list[Undocumented] = field(default_factory=list)  # this year's
    documented_of: int = 0  # this year's transactions that need a document
    orphans: list[str] = field(default_factory=list)  # under `option "documents"`
    alerts: list[str] = field(default_factory=list)


def compute(ledger_path: Path, today: Date, cfg: config.Config | None = None) -> Digest:
    cfg = cfg or config.get()
    entries, errors, options = loader.load_file(str(ledger_path))
    loaded = (entries, ledger.violations(errors))
    root = Path(os.path.abspath(ledger_path)).parent
    digest = Digest(today=str(today))

    for link in mwst.unsettled_periods(entries, cfg, today):
        end = Date.fromisoformat(mwst.period_range(link.removeprefix("VAT-"))[1])
        if end < today:
            due = end + timedelta(days=FILING_DAYS)
            digest.unsettled.append(Unsettled(link, str(due), (due - today).days))
        else:
            report = mwst.compute(ledger_path, *mwst.period_range(link[4:]), cfg, loaded=loaded)
            digest.running = Running(link, report.z200, report.z400, report.z500)
    digest.unpaid, _unlinked, _total, _today = settlement.outstanding(
        ledger_path, today, cfg, entries
    )
    digest.receivables = sorted(
        receivables.compute_from_entries(entries, today, cfg), key=lambda o: -o.age_days
    )
    digest.inbox = [
        d.name for d in inbox.scan(root, entries) if not d.linked and not d.duplicate_of
    ]

    plan = closing.compute_depreciation(ledger_path, today.year, cfg, entries)
    generated = closing.generated_accounts(cfg, plan)
    missing, digest.documented_of = closing.undocumented(entries, today.year, cfg, generated)
    for e in missing:
        file, line = ledger.location(e.meta)
        digest.undocumented.append(
            Undocumented(str(e.date), e.payee or "", e.narration or "", file, line)
        )
    digest.orphans = _orphans(entries, options.get("documents", []), root)
    digest.alerts = _alerts(digest)
    return digest


def _orphans(entries: data.Directives, folders: list[str], root: Path) -> list[str]:
    """Files under the documents folders that no ``document…:`` key names."""
    named = {
        os.path.basename(str(value))
        for e in entries
        if isinstance(e, data.Transaction)
        for meta in (e.meta or {}, *(p.meta or {} for p in e.postings))
        for key, value in meta.items()
        if key.startswith("document") and value
    }
    out: list[str] = []
    for folder in folders:
        base = root / folder
        out += [
            str(path.relative_to(root))
            for path in base.rglob("*")
            if path.is_file() and not path.name.startswith(".") and path.name not in named
        ]
    return sorted(out)


def _alerts(d: Digest) -> list[str]:
    alerts = [
        f"{u.period} is not settled — the return is due {u.due} ({u.days_left} days)"
        for u in d.unsettled
        if u.days_left <= VAT_WARN_DAYS
    ]
    for liab in d.unpaid:
        if liab.days_left is None:
            alerts.append(f"{liab.period}: {liab.owed:,.2f} CHF owed, no due: on its settlement")
        elif liab.days_left <= VAT_WARN_DAYS:
            alerts.append(
                f"{liab.period}: {liab.owed:,.2f} CHF due {liab.due} ({liab.days_left} days)"
            )
    alerts += [
        f"{o.number} ({o.payee}) open {o.open_amount:,.2f} {o.currency} for {o.age_days} days"
        for o in d.receivables
        if o.age_days >= RECEIVABLE_WARN_DAYS
    ]
    if d.inbox:
        alerts.append(f"{len(d.inbox)} document(s) waiting in inbox/")
    return alerts


def render(d: Digest, markdown: bool) -> str:
    """The digest as text, or as Markdown for an issue or a job summary."""
    h = "## " if markdown else ""

    def table(head: list[str], rows: list[list[str]]) -> list[str]:
        if not markdown:
            return [" · ".join(r) for r in rows]
        return [
            "| " + " | ".join(head) + " |",
            "|" + "---|" * len(head),
            *("| " + " | ".join(r) + " |" for r in rows),
        ]

    lines = [f"Books as of {d.today}.", ""]
    if d.alerts:
        lines += ["Needs attention:", "", *(f"- {a}" for a in d.alerts), ""]
    else:
        lines += ["Nothing needs attention.", ""]

    lines += [f"{h}VAT", ""]
    if not d.unsettled and not d.unpaid:
        lines += ["Every ended period is settled and paid."]
    if d.unsettled:
        rows = [[u.period, u.due, str(u.days_left)] for u in d.unsettled]
        lines += ["Not settled:", "", *table(["Period", "Return due", "Days left"], rows), ""]
    if d.unpaid:
        rows = [
            [
                x.period,
                f"{x.owed:,.2f}",
                x.due or "—",
                "—" if x.days_left is None else str(x.days_left),
            ]
            for x in d.unpaid
        ]
        lines += [
            "Settled, not paid:",
            "",
            *table(["Period", "Owed CHF", "Due", "Days left"], rows),
        ]
    if d.running:
        r = d.running
        lines += [
            "",
            f"Running {r.period}: turnover (Ziffer 200) {r.z200:,.2f}, input VAT (Ziffer 400) "
            f"{r.z400:,.2f}, payable (Ziffer 500) {r.z500:,.2f} CHF.",
        ]
    lines += ["", f"{h}Open invoices", ""]
    if d.receivables:
        rows = [
            [
                o.number,
                o.payee,
                str(o.invoice_date),
                f"{o.open_amount:,.2f} {o.currency}",
                f"{o.age_days} d",
            ]
            for o in d.receivables[:MAX_LISTED]
        ]
        lines += table(["Invoice", "Customer", "Issued", "Open", "Age"], rows)
        totals: dict[str, Decimal] = {}
        for o in d.receivables:
            totals[o.currency] = totals.get(o.currency, Decimal("0")) + o.open_amount
        lines += [
            "",
            "Total open: " + ", ".join(f"{v:,.2f} {c}" for c, v in sorted(totals.items())),
        ]
    else:
        lines += ["Nothing outstanding."]
    lines += ["", f"{h}Inbox", ""]
    lines += [f"- {name}" for name in d.inbox[:MAX_LISTED]] or ["Empty: everything is filed."]
    lines += ["", f"{h}Hygiene", ""]
    year = d.today[:4]
    lines.append(
        f"- {year} income/expense transactions with no document: "
        f"{len(d.undocumented)} of {d.documented_of}"
    )
    for u in d.undocumented[-5:]:
        lines.append(f"  - {diagnostics.where(u)} {u.date} {u.payee} {u.narration}".rstrip())
    lines.append(f"- Files under the documents folders that nothing names: {len(d.orphans)}")
    lines += [f"  - {path}" for path in d.orphans[:MAX_LISTED]]
    return "\n".join(lines).rstrip() + "\n"
