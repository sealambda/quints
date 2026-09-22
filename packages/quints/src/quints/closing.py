"""Year-end close: the readiness checklist and the depreciation entry.

Two jobs, both deterministic, neither of which writes the ledger:

``check`` answers "can this fiscal year be closed?" as a list of items, each
one pass / warn / fail with the specifics attached (counts, accounts, dates).
A *fail* is something that must be booked or fixed before the books are
final; a *warn* is a judgement call the reader has to make (an untidy inbox,
an ancient receivable that may need a Delkredere).

``compute_depreciation`` derives the year's depreciation from metadata on the
fixed-asset ``open`` directives and prints the entry to paste — like
``fx revalue`` and the VAT settlement, never a write.

Depreciation vocabulary (on the asset's ``open`` directive in accounts.bean)::

    2026-01-01 open Assets:CH:GmbH:FixedAssets:Equipment CHF
      kmu: "1520"                        ; Büromaschinen, Informatik
      depreciation: "declining"          ; or "linear"
      depreciation_rate: "40"            ; percent
      depreciation_category: "it-equipment"   ; ESTV Merkblatt A/1995
      residual: "1"                      ; pro-memoria franc, optional

``useful_life_years: "3"`` replaces ``depreciation_rate`` for linear assets
(charge = cost / years). ``depreciation_account:`` overrides the expense
account, ``depreciation_contra:`` books the charge indirectly against a
Wertberichtigung account (KMU 15x9) instead of writing the asset down.

Maxima: :data:`MERKBLATT_A1995` is the Normalsatz table of the ESTV's
*Merkblatt A/1995 — Abschreibungen auf dem Anlagevermögen geschäftlicher
Betriebe* (Rechtsgrundlagen: Art. 27 Abs. 2 Bst. a, 28 und 62 DBG), in
percent **of book value**; its footnote 3 halves them for depreciation from
the Anschaffungswert, which is what ``linear`` does here. Exceeding a
category's rate is legal bookkeeping but not deductible without explanation,
so it is a warning, never a refusal.
https://www.estv.admin.ch/dam/de/sd-web/Qyxr5xBfdWDp/dbst-mb-a-1995-geschbetriebe-de.pdf

Idempotence: the generated transaction carries ``depreciation_year: "2026"``.
Re-running after booking it finds the charge already there and prints a zero
delta, exactly like a second ``fx revalue``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from beancount.core import data
from beancount.core import prices as bc_prices
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, fx, inbox, ledger, mwst, receivables, settlement, ui

# ESTV Merkblatt A/1995, Ziffer 1 "Normalsätze in Prozenten des Buchwertes":
# category id → (maximum declining-balance rate in percent, the German
# heading it is printed under). Verified against the ESTV PDF (Art. 27 Abs. 2
# Bst. a, 28 und 62 DBG); footnote 3: "Für Abschreibungen auf dem
# Anschaffungswert sind die genannten Sätze um die Hälfte zu reduzieren."
MERKBLATT_A1995: dict[str, tuple[Decimal, str]] = {
    "buildings-residential": (
        Decimal("2"),
        "Wohnhäuser von Immobiliengesellschaften und Personalwohnhäuser (Gebäude allein)",
    ),
    "buildings-residential-with-land": (
        Decimal("1.5"),
        "Wohnhäuser von Immobiliengesellschaften und Personalwohnhäuser (Gebäude und Land)",
    ),
    "buildings-commercial": (
        Decimal("4"),
        "Geschäftshäuser, Büro- und Bankgebäude, Warenhäuser, Kinogebäude (Gebäude allein)",
    ),
    "buildings-commercial-with-land": (
        Decimal("3"),
        "Geschäftshäuser, Büro- und Bankgebäude, Warenhäuser, Kinogebäude (Gebäude und Land)",
    ),
    "buildings-hospitality": (
        Decimal("6"),
        "Gebäude des Gastwirtschaftsgewerbes und der Hotellerie (Gebäude allein)",
    ),
    "buildings-hospitality-with-land": (
        Decimal("4"),
        "Gebäude des Gastwirtschaftsgewerbes und der Hotellerie (Gebäude und Land)",
    ),
    "buildings-industrial": (
        Decimal("8"),
        "Fabrikgebäude, Lagergebäude und gewerbliche Bauten (Gebäude allein)",
    ),
    "buildings-industrial-with-land": (
        Decimal("7"),
        "Fabrikgebäude, Lagergebäude und gewerbliche Bauten (Gebäude und Land)",
    ),
    "high-bay-warehouse": (Decimal("15"), "Hochregallager und ähnliche Einrichtungen"),
    "movable-structures": (Decimal("20"), "Fahrnisbauten auf fremdem Grund und Boden"),
    "rail-sidings": (Decimal("20"), "Geleiseanschlüsse"),
    "industrial-water-pipes": (Decimal("20"), "Wasserleitungen zu industriellen Zwecken"),
    "tanks-containers": (Decimal("20"), "Tanks (inkl. Zisternenwaggons), Container"),
    "furniture": (
        Decimal("25"),
        "Geschäftsmobiliar, Werkstatt- und Lagereinrichtungen mit Mobiliarcharakter",
    ),
    "transport-non-motorised": (
        Decimal("30"),
        "Transportmittel aller Art ohne Motorfahrzeuge, insbesondere Anhänger",
    ),
    "machines": (Decimal("30"), "Apparate und Maschinen zu Produktionszwecken"),
    "vehicles": (Decimal("40"), "Motorfahrzeuge aller Art"),
    "machines-heavy-duty": (
        Decimal("40"),
        "Maschinen im Schichtbetrieb oder unter besonderen Bedingungen",
    ),
    "machines-chemical": (
        Decimal("40"),
        "Maschinen, die in erhöhtem Masse schädigenden chemischen Einflüssen ausgesetzt sind",
    ),
    "office-machines": (Decimal("40"), "Büromaschinen"),
    "it-equipment": (Decimal("40"), "Datenverarbeitungsanlagen (Hardware und Software)"),
    "intangibles": (
        Decimal("40"),
        "Immaterielle Werte, die der Erwerbstätigkeit dienen; Goodwill",
    ),
    "control-systems": (Decimal("40"), "Automatische Steuerungssysteme"),
    "measuring-equipment": (
        Decimal("40"),
        "Sicherheitseinrichtungen, elektronische Mess- und Prüfgeräte",
    ),
    "tools": (
        Decimal("45"),
        "Werkzeuge, Werkgeschirr, Maschinenwerkzeuge, Geräte, Gebinde, Gerüstmaterial, Paletten",
    ),
    "hospitality-supplies": (
        Decimal("45"),
        "Hotel- und Gastwirtschaftsgeschirr sowie Hotel- und Gastwirtschaftswäsche",
    ),
}

MERKBLATT_URL = (
    "https://www.estv.admin.ch/dam/de/sd-web/Qyxr5xBfdWDp/dbst-mb-a-1995-geschbetriebe-de.pdf"
)

# The metadata key the generated transaction carries, so a second run sees
# the charge is already booked (the `open` directive's own `depreciation:`
# key names the method — deliberately a different key).
YEAR_KEY = "depreciation_year"

PASS, WARN, FAIL = "pass", "warn", "fail"


# ── depreciation ─────────────────────────────────────────────────────────────


@dataclass
class AssetDepreciation:
    """One fixed-asset account's charge for the year."""

    account: str
    method: str  # "declining" | "linear"
    rate: Decimal | None  # percent, as configured
    useful_life_years: int | None
    category: str | None
    max_rate: Decimal | None  # Merkblatt A/1995 ceiling for this method
    exceeds_max: bool
    cost: Decimal  # historical cost (additions up to year end)
    book_start: Decimal  # book value at the start of the year
    additions: Decimal  # capitalised during the year
    base: Decimal  # what the rate is applied to (pro-rata included)
    book_before: Decimal  # book value at year end, before this year's charge
    residual: Decimal
    expected: Decimal  # the year's charge
    booked: Decimal  # already booked for the year
    expense_account: str
    contra_account: str | None  # set = indirect (Wertberichtigung)
    acquired: Date | None

    @property
    def delta(self) -> Decimal:
        """What still has to be booked (0 once the entry is in the books)."""
        return self.expected - self.booked

    @property
    def book_after(self) -> Decimal:
        return self.book_before - self.expected


@dataclass
class DepreciationPlan:
    year: int
    at: str  # the fiscal year end
    assets: list[AssetDepreciation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((a.delta for a in self.assets), Decimal("0"))


def _meta_str(meta: data.Meta | None, key: str) -> str | None:
    value = (meta or {}).get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _meta_decimal(meta: data.Meta | None, key: str) -> Decimal | None:
    text = _meta_str(meta, key)
    if text is None:
        return None
    try:
        return Decimal(text)
    except (ArithmeticError, ValueError):
        raise ValueError(f"{key}: {text!r} is not a number") from None


def _prorata(acquired: Date, year: int, rule: str) -> Decimal:
    """The fraction of a year an addition made in ``year`` is depreciated for.

    ``full`` — the Swiss small-business default: a full year's depreciation in
    the year of acquisition. ``months`` — from the month of acquisition to
    December, inclusive.
    """
    if rule != "months" or acquired.year != year:
        return Decimal("1")
    return Decimal(13 - acquired.month) / Decimal("12")


def _is_depreciation(entry: data.Transaction, year: int) -> bool:
    return _meta_str(entry.meta, YEAR_KEY) == str(year)


def compute_depreciation(
    ledger_path: Path,
    year: int,
    cfg: config.Config | None = None,
    entries: data.Directives | None = None,
) -> DepreciationPlan:
    """The year's depreciation per fixed-asset account, expected vs booked.

    ``declining`` applies the rate to the book value carried into the year
    plus the year's additions (weighted by the pro-rata rule) less disposals —
    which is what "rate × book value at the start of the year" means once a
    ``full`` year is granted on this year's purchases. ``linear`` applies it
    to historical cost instead (or cost / ``useful_life_years``). Both are
    floored so the book value never falls below ``residual:``.
    """
    cfg = cfg or config.get()
    if entries is None:
        entries, _ = ledger.load_entries(ledger_path)
    year_start, year_end = Date(year, 1, 1), Date(year, 12, 31)

    assets: list[data.Open] = [
        e
        for e in entries
        if isinstance(e, data.Open)
        and cfg.entity_marker in e.account
        and _meta_str(e.meta, "depreciation") is not None
    ]
    plan = DepreciationPlan(year=year, at=str(year_end))
    if not assets:
        return plan

    accounts = {e.account for e in assets}
    contras = {
        e.account: _meta_str(e.meta, "depreciation_contra")
        for e in assets
        if _meta_str(e.meta, "depreciation_contra")
    }
    watched = accounts | {c for c in contras.values() if c}

    # One pass over the books: what each account carried into the year, what
    # was capitalised during it, and what this year's own entry already booked.
    opening: dict[str, Decimal] = {}
    additions: dict[str, Decimal] = {}
    disposals: dict[str, Decimal] = {}
    cost: dict[str, Decimal] = {}
    booked: dict[str, Decimal] = {}
    acquired: dict[str, Date] = {}
    weighted: dict[str, Decimal] = {}
    for e in entries:
        if not isinstance(e, data.Transaction) or e.date > year_end:
            continue
        charge_entry = _is_depreciation(e, year)
        for p in e.postings:
            if p.account not in watched or p.units is None or p.units.number is None:
                continue
            number = p.units.number
            if charge_entry:
                booked[p.account] = booked.get(p.account, Decimal("0")) - number
                continue
            if e.date < year_start:
                opening[p.account] = opening.get(p.account, Decimal("0")) + number
            elif number >= 0:
                additions[p.account] = additions.get(p.account, Decimal("0")) + number
                weighted[p.account] = weighted.get(p.account, Decimal("0")) + number * _prorata(
                    e.date, year, cfg.depreciation_prorata
                )
            else:
                disposals[p.account] = disposals.get(p.account, Decimal("0")) + number
            if number > 0:
                cost[p.account] = cost.get(p.account, Decimal("0")) + number
                if p.account not in acquired or e.date < acquired[p.account]:
                    acquired[p.account] = e.date

    for open_directive in sorted(assets, key=lambda e: e.account):
        account = open_directive.account
        meta = open_directive.meta
        method = (_meta_str(meta, "depreciation") or "").lower()
        if method not in ("declining", "linear"):
            plan.warnings.append(
                f'{account}: depreciation: "{method}" is not a method — '
                f'use "declining" or "linear".'
            )
            continue
        rate = _meta_decimal(meta, "depreciation_rate")
        life_text = _meta_str(meta, "useful_life_years")
        life = int(Decimal(life_text)) if life_text else None
        if rate is None and not life:
            plan.warnings.append(
                f"{account}: neither depreciation_rate: nor useful_life_years: is set — skipped."
            )
            continue
        residual = _meta_decimal(meta, "residual") or Decimal("0")
        contra = contras.get(account)
        if contra is None and cfg.depreciation_method == "indirect":
            plan.warnings.append(
                f'{account}: [close] method = "indirect" needs a depreciation_contra: '
                f"account (KMU 15x9 Wertberichtigungen) on the open directive — "
                f"booked directly instead."
            )

        prior_contra = opening.get(contra, Decimal("0")) if contra else Decimal("0")
        book_start = opening.get(account, Decimal("0")) + prior_contra
        added = additions.get(account, Decimal("0"))
        weighted_additions = weighted.get(account, Decimal("0"))
        gone = disposals.get(account, Decimal("0"))
        book_before = book_start + added + gone
        if method == "declining":
            # Rate on the book value carried into the year, plus this year's
            # own purchases at their pro-rata weight (`full` = a whole year).
            base = book_start + weighted_additions + gone
            charge = base * (rate or Decimal("0")) / Decimal("100")
        else:
            # Linear runs off historical cost, not book value: prior years'
            # cost in full, this year's additions at their pro-rata weight.
            base = cost.get(account, Decimal("0")) - added + weighted_additions
            charge = (
                base / Decimal(life) if life else base * (rate or Decimal("0")) / Decimal("100")
            )
        charge = max(Decimal("0"), min(charge, book_before - residual))

        max_rate: Decimal | None = None
        category = _meta_str(meta, "depreciation_category")
        if category and category in MERKBLATT_A1995:
            ceiling, heading = MERKBLATT_A1995[category]
            max_rate = ceiling if method == "declining" else ceiling / 2
            applied = rate if rate is not None else (Decimal("100") / Decimal(life or 1))
            if applied > max_rate:
                plan.warnings.append(
                    f"{account}: {applied:g}% exceeds the {max_rate:g}% Normalsatz for "
                    f"{category} ({heading}) — ESTV Merkblatt A/1995; the excess needs a "
                    f"reason the Steuerverwaltung accepts."
                )
        elif category:
            plan.warnings.append(
                f"{account}: unknown depreciation_category: {category!r} — "
                f"no Merkblatt A/1995 ceiling checked."
            )

        plan.assets.append(
            AssetDepreciation(
                account=account,
                method=method,
                rate=rate,
                useful_life_years=life,
                category=category,
                max_rate=max_rate,
                exceeds_max=bool(
                    max_rate is not None
                    and (rate if rate is not None else Decimal("100") / Decimal(life or 1))
                    > max_rate
                ),
                cost=ledger.rappen(cost.get(account, Decimal("0"))),
                book_start=ledger.rappen(book_start),
                additions=ledger.rappen(added),
                base=ledger.rappen(base),
                book_before=ledger.rappen(book_before),
                residual=residual,
                expected=ledger.rappen(charge),
                booked=ledger.rappen(booked.get(contra or account, Decimal("0"))),
                expense_account=_meta_str(meta, "depreciation_account") or cfg.depreciation_account,
                contra_account=contra,
                acquired=acquired.get(account),
            )
        )
    return plan


def depreciation_text(plan: DepreciationPlan, cfg: config.Config | None = None) -> str:
    """The paste-ready block: one transaction plus the balance assertions."""
    cfg = cfg or config.get()
    due = [a for a in plan.assets if a.delta]
    if not due:
        return ""
    width = max(len(a.expense_account) for a in due)
    width = max(width, max(len(a.contra_account or a.account) for a in due))
    lines = [
        f'{plan.at} * "Depreciation {plan.year} (Art. 960a OR)"',
        f'    {YEAR_KEY}: "{plan.year}"',
    ]
    for a in due:
        basis = (
            f"{a.rate:g}% of {a.base} CHF"
            if a.rate is not None
            else f"{a.cost} CHF over {a.useful_life_years} years"
        )
        lines.append(f"    ; {a.account}: {a.method} {basis} → book value {a.book_after} CHF")
    for a in due:
        lines.append(f"    {a.expense_account:<{width}} {a.delta:>12} CHF")
        lines.append(f"    {a.contra_account or a.account:<{width}} {-a.delta:>12} CHF")
    lines.append("")
    assert_date = Date(plan.year + 1, 1, 1)
    for a in due:
        # Beancount reads a balance assertion at the *start* of its date, so
        # the year-end balance is asserted on 1 January.
        account = a.contra_account or a.account
        amount = -(a.booked + a.delta) if a.contra_account else a.book_after
        lines.append(f"{assert_date} balance {account:<{width}} {amount:>12} CHF")
    return "\n".join(lines)


# ── the readiness checklist ──────────────────────────────────────────────────


@dataclass
class CheckItem:
    """One line of the checklist: what was looked at, and what was found."""

    id: str
    status: str  # PASS | WARN | FAIL
    detail: str
    data: dict[str, object] = field(default_factory=dict)


@dataclass
class Checklist:
    year: int
    at: str
    items: list[CheckItem] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if i.status == FAIL)

    @property
    def warned(self) -> int:
        return sum(1 for i in self.items if i.status == WARN)

    @property
    def ok(self) -> bool:
        """True when nothing *fails* — warnings are judgement calls."""
        return self.failed == 0


def _bank_accounts(entries: data.Directives, cfg: config.Config, upto: Date) -> list[str]:
    """Accounts that hold cash: KMU 1020 plus every importer-configured one.

    Only accounts the ledger actually has open at ``upto`` — an importer
    section may name an account that is not opened yet (or is already closed),
    and asking for a balance assertion on one would never be satisfiable.
    """
    open_at: set[str] = set()
    closed: set[str] = set()
    found: set[str] = set()
    for e in entries:
        if isinstance(e, data.Open) and e.date <= upto:
            open_at.add(e.account)
            if cfg.entity_marker in e.account and _meta_str(e.meta, "kmu") == "1020":
                found.add(e.account)
        elif isinstance(e, data.Close) and e.date <= upto:
            closed.add(e.account)
    for importer in (cfg.import_ubs, cfg.import_yapeal):
        if importer is not None:
            found.add(importer.account)
    for multi in (cfg.import_wise, cfg.import_stripe):
        if multi is not None:
            found.update(multi.account_map.values())
    return sorted((found & open_at) - closed)


def _held_currencies(entries: data.Directives, cfg: config.Config, upto: Date) -> list[str]:
    """Foreign currencies with a non-zero Asset/Liability balance at ``upto``."""
    balances: dict[str, Decimal] = {}
    for e in entries:
        if not isinstance(e, data.Transaction) or e.date > upto:
            continue
        for p in e.postings:
            if p.units is None or p.units.number is None:
                continue
            if cfg.entity_marker not in p.account:
                continue
            if p.account.split(":", 1)[0] not in ("Assets", "Liabilities"):
                continue
            if p.units.currency == cfg.operating_currency:
                continue
            balances[p.units.currency] = (
                balances.get(p.units.currency, Decimal("0")) + p.units.number
            )
    return sorted(c for c, total in balances.items() if total)


def _generated(
    entry: data.Transaction, cfg: config.Config, generated_accounts: frozenset[str]
) -> bool:
    """A transaction quints itself prints to paste, so it carries no document.

    Recognised structurally, not by narration: a VAT settlement or its payment
    (the ``^VAT-…`` link), the depreciation entry (its year marker), or an
    entry whose only Income/Expenses legs are the FX gain/loss, rounding and
    depreciation accounts (the FX revaluation).
    """
    if any(link.startswith("VAT-") for link in (entry.links or ())):
        return True
    if _meta_str(entry.meta, YEAR_KEY) is not None:
        return True
    # Same leg filter as the checklist item, so the two never disagree about
    # what counts as an income/expense leg (US:LLC legs are out of scope).
    flows = [
        p.account
        for p in entry.postings
        if cfg.entity_marker in p.account and p.account.split(":", 1)[0] in ("Income", "Expenses")
    ]
    return bool(flows) and all(account in generated_accounts for account in flows)


def _item_ledger(errors: Sequence[object]) -> CheckItem:
    if not errors:
        return CheckItem("ledger", PASS, "Ledger loads; every KMU code is present and valid.")
    messages = [str(getattr(e, "message", e)) for e in errors]
    return CheckItem(
        "ledger",
        FAIL,
        f"{len(errors)} loader error(s) — run `quints check`.",
        {"errors": len(errors), "messages": messages[:5]},
    )


def _item_vat(
    ledger_path: Path,
    entries: data.Directives,
    year: int,
    cfg: config.Config,
) -> CheckItem:
    since = cfg.vat_registered_since
    if since is None:
        return CheckItem("vat", PASS, "Not VAT registered — nothing to settle.")
    periods: list[str] = []
    for quarter in range(1, 5):
        _start, end = mwst.quarter_range(f"{year}-Q{quarter}")
        if Date.fromisoformat(end) >= since:
            periods.append(f"VAT-{year}-Q{quarter}")
    if not periods:
        return CheckItem("vat", PASS, f"VAT liability starts {since} — no period in {year}.")

    settled: set[str] = set()
    for e in entries:
        if not isinstance(e, data.Transaction):
            continue
        links = [lk for lk in (e.links or ()) if lk.startswith("VAT-")]
        if links and any(p.account == cfg.payable_vat for p in e.postings):
            settled.update(links)
    missing = [p for p in periods if p not in settled]
    liabilities, _unlinked, _total, _today = settlement.outstanding(
        ledger_path, cfg=cfg, entries=entries
    )
    unpaid = [liab for liab in liabilities if liab.period in periods]
    payload: dict[str, object] = {
        "periods": periods,
        "unsettled": missing,
        "unpaid": [{"period": liab.period, "owed": liab.owed, "due": liab.due} for liab in unpaid],
    }
    if missing:
        return CheckItem(
            "vat",
            FAIL,
            f"{len(missing)} VAT period(s) not settled: {', '.join(missing)} — "
            f"`quints vat settle -q {missing[0].removeprefix('VAT-')}`.",
            payload,
        )
    if unpaid:
        owed = sum((liab.owed for liab in unpaid), Decimal("0"))
        return CheckItem(
            "vat",
            WARN,
            f"Every period is filed; {ui.money(owed)} CHF still unpaid "
            f"({', '.join(liab.period for liab in unpaid)}).",
            payload,
        )
    return CheckItem("vat", PASS, f"All {len(periods)} VAT period(s) settled and paid.", payload)


def _item_flagged(entries: data.Directives, year: int) -> CheckItem:
    flagged = [
        e
        for e in entries
        if isinstance(e, data.Transaction) and e.date.year == year and e.flag == "!"
    ]
    if not flagged:
        return CheckItem("flagged", PASS, "No `!`-flagged transactions in the year.")
    examples = [f"{e.date} {e.payee or ''} {e.narration}".strip() for e in flagged[:3]]
    return CheckItem(
        "flagged",
        FAIL,
        f"{len(flagged)} `!`-flagged transaction(s) still incomplete.",
        {"count": len(flagged), "examples": examples},
    )


def _item_staging(root: Path) -> CheckItem:
    staging = root / "staging"
    drafts = (
        sorted(p.name for p in staging.iterdir() if p.is_file() and not p.name.startswith("."))
        if staging.is_dir()
        else []
    )
    if not drafts:
        return CheckItem("staging", PASS, "staging/ is empty — no unreviewed drafts.")
    return CheckItem(
        "staging",
        WARN,
        f"{len(drafts)} draft file(s) in staging/ — review and move them into books/.",
        {"count": len(drafts), "files": drafts[:5]},
    )


def _item_inbox(root: Path, entries: data.Directives) -> CheckItem:
    docs = inbox.scan(root, entries)
    pending = [d for d in docs if not d.linked and not d.duplicate_of]
    if not docs:
        return CheckItem("inbox", PASS, "inbox/ is empty — every document is filed.")
    return CheckItem(
        "inbox",
        WARN,
        f"{len(docs)} document(s) in inbox/ ({len(pending)} still to book) — `quints inbox`.",
        {"count": len(docs), "unbooked": len(pending), "files": [d.name for d in docs[:5]]},
    )


def _item_documents(
    entries: data.Directives, year: int, cfg: config.Config, generated_accounts: frozenset[str]
) -> CheckItem:
    missing: list[str] = []
    total = 0
    for e in entries:
        if not isinstance(e, data.Transaction) or e.date.year != year:
            continue
        flows = [
            p
            for p in e.postings
            if cfg.entity_marker in p.account
            and p.account.split(":", 1)[0] in ("Income", "Expenses")
        ]
        if not flows or _generated(e, cfg, generated_accounts):
            continue
        total += 1
        linked = any(key.startswith("document") for key in (e.meta or {}))
        linked = linked or any(
            any(key.startswith("document") for key in (p.meta or {})) for p in e.postings
        )
        if not linked:
            missing.append(f"{e.date} {e.payee or ''} {e.narration}".strip())
    if not missing:
        return CheckItem(
            "documents", PASS, f"All {total} income/expense transaction(s) link a document."
        )
    return CheckItem(
        "documents",
        WARN,
        f"{len(missing)} of {total} income/expense transaction(s) have no `document:` link "
        f"(generated entries — VAT settlements, FX revaluation, depreciation, rounding — "
        f"are exempt).",
        {"count": len(missing), "of": total, "examples": missing[:3]},
    )


def _item_assertions(entries: data.Directives, year: int, cfg: config.Config) -> CheckItem:
    year_end = Date(year, 12, 31)
    accounts = _bank_accounts(entries, cfg, year_end)
    if not accounts:
        return CheckItem("assertions", PASS, "No bank account open at the year end.")
    # Beancount evaluates a `balance` at the *start* of its date, so only an
    # assertion dated 1 January (or later) covers 31 December.
    cutoff = Date(year + 1, 1, 1)
    asserted = {e.account for e in entries if isinstance(e, data.Balance) and e.date >= cutoff}
    missing = [a for a in accounts if a not in asserted]
    payload: dict[str, object] = {
        "accounts": accounts,
        "missing": missing,
        "on_or_after": str(cutoff),
    }
    if missing:
        return CheckItem(
            "assertions",
            FAIL,
            f"{len(missing)} bank account(s) without a balance assertion dated {cutoff} "
            f"or later: {', '.join(missing)}.",
            payload,
        )
    return CheckItem(
        "assertions",
        PASS,
        f"All {len(accounts)} bank account(s) carry a {cutoff}-or-later balance assertion.",
        payload,
    )


def _item_fx(ledger_path: Path, year: int, cfg: config.Config) -> CheckItem:
    at = f"{year}-12-31"
    try:
        revaluations = fx.compute(ledger_path, at, cfg)
    except fx.RateUnavailable as e:
        return CheckItem(
            "fx",
            WARN,
            f"Cannot revalue: {e} — run `quints prices sync`.",
            {"currency": e.ccy, "missing_on": str(e.on)},
        )
    open_deltas = [r for r in revaluations if r.delta]
    if not open_deltas:
        return CheckItem("fx", PASS, f"No unrealized FX at {at} — the revaluation is booked.")
    total = sum((r.delta for r in open_deltas), Decimal("0"))
    return CheckItem(
        "fx",
        FAIL,
        f"{ui.money(total)} CHF unrealized FX on {len(open_deltas)} balance(s) — "
        f"`quints fx revalue --at {at}`.",
        {
            "total": total,
            "balances": [
                {"account": r.account, "currency": r.currency, "delta": r.delta}
                for r in open_deltas
            ],
        },
    )


def _item_prices(entries: data.Directives, year: int, cfg: config.Config) -> CheckItem:
    year_end = Date(year, 12, 31)
    held = _held_currencies(entries, cfg, year_end)
    if not held:
        return CheckItem("prices", PASS, "No foreign-currency balance at the year end.")
    price_map = bc_prices.build_price_map(entries)
    missing: list[str] = []
    stale: list[dict[str, object]] = []
    for ccy in held:
        rate_date, rate = ledger.rate(price_map, ccy, year_end, quote=cfg.operating_currency)
        if rate is None or rate_date is None:
            missing.append(ccy)
        elif rate_date < year_end:
            stale.append({"currency": ccy, "latest": str(rate_date)})
    payload: dict[str, object] = {"held": held, "missing": missing, "stale": stale}
    if missing:
        return CheckItem(
            "prices",
            FAIL,
            f"No {'/'.join(missing)} rate on or before {year_end} — `quints prices sync`.",
            payload,
        )
    if stale:
        latest = ", ".join(f"{s['currency']} {s['latest']}" for s in stale)
        return CheckItem(
            "prices",
            WARN,
            f"No rate dated {year_end} itself (latest: {latest}) — "
            f"`quints prices sync` before valuing the balance sheet.",
            payload,
        )
    return CheckItem("prices", PASS, f"Year-end rates present for {', '.join(held)}.", payload)


def _item_depreciation(plan: DepreciationPlan) -> CheckItem:
    payload: dict[str, object] = {
        "assets": [
            {
                "account": a.account,
                "expected": a.expected,
                "booked": a.booked,
                "delta": a.delta,
            }
            for a in plan.assets
        ],
        "warnings": plan.warnings,
    }
    due = [a for a in plan.assets if a.delta]
    if due:
        return CheckItem(
            "depreciation",
            FAIL,
            f"{ui.money(plan.total)} CHF of depreciation not booked on "
            f"{len(due)} asset account(s) — `quints close depreciation --year {plan.year}`.",
            payload,
        )
    if plan.warnings:
        return CheckItem("depreciation", WARN, plan.warnings[0], payload)
    if not plan.assets:
        return CheckItem(
            "depreciation",
            PASS,
            "No account carries depreciation metadata — nothing to write down.",
            payload,
        )
    if not any(a.book_before for a in plan.assets):
        return CheckItem(
            "depreciation",
            PASS,
            f"{len(plan.assets)} fixed-asset account(s) open, nothing capitalised yet.",
            payload,
        )
    return CheckItem(
        "depreciation",
        PASS,
        f"Depreciation booked on {len(plan.assets)} asset account(s).",
        payload,
    )


def _item_receivables(entries: data.Directives, year: int, cfg: config.Config) -> CheckItem:
    year_end = Date(year, 12, 31)
    open_invoices = receivables.compute_from_entries(entries, year_end, cfg)
    old = [o for o in open_invoices if o.age_days > cfg.receivable_review_days]
    if not open_invoices:
        return CheckItem("receivables", PASS, "No receivable open at the year end.")
    payload: dict[str, object] = {
        "open": len(open_invoices),
        "older_than_days": cfg.receivable_review_days,
        "aged": [
            {
                "number": o.number,
                "payee": o.payee,
                "age_days": o.age_days,
                "open_amount": o.open_amount,
                "currency": o.currency,
            }
            for o in old
        ],
    }
    if not old:
        return CheckItem(
            "receivables",
            PASS,
            f"{len(open_invoices)} receivable(s) open, none older than "
            f"{cfg.receivable_review_days} days.",
            payload,
        )
    return CheckItem(
        "receivables",
        WARN,
        f"{len(old)} receivable(s) open longer than {cfg.receivable_review_days} days at "
        f"{year_end} — decide on a Delkredere or a write-off.",
        payload,
    )


def check(ledger_path: Path, year: int, cfg: config.Config | None = None) -> Checklist:
    """The year-end readiness checklist. One ledger load, ten-odd verdicts."""
    cfg = cfg or config.get()
    entries, errors = ledger.load_entries(ledger_path)
    root = ledger_path.resolve().parent
    plan = compute_depreciation(ledger_path, year, cfg, entries)
    generated_accounts = frozenset(
        {cfg.fx_gain, cfg.fx_loss, cfg.rounding_income, cfg.depreciation_account}
        | {a.expense_account for a in plan.assets}
    )
    return Checklist(
        year=year,
        at=f"{year}-12-31",
        items=[
            _item_ledger(errors),
            _item_vat(ledger_path, entries, year, cfg),
            _item_flagged(entries, year),
            _item_staging(root),
            _item_inbox(root, entries),
            _item_documents(entries, year, cfg, generated_accounts),
            _item_assertions(entries, year, cfg),
            _item_fx(ledger_path, year, cfg),
            _item_prices(entries, year, cfg),
            _item_depreciation(plan),
            _item_receivables(entries, year, cfg),
        ],
    )


# ── render ───────────────────────────────────────────────────────────────────

_MARK = {PASS: ("[ok]✓[/]", "ok"), WARN: ("[warn]![/]", "warn"), FAIL: ("[owe]✗[/]", "owe")}

_TITLES = {
    "ledger": "Ledger loads",
    "vat": "VAT periods settled",
    "flagged": "No flagged entries",
    "staging": "Staging empty",
    "inbox": "Inbox filed",
    "documents": "Documents linked",
    "assertions": "Bank balances asserted",
    "fx": "FX revaluation booked",
    "prices": "Year-end rates",
    "depreciation": "Depreciation booked",
    "receivables": "Receivables reviewed",
}


def render_check(checklist: Checklist, console: Console | None = None) -> None:
    console = console or ui.console
    console.print()
    console.rule(f"[bold]Year-end close[/]   {checklist.year}  ·  per {checklist.at}")
    t = Table(box=box.SIMPLE_HEAVY, pad_edge=False, show_header=False)
    t.add_column("", no_wrap=True)
    t.add_column("Item", no_wrap=True)
    t.add_column("Detail")
    for item in checklist.items:
        mark, style = _MARK[item.status]
        t.add_row(mark, f"[{style}]{_TITLES.get(item.id, item.id)}[/]", item.detail)
    console.print(t)
    if checklist.ok and not checklist.warned:
        console.print(f"[ok]{checklist.year} is ready to close.[/]")
    elif checklist.ok:
        console.print(
            f"[warn]Nothing blocking; {checklist.warned} item(s) need a judgement call.[/]"
        )
    else:
        console.print(
            f"[owe]{checklist.failed} item(s) must be fixed before {checklist.year} can be "
            f"closed[/][muted] · {checklist.warned} to review[/]"
        )
    console.print()


def render_depreciation(plan: DepreciationPlan, console: Console | None = None) -> None:
    console = console or ui.console
    console.print()
    console.rule(f"[bold]Depreciation[/]   {plan.year}  ·  per {plan.at}")
    if not plan.assets:
        console.print(
            "No account carries depreciation metadata — add `depreciation:` to a "
            "fixed-asset open directive (see the year-end guide).",
            style="muted",
        )
        console.print()
        return

    t = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    t.add_column("Account")
    t.add_column("Method", no_wrap=True)
    t.add_column("Base CHF", justify="right", no_wrap=True)
    t.add_column("Charge CHF", justify="right", no_wrap=True)
    t.add_column("Booked CHF", justify="right", no_wrap=True)
    t.add_column("Book value CHF", justify="right", no_wrap=True)
    for a in plan.assets:
        rate = f"{a.rate:g}%" if a.rate is not None else f"{a.useful_life_years} y"
        style = "warn" if a.exceeds_max else "muted"
        t.add_row(
            a.account,
            f"[{style}]{a.method} {rate}[/]",
            ui.money(a.base),
            ui.money(a.expected),
            ui.money(a.booked),
            ui.money(a.book_after),
        )
    console.print(t)
    for warning in plan.warnings:
        console.print(f"[warn]{warning}[/]")
    if not plan.total:
        if any(a.expected for a in plan.assets):
            console.print(
                f"[ok]Depreciation for {plan.year} is already booked — nothing to paste.[/]"
            )
        else:
            console.print(f"[muted]No book value to write down in {plan.year}.[/]")
        console.print()
        return
    console.print()
    console.print("Paste into books/<year>.bean (review first):", style="muted")
    console.print()
    console.print(depreciation_text(plan), highlight=False, markup=False)
    console.print()
    console.print(
        f"[muted]Maximum rates: ESTV Merkblatt A/1995 — {MERKBLATT_URL}[/]",
    )
    console.print()
