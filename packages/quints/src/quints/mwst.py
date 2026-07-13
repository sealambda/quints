"""Swiss MWST (VAT) report — effective method, 8.1% standard rate.

Structure mirrors ESTV Formular 310 (validated against a filed Q1/2026 return):

    200  Total weltweiter Umsatz (net)          = Inland + Ausland
    221  Leistungen im Ausland (zero-rated exports, Income:CH:GmbH:*:Export)
    289  Total Abzüge                           = 221 (+ any other deductions)
    299  Steuerbarer Gesamtumsatz               = 200 − 289  (= domestic net)
    303  Leistungen zum Normalsatz 8.1%         net + Steuer
    382  Bezugsteuer (Art. 45 ff. MWSTG)        net + Steuer (reverse charge)
    399  Total geschuldete Steuer               = output VAT + Bezugsteuer
    400  Vorsteuer auf Material/DL              = input VAT (incl. Bezugsteuer deduction)
    479  Total Vorsteuer                        = 400
    500  Zu bezahlender Betrag                  = 399 − 479

VAT is computed on an **accrual** basis directly from ledger entries: output VAT
from credits to OutputVAT (sales), input VAT from debits to InputVAT (purchases),
Bezugsteuer from credits to the Bezugsteuer liability (reverse-charge purchases —
the matching InputVAT debit lands in Ziffer 400 on its own, so the pair is
cash-neutral). The quarterly settlement (which debits OutputVAT/Bezugsteuer and
credits InputVAT into PayableVAT) and the one-off pre-liability reversal
therefore fall out automatically — they move VAT in the opposite direction and
are not accruals.

Computation (`compute`) is separated from presentation (`render`) so a future
web/TUI front-end can consume the :class:`MwstReport` dataclass directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from beancount.core import convert as bc_convert
from beancount.core import data
from beancount.core import prices as bc_prices
from beancount.core.amount import Amount
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, ledger, ui

_QUARTER_MONTHS = {
    1: ("01-01", "03-31"),
    2: ("04-01", "06-30"),
    3: ("07-01", "09-30"),
    4: ("10-01", "12-31"),
}


def quarter_range(quarter: str) -> tuple[str, str]:
    """'2026-Q2' (or '2026Q2') → ('2026-04-01', '2026-06-30')."""
    m = re.match(r"^(\d{4})-?Q([1-4])$", quarter.upper().replace(" ", ""))
    if not m:
        raise ValueError(f"bad quarter {quarter!r}, expected e.g. 2026-Q2")
    year, qn = int(m.group(1)), int(m.group(2))
    start, end = _QUARTER_MONTHS[qn]
    return f"{year}-{start}", f"{year}-{end}"


# ── data ─────────────────────────────────────────────────────────────────────


@dataclass
class VatLine:
    date: str
    payee: str
    narration: str
    original: Decimal
    currency: str
    rate: Decimal  # CHF per unit of `currency`
    chf: Decimal


@dataclass
class RevenueLine:
    date: str
    payee: str
    original: Decimal
    currency: str
    chf: Decimal


@dataclass
class MwstReport:
    date_from: str
    date_to: str
    z200: Decimal
    z221: Decimal
    z289: Decimal
    z299: Decimal
    z303_net: Decimal
    z303_tax: Decimal
    z399: Decimal
    z400: Decimal
    z479: Decimal
    z500: Decimal
    z382_net: Decimal = Decimal("0")
    z382_tax: Decimal = Decimal("0")
    vat_lines: list = field(default_factory=list)
    bezugsteuer_lines: list = field(default_factory=list)
    domestic: list = field(default_factory=list)
    export: list = field(default_factory=list)

    @property
    def domestic_total(self) -> Decimal:
        return sum((r.chf for r in self.domestic), Decimal("0"))

    @property
    def export_total(self) -> Decimal:
        return sum((r.chf for r in self.export), Decimal("0"))


# ── compute ───────────────────────────────────────────────────────────────────


def _to_chf(units: Amount, date: Date, price_map) -> Decimal:
    """Value an amount in CHF at ``date`` (prior-date fallback via price map)."""
    if units.currency == "CHF":
        return units.number
    conv = bc_convert.convert_amount(units, "CHF", price_map, date=date)
    return conv.number if conv.currency == "CHF" else Decimal("0")


def compute(
    ledger_path: Path, date_from: str, date_to: str, cfg: config.Config | None = None
) -> MwstReport:
    cfg = cfg or config.get()
    d0, d1 = Date.fromisoformat(date_from), Date.fromisoformat(date_to)
    # Pre-registration activity is not part of any VAT period (the transition
    # entry reversed its input VAT); clamping keeps calendar-quarter reports
    # reproducing the filed returns for the registration quarter.
    if cfg.vat_registered_since and d0 < cfg.vat_registered_since:
        d0 = cfg.vat_registered_since
    entries, _errors = ledger.load_entries(ledger_path)
    price_map = bc_prices.build_price_map(entries)

    output_vat = Decimal("0")
    input_vat = Decimal("0")
    bezugsteuer = Decimal("0")
    vat_lines: list[VatLine] = []
    bezugsteuer_lines: list[VatLine] = []
    domestic: list[RevenueLine] = []
    export: list[RevenueLine] = []

    for e in entries:
        if not isinstance(e, data.Transaction) or not (d0 <= e.date <= d1):
            continue
        for p in e.postings:
            n = p.units.number
            acct = p.account

            # Output VAT: credits to OutputVAT are sales accruals (settlement is a debit).
            if acct == cfg.output_vat and n < 0:
                output_vat += -n

            # Bezugsteuer: credits are reverse-charge accruals on foreign
            # purchases (Art. 45 ff. MWSTG); the settlement debit is excluded.
            elif acct == cfg.bezugsteuer and n < 0:
                tax = -n
                bezugsteuer += tax
                if p.price is not None:  # e.g. "-7.52 CHF @@ 8.10 EUR" → original in EUR
                    original = (tax * p.price.number).quantize(Decimal("0.01"))
                    currency = p.price.currency
                    rate = (tax / original) if original else Decimal("0")
                else:
                    original, currency, rate = tax, p.units.currency, Decimal("1")
                bezugsteuer_lines.append(
                    VatLine(
                        str(e.date), e.payee or "", e.narration or "", original, currency, rate, tax
                    )
                )

            # Input VAT: debits to InputVAT are purchase accruals
            # (settlement + pre-liability reversal are credits → excluded).
            elif acct == cfg.input_vat and n > 0:
                input_vat += n
                if p.price is not None:  # e.g. "6.39 CHF @@ 8.10 USD" → original in USD
                    original = (n * p.price.number).quantize(Decimal("0.01"))
                    currency = p.price.currency
                    rate = (n / original) if original else Decimal("0")
                else:
                    original, currency, rate = n, p.units.currency, Decimal("1")
                vat_lines.append(
                    VatLine(
                        str(e.date), e.payee or "", e.narration or "", original, currency, rate, n
                    )
                )

            # Revenue: credits to CH GmbH income (converted to CHF at the txn date).
            elif acct.startswith(cfg.income_prefix) and n < 0:
                gross = Amount(-n, p.units.currency)
                chf = _to_chf(gross, e.date, price_map)
                line = RevenueLine(str(e.date), e.payee or "", gross.number, gross.currency, chf)
                (export if cfg.export_marker in acct else domestic).append(line)

    domestic_total = sum((r.chf for r in domestic), Decimal("0"))
    export_total = sum((r.chf for r in export), Decimal("0"))

    z221 = z289 = export_total
    z299 = domestic_total
    z200 = domestic_total + export_total

    return MwstReport(
        date_from=date_from,
        date_to=date_to,
        z200=z200,
        z221=z221,
        z289=z289,
        z299=z299,
        z303_net=domestic_total,
        z303_tax=output_vat,
        z382_net=ledger.rappen(bezugsteuer / ledger.vat_rate(d1)) if bezugsteuer else Decimal("0"),
        z382_tax=bezugsteuer,
        z399=output_vat + bezugsteuer,
        z400=input_vat,
        z479=input_vat,
        z500=output_vat + bezugsteuer - input_vat,
        vat_lines=vat_lines,
        bezugsteuer_lines=bezugsteuer_lines,
        domestic=domestic,
        export=export,
    )


# ── render ────────────────────────────────────────────────────────────────────


def render(
    report: MwstReport, console: Console | None = None, cfg: config.Config | None = None
) -> None:
    cfg = cfg or config.get()
    console = console or ui.console
    console.print()
    console.rule(f"[bold]MWST-Abrechnung[/]   {report.date_from} – {report.date_to}")
    console.print(
        f"{cfg.entity_name} · "
        f"{'Effektive Abrechnungsmethode' if cfg.vat_method == 'effective' else cfg.vat_method} · "
        f"{ledger.vat_rate(Date.fromisoformat(report.date_to)) * 100:.1f} %",
        style="muted",
        justify="center",
    )
    console.print()

    main = Table(box=box.SIMPLE_HEAVY, pad_edge=False, expand=False)
    main.add_column("Ziffer", justify="right", style="ziffer", no_wrap=True)
    main.add_column("Position")
    main.add_column("Umsatz CHF", justify="right", no_wrap=True)
    main.add_column("Steuer CHF", justify="right", no_wrap=True)

    def row(z, label, umsatz=None, steuer=None, style=None):
        u = ui.money(umsatz) if umsatz is not None else ""
        s = ui.money(steuer) if steuer is not None else ""
        if style:
            label, u, s = (f"[{style}]{x}[/]" if x else x for x in (label, u, s))
        main.add_row(z, label, u, s)

    row("200", "Total weltweiter Umsatz (netto)", report.z200)
    row("221", "Leistungen im Ausland (Export)", report.z221)
    row("289", "Total Abzüge", report.z289)
    row("299", "Steuerbarer Gesamtumsatz", report.z299)
    main.add_section()
    row("303", "Leistungen zum Normalsatz 8.1 %", report.z303_net, report.z303_tax)
    row("382", "Bezugsteuer (Art. 45 ff. MWSTG)", report.z382_net, report.z382_tax)
    row("399", "Total geschuldete Steuer", None, report.z399)
    main.add_section()
    row("400", "Vorsteuer Material / DL", None, report.z400)
    row("479", "Total Vorsteuer", None, report.z479)
    main.add_section()
    owed = report.z500 >= 0
    row(
        "500",
        "Zu bezahlender Betrag" if owed else "Guthaben",
        None,
        report.z500,
        style="owe" if owed else "refund",
    )
    console.print(main)

    if report.vat_lines:
        console.print()
        console.print(
            _vat_table(report.vat_lines, report.z400, "Vorsteuer (Input VAT) — Ziffer 400")
        )
    if report.bezugsteuer_lines:
        console.print()
        console.print(
            _vat_table(
                report.bezugsteuer_lines,
                report.z382_tax,
                "Bezugsteuer (reverse charge) — Ziffer 382",
            )
        )
    if report.domestic or report.export:
        console.print()
        console.print(_revenue_table(report))
    console.print()


def _vat_table(lines: list[VatLine], total: Decimal, title: str) -> Table:
    t = Table(
        box=box.SIMPLE,
        title=title,
        title_justify="left",
        title_style="bold",
    )
    t.add_column("Datum", style="muted", no_wrap=True)
    t.add_column("Payee")
    t.add_column("Narration")
    t.add_column("Original", justify="right", no_wrap=True)
    t.add_column("Kurs CHF", justify="right", no_wrap=True)
    t.add_column("CHF", justify="right", no_wrap=True)
    for r in lines:
        t.add_row(
            r.date,
            r.payee,
            r.narration,
            f"{r.original:,.2f} {r.currency}",
            f"{r.rate:.5f}" if r.currency != "CHF" else "—",
            ui.money(r.chf),
        )
    t.add_section()
    t.add_row("", "", "[bold]Total[/]", "", "", f"[bold]{ui.money(total)}[/]")
    return t


def _revenue_table(report: MwstReport) -> Table:
    t = Table(
        box=box.SIMPLE,
        title="Umsatz (Revenue)",
        title_justify="left",
        title_style="bold",
    )
    t.add_column("Datum", style="muted", no_wrap=True)
    t.add_column("Payee")
    t.add_column("Original", justify="right", no_wrap=True)
    t.add_column("CHF", justify="right", no_wrap=True)

    def group(title, rows, subtotal, ziffer):
        t.add_row(f"[bold]{title}[/]", "", "", "")
        for r in rows:
            t.add_row(r.date, r.payee, f"{r.original:,.2f} {r.currency}", ui.money(r.chf))
        t.add_row("", "", f"[muted]Ziffer {ziffer}[/]", f"[bold]{ui.money(subtotal)}[/]")

    group("Inland (steuerbar)", report.domestic, report.z299, "299")
    if report.export:
        t.add_section()
        group("Ausland (Export, zero-rated)", report.export, report.z221, "221")
    return t
