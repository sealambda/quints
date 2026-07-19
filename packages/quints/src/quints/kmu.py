"""Statutory financial statements grouped by the Swiss KMU chart of accounts.

Bilanz (balance sheet, OR Art. 959a) and Erfolgsrechnung (income statement,
OR Art. 959b) are produced by aggregating ledger accounts on the ``kmu:``
metadata of their ``open`` directives (enforced by ``quints.plugins.kmu``).
The statutory structure and all labels are static tables in this module;
language is purely a rendering concern (``lang="en"`` or ``"de"``).

Valuation:

- Balance-sheet positions: non-CHF balances are converted at the report-date
  rate (latest BAZG rate on or before ``--at``) — the same method Fava uses
  for market value, so totals tie out.
- Income-statement flows: converted at each transaction's date.
- The difference between the balance sheet's balancing result and the income
  statement's result is therefore the **unrealized currency translation** not
  yet booked (Plan 1.2's year-end revaluation makes it explicit); compute
  exposes it instead of hiding it.

Computation (`compute_bilanz` / `compute_erfolg` / `compute_konten`) is
separated from presentation (`render_*`) so web/TUI/MCP front-ends can consume
the dataclasses directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from beancount.core import convert as bc_convert
from beancount.core import data
from beancount.core import prices as bc_prices
from beancount.core.amount import Amount
from beancount.core.inventory import Inventory
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, ledger, ui

# ── static structure tables ──────────────────────────────────────────────────
# KMU code → name, per language. Only codes in use (or expected) are listed;
# an unknown code renders as the code itself, never an error.
KMU_NAMES: dict[str, dict[str, str]] = {
    "1020": {"en": "Bank deposits", "de": "Bankguthaben"},
    "1100": {"en": "Trade receivables", "de": "Forderungen aus Lieferungen und Leistungen"},
    "1140": {"en": "Receivables from shareholders", "de": "Forderungen gegenüber Beteiligten"},
    "1170": {"en": "Input VAT", "de": "Vorsteuer"},
    "1510": {"en": "Furniture and equipment", "de": "Mobiliar und Einrichtungen"},
    "2000": {"en": "Trade payables", "de": "Verbindlichkeiten aus Lieferungen und Leistungen"},
    "2200": {"en": "VAT due", "de": "Geschuldete Mehrwertsteuer"},
    "2270": {"en": "Social security payable", "de": "Geschuldete Sozialversicherungsbeiträge"},
    "2300": {"en": "Accrued expenses", "de": "Passive Rechnungsabgrenzung"},
    "2500": {
        "en": "Liabilities to shareholders",
        "de": "Verbindlichkeiten gegenüber Gesellschaftern",
    },
    "2800": {"en": "Share capital", "de": "Stammkapital"},
    "2820": {
        "en": "Capital contributions and withdrawals",
        "de": "Kapitaleinlagen und Kapitalrückzüge",
    },
    "2850": {"en": "Private account", "de": "Privat"},
    "2891": {"en": "Profit or loss for the year", "de": "Jahresgewinn oder Jahresverlust"},
    "2900": {"en": "Statutory capital reserve", "de": "Gesetzliche Kapitalreserve"},
    "2950": {"en": "Statutory retained earnings", "de": "Gesetzliche Gewinnreserve"},
    "2970": {"en": "Retained earnings", "de": "Gewinnvortrag oder Verlustvortrag"},
    "2979": {"en": "Profit or loss for the year", "de": "Jahresgewinn oder Jahresverlust"},
    "3400": {"en": "Revenue from services", "de": "Dienstleistungserlöse"},
    "4400": {"en": "Purchased services", "de": "Aufwand für bezogene Dienstleistungen"},
    "5000": {"en": "Wages and salaries", "de": "Lohnaufwand"},
    "5700": {"en": "Social security expenses", "de": "Sozialversicherungsaufwand"},
    "6500": {"en": "Administrative expenses", "de": "Verwaltungsaufwand"},
    "6530": {"en": "Accounting and consulting fees", "de": "Buchführungs- und Beratungsaufwand"},
    "6570": {"en": "IT expenses", "de": "Informatikaufwand"},
    "6600": {"en": "Advertising", "de": "Werbeaufwand"},
    "6620": {"en": "Commissions", "de": "Provisionen"},
    "6640": {"en": "Travel expenses", "de": "Reisespesen"},
    "6800": {"en": "Depreciation", "de": "Abschreibungen"},
    "6900": {"en": "Financial expenses", "de": "Finanzaufwand"},
    "6940": {"en": "Bank charges", "de": "Bankspesen"},
    "6950": {"en": "Financial income", "de": "Finanzertrag"},
}

# Klasse 28 is the one place the official KMU Kontenrahmen differs per legal
# form (veb.ch prints three variants: juristische Personen, Einzelunternehmen,
# Personengesellschaft). These overlays adapt the shared code 2800 and the
# statutory equity row; every other code is form-independent. Keys are
# config.LEGAL_FORMS keys; the base tables are the GmbH reading.
KMU_NAMES_BY_FORM: dict[str, dict[str, dict[str, str]]] = {
    "ag": {"2800": {"en": "Share capital", "de": "Aktienkapital"}},
    "einzelfirma": {"2800": {"en": "Owner's equity", "de": "Eigenkapital"}},
}

LABELS_BY_FORM: dict[str, dict[str, dict[str, str]]] = {
    "ag": {"share_capital": {"en": "Share capital", "de": "Aktienkapital"}},
    "einzelfirma": {"share_capital": {"en": "Owner's equity", "de": "Eigenkapital"}},
}

# Statutory rows: (row key, code range lo..hi inclusive). A KMU code belongs to
# the first row whose range contains it. Section membership below.
BILANZ_ROWS: tuple[tuple[str, str, str], ...] = (
    ("cash", "1000", "1099"),
    ("trade_receivables", "1100", "1139"),
    ("other_receivables", "1140", "1199"),
    ("inventories", "1200", "1299"),
    ("prepaid_expenses", "1300", "1399"),
    ("financial_assets", "1400", "1499"),
    ("tangible_assets", "1500", "1699"),
    ("intangible_assets", "1700", "1799"),
    ("trade_payables", "2000", "2099"),
    ("short_term_debt", "2100", "2199"),
    ("other_short_term", "2200", "2299"),
    ("accrued_liabilities", "2300", "2399"),
    ("long_term_debt", "2400", "2499"),
    ("other_long_term", "2500", "2599"),
    ("provisions", "2600", "2699"),
    ("share_capital", "2800", "2899"),
    ("reserves", "2900", "2969"),
    ("retained_earnings", "2970", "2979"),
)

BILANZ_SECTIONS: dict[str, tuple[str, ...]] = {
    "current_assets": (
        "cash",
        "trade_receivables",
        "other_receivables",
        "inventories",
        "prepaid_expenses",
    ),
    "noncurrent_assets": ("financial_assets", "tangible_assets", "intangible_assets"),
    "short_term_liabilities": (
        "trade_payables",
        "short_term_debt",
        "other_short_term",
        "accrued_liabilities",
    ),
    "long_term_liabilities": ("long_term_debt", "other_long_term", "provisions"),
    "equity": ("share_capital", "reserves", "retained_earnings"),
}

ERFOLG_ROWS: tuple[tuple[str, str, str], ...] = (
    ("revenue", "3000", "3999"),
    ("materials_services", "4000", "4999"),
    ("personnel", "5000", "5999"),
    ("other_operating", "6000", "6799"),
    ("depreciation", "6800", "6899"),
    ("financial_expenses", "6900", "6949"),
    ("financial_income", "6950", "6999"),
)

# Rows whose natural beancount sign is credit (display flipped).
CREDIT_ROWS = frozenset(
    {
        "trade_payables",
        "short_term_debt",
        "other_short_term",
        "accrued_liabilities",
        "long_term_debt",
        "other_long_term",
        "provisions",
        "share_capital",
        "reserves",
        "retained_earnings",
        "revenue",
        "financial_income",
    }
)

LABELS: dict[str, dict[str, str]] = {
    "bilanz_title": {"en": "Balance sheet", "de": "Bilanz"},
    "erfolg_title": {"en": "Income statement", "de": "Erfolgsrechnung"},
    "konten_title": {"en": "Account statements", "de": "Kontoblätter"},
    "as_at": {"en": "as at", "de": "per"},
    "assets": {"en": "ASSETS", "de": "AKTIVEN"},
    "liabilities_equity": {"en": "LIABILITIES AND EQUITY", "de": "PASSIVEN"},
    "current_assets": {"en": "Current assets", "de": "Umlaufvermögen"},
    "noncurrent_assets": {"en": "Non-current assets", "de": "Anlagevermögen"},
    "short_term_liabilities": {"en": "Short-term liabilities", "de": "Kurzfristiges Fremdkapital"},
    "long_term_liabilities": {"en": "Long-term liabilities", "de": "Langfristiges Fremdkapital"},
    "equity": {"en": "Equity", "de": "Eigenkapital"},
    "cash": {"en": "Cash and cash equivalents", "de": "Flüssige Mittel"},
    "trade_receivables": {
        "en": "Trade receivables",
        "de": "Forderungen aus Lieferungen und Leistungen",
    },
    "other_receivables": {
        "en": "Other current receivables",
        "de": "Übrige kurzfristige Forderungen",
    },
    "inventories": {"en": "Inventories", "de": "Vorräte"},
    "prepaid_expenses": {"en": "Prepaid expenses", "de": "Aktive Rechnungsabgrenzung"},
    "financial_assets": {"en": "Financial assets", "de": "Finanzanlagen"},
    "tangible_assets": {"en": "Tangible fixed assets", "de": "Sachanlagen"},
    "intangible_assets": {"en": "Intangible assets", "de": "Immaterielle Werte"},
    "trade_payables": {
        "en": "Trade payables",
        "de": "Verbindlichkeiten aus Lieferungen und Leistungen",
    },
    "short_term_debt": {
        "en": "Short-term interest-bearing debt",
        "de": "Kurzfristige verzinsliche Verbindlichkeiten",
    },
    "other_short_term": {
        "en": "Other short-term liabilities",
        "de": "Übrige kurzfristige Verbindlichkeiten",
    },
    "accrued_liabilities": {"en": "Accrued liabilities", "de": "Passive Rechnungsabgrenzung"},
    "long_term_debt": {
        "en": "Long-term interest-bearing debt",
        "de": "Langfristige verzinsliche Verbindlichkeiten",
    },
    "other_long_term": {
        "en": "Other long-term liabilities",
        "de": "Übrige langfristige Verbindlichkeiten",
    },
    "provisions": {"en": "Provisions", "de": "Rückstellungen"},
    "share_capital": {"en": "Share capital", "de": "Stammkapital"},
    "reserves": {"en": "Reserves", "de": "Reserven"},
    "retained_earnings": {"en": "Retained earnings", "de": "Gewinnvortrag"},
    "retained_prior": {"en": "Profit/loss brought forward", "de": "Gewinn-/Verlustvortrag"},
    "result": {"en": "Profit/loss for the year", "de": "Jahresgewinn/-verlust"},
    "total_assets": {"en": "Total assets", "de": "Total Aktiven"},
    "total_liabilities_equity": {"en": "Total liabilities and equity", "de": "Total Passiven"},
    "revenue": {
        "en": "Net revenue from services",
        "de": "Nettoerlöse aus Lieferungen und Leistungen",
    },
    "materials_services": {
        "en": "Cost of materials and purchased services",
        "de": "Material- und Dienstleistungsaufwand",
    },
    "personnel": {"en": "Personnel expenses", "de": "Personalaufwand"},
    "other_operating": {"en": "Other operating expenses", "de": "Übriger betrieblicher Aufwand"},
    "depreciation": {
        "en": "Depreciation and value adjustments",
        "de": "Abschreibungen und Wertberichtigungen",
    },
    "ebit": {"en": "Operating result (EBIT)", "de": "Betriebsergebnis (EBIT)"},
    "financial_expenses": {"en": "Financial expenses", "de": "Finanzaufwand"},
    "financial_income": {"en": "Financial income", "de": "Finanzertrag"},
    "unrealized_fx": {
        "en": "Unrealized currency translation (not booked)",
        "de": "Nicht verbuchte Kursdifferenzen (unrealisiert)",
    },
    "period": {"en": "Period", "de": "Periode"},
}


def label(key: str, lang: str, form: str | None = None) -> str:
    entry = LABELS_BY_FORM.get(form or "", {}).get(key) or LABELS.get(key)
    if entry:
        return entry.get(lang) or entry["en"]
    return key


def kmu_name(code: str, lang: str, form: str | None = None) -> str:
    entry = KMU_NAMES_BY_FORM.get(form or "", {}).get(code) or KMU_NAMES.get(code)
    if entry:
        return entry.get(lang) or entry["en"]
    return code


def _row_for(code: str, rows: tuple[tuple[str, str, str], ...]) -> str | None:
    for key, lo, hi in rows:
        if lo <= code <= hi:
            return key
    return None


# ── data ─────────────────────────────────────────────────────────────────────


@dataclass
class CodeLine:
    code: str
    amount: Decimal  # display sign (positive in its natural direction)
    accounts: list[str]


@dataclass
class RowLine:
    key: str
    amount: Decimal
    codes: list[CodeLine] = field(default_factory=list)


@dataclass
class BilanzReport:
    at: str
    current_assets: list[RowLine]
    noncurrent_assets: list[RowLine]
    short_term_liabilities: list[RowLine]
    long_term_liabilities: list[RowLine]
    equity: list[RowLine]
    retained_prior: Decimal  # unbooked P&L of years before the report year (Gewinnvortrag)
    result: Decimal  # report-year share of the balancing figure (Jahresgewinn)
    total_assets: Decimal
    total_liabilities_equity: Decimal
    converted: dict[str, Decimal] = field(
        default_factory=dict
    )  # ccy → units valued at report-date rate
    legal_form: str = "gmbh"  # picks the Klasse-28 label variant when rendering


@dataclass
class ErfolgReport:
    date_from: str
    date_to: str
    revenue: list[RowLine]
    expenses: list[RowLine]  # materials..depreciation, statutory order
    ebit: Decimal
    financial_expenses: list[RowLine]
    financial_income: list[RowLine]
    result: Decimal


@dataclass
class KontoLine:
    date: str
    payee: str
    narration: str
    account: str
    original: Decimal
    currency: str
    chf: Decimal


@dataclass
class Konto:
    code: str
    lines: list[KontoLine]
    flow: Decimal  # CHF, natural display sign


@dataclass
class KontenReport:
    date_from: str
    date_to: str
    konten: list[Konto]


# ── compute ──────────────────────────────────────────────────────────────────


def kmu_map(entries: data.Entries, marker: str) -> dict[str, str]:
    """account → kmu code, from the entity's open directives."""
    mapping: dict[str, str] = {}
    for e in entries:
        if isinstance(e, data.Open) and marker in e.account:
            code = (e.meta or {}).get("kmu")
            if isinstance(code, str):
                mapping[e.account] = code
    return mapping


def _to_chf(units: Amount, on: Date, price_map: bc_prices.PriceMap) -> Decimal:
    if units.number is None:  # incomplete amount — cannot occur in a loaded ledger
        return Decimal("0")
    if units.currency == "CHF":
        return units.number
    conv = bc_convert.convert_amount(units, "CHF", price_map, date=on)
    if conv.currency == "CHF" and conv.number is not None:
        return conv.number
    return Decimal("0")


def _posting_chf(
    p: data.Posting, on: Date, fallback: Date, price_map: bc_prices.PriceMap
) -> Decimal:
    """CHF value of a P&L posting at its transaction date.

    The posting's own ``@``/``@@`` annotation wins (it is the booked rate);
    otherwise the price map at ``on``, then at ``fallback`` — old entries may
    predate the BAZG price coverage, and silently valuing them at 0 would
    corrupt totals.
    """
    weight = bc_convert.get_weight(p)
    if weight.number is None:  # incomplete posting — cannot occur in a loaded ledger
        return Decimal("0")
    if weight.currency == "CHF":
        return weight.number
    conv = bc_convert.convert_amount(weight, "CHF", price_map, date=on)
    if conv.currency == "CHF" and conv.number is not None:
        return conv.number
    conv = bc_convert.convert_amount(weight, "CHF", price_map, date=fallback)
    if conv.currency == "CHF" and conv.number is not None:
        return conv.number
    return Decimal("0")


def _rows_from(
    per_code: dict[str, tuple[Decimal, set[str]]],
    structure: tuple[tuple[str, str, str], ...],
    keys: tuple[str, ...],
) -> list[RowLine]:
    """Group per-code totals into statutory rows (only ``keys``), display signs."""
    rows: dict[str, RowLine] = {}
    for code in sorted(per_code):
        key = _row_for(code, structure)
        if key not in keys:
            continue
        amount, accounts = per_code[code]
        display = ledger.rappen(-amount if key in CREDIT_ROWS else amount)
        row = rows.setdefault(key, RowLine(key, Decimal("0")))
        row.amount += display
        row.codes.append(CodeLine(code, display, sorted(accounts)))
    return [rows[k] for k in keys if k in rows]


def compute_bilanz(ledger_path: Path, at: str, cfg: config.Config | None = None) -> BilanzReport:
    cfg = cfg or config.get()
    on = Date.fromisoformat(at)
    entries, _ = ledger.load_entries(ledger_path)
    price_map = bc_prices.build_price_map(entries)
    mapping = kmu_map(entries, cfg.entity_marker)

    fy_start = Date(on.year, 1, 1)
    inventories: dict[str, Inventory] = {}
    prior_flows = Decimal("0")  # P&L of years before the report year, at txn-date rates
    for e in entries:
        if not isinstance(e, data.Transaction) or e.date > on:
            continue
        for p in e.postings:
            if p.account not in mapping:
                continue
            root = p.account.split(":", 1)[0]
            if root in ("Assets", "Liabilities", "Equity"):
                inventories.setdefault(p.account, Inventory()).add_position(p)
            elif e.date < fy_start:
                prior_flows += _posting_chf(p, e.date, on, price_map)

    per_code: dict[str, tuple[Decimal, set[str]]] = {}
    converted: dict[str, Decimal] = {}
    for account, inv in inventories.items():
        total = Decimal("0")
        for pos in inv:
            chf = _to_chf(pos.units, on, price_map)
            total += chf
            if pos.units.currency != "CHF" and pos.units.number:
                converted[pos.units.currency] = (
                    converted.get(pos.units.currency, Decimal("0")) + pos.units.number
                )
        code = mapping[account]
        amount, accounts = per_code.get(code, (Decimal("0"), set()))
        per_code[code] = (amount + total, accounts | {account})

    assets_rows = _rows_from(
        per_code,
        BILANZ_ROWS,
        BILANZ_SECTIONS["current_assets"] + BILANZ_SECTIONS["noncurrent_assets"],
    )
    current = [r for r in assets_rows if r.key in BILANZ_SECTIONS["current_assets"]]
    noncurrent = [r for r in assets_rows if r.key in BILANZ_SECTIONS["noncurrent_assets"]]
    short = _rows_from(per_code, BILANZ_ROWS, BILANZ_SECTIONS["short_term_liabilities"])
    long_ = _rows_from(per_code, BILANZ_ROWS, BILANZ_SECTIONS["long_term_liabilities"])
    equity = _rows_from(per_code, BILANZ_ROWS, BILANZ_SECTIONS["equity"])

    total_assets = sum((r.amount for r in current + noncurrent), Decimal("0"))
    liabilities_and_equity = sum((r.amount for r in short + long_ + equity), Decimal("0"))
    # Balancing figure = all not-yet-closed P&L since inception. Split it into
    # prior years (Gewinnvortrag) and the report year (Jahresgewinn); the report
    # year's share absorbs the unrealized FX of report-date valuation.
    balancing = total_assets - liabilities_and_equity
    retained_prior = ledger.rappen(-prior_flows)
    result = ledger.rappen(balancing) - retained_prior

    return BilanzReport(
        at=at,
        current_assets=current,
        noncurrent_assets=noncurrent,
        short_term_liabilities=short,
        long_term_liabilities=long_,
        equity=equity,
        retained_prior=retained_prior,
        result=result,
        total_assets=ledger.rappen(total_assets),
        total_liabilities_equity=ledger.rappen(liabilities_and_equity) + retained_prior + result,
        converted={c: ledger.rappen(v) for c, v in converted.items()},
        legal_form=cfg.legal_form,
    )


def compute_erfolg(
    ledger_path: Path, date_from: str, date_to: str, cfg: config.Config | None = None
) -> ErfolgReport:
    cfg = cfg or config.get()
    d0, d1 = Date.fromisoformat(date_from), Date.fromisoformat(date_to)
    entries, _ = ledger.load_entries(ledger_path)
    price_map = bc_prices.build_price_map(entries)
    mapping = kmu_map(entries, cfg.entity_marker)

    per_code: dict[str, tuple[Decimal, set[str]]] = {}
    for e in entries:
        if not isinstance(e, data.Transaction) or not (d0 <= e.date <= d1):
            continue
        for p in e.postings:
            if p.account not in mapping:
                continue
            if p.account.split(":", 1)[0] not in ("Income", "Expenses"):
                continue
            chf = _posting_chf(p, e.date, d1, price_map)
            code = mapping[p.account]
            amount, accounts = per_code.get(code, (Decimal("0"), set()))
            per_code[code] = (amount + chf, accounts | {p.account})

    revenue = _rows_from(per_code, ERFOLG_ROWS, ("revenue",))
    expenses = _rows_from(
        per_code,
        ERFOLG_ROWS,
        ("materials_services", "personnel", "other_operating", "depreciation"),
    )
    fin_exp = _rows_from(per_code, ERFOLG_ROWS, ("financial_expenses",))
    fin_inc = _rows_from(per_code, ERFOLG_ROWS, ("financial_income",))

    revenue_total = sum((r.amount for r in revenue), Decimal("0"))
    expense_total = sum((r.amount for r in expenses), Decimal("0"))
    ebit = ledger.rappen(revenue_total - expense_total)
    result = ledger.rappen(
        ebit
        - sum((r.amount for r in fin_exp), Decimal("0"))
        + sum((r.amount for r in fin_inc), Decimal("0"))
    )
    return ErfolgReport(
        date_from=date_from,
        date_to=date_to,
        revenue=revenue,
        expenses=expenses,
        ebit=ebit,
        financial_expenses=fin_exp,
        financial_income=fin_inc,
        result=result,
    )


def compute_konten(
    ledger_path: Path, date_from: str, date_to: str, cfg: config.Config | None = None
) -> KontenReport:
    cfg = cfg or config.get()
    d0, d1 = Date.fromisoformat(date_from), Date.fromisoformat(date_to)
    entries, _ = ledger.load_entries(ledger_path)
    price_map = bc_prices.build_price_map(entries)
    mapping = kmu_map(entries, cfg.entity_marker)

    konten: dict[str, Konto] = {}
    for e in entries:
        if not isinstance(e, data.Transaction) or not (d0 <= e.date <= d1):
            continue
        for p in e.postings:
            code = mapping.get(p.account)
            if code is None or p.units is None or p.units.number is None:
                continue
            chf = _posting_chf(p, e.date, d1, price_map)
            konto = konten.setdefault(code, Konto(code, [], Decimal("0")))
            konto.lines.append(
                KontoLine(
                    str(e.date),
                    e.payee or "",
                    e.narration or "",
                    p.account,
                    p.units.number,
                    p.units.currency,
                    chf,
                )
            )
            konto.flow += chf
    return KontenReport(date_from, date_to, [konten[c] for c in sorted(konten)])


# ── render ───────────────────────────────────────────────────────────────────


def _statement_table(lang: str) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, pad_edge=False, expand=False, show_header=False)
    t.add_column("KMU", justify="right", style="ziffer", no_wrap=True)
    t.add_column("Position", min_width=44)
    t.add_column("CHF", justify="right", no_wrap=True)
    return t


def _add_rows(t: Table, rows: list[RowLine], lang: str, form: str | None = None) -> None:
    for row in rows:
        t.add_row("", label(row.key, lang, form), ui.money(row.amount))
        for cl in row.codes:
            t.add_row(
                cl.code,
                f"[muted]{kmu_name(cl.code, lang, form)}[/]",
                f"[muted]{ui.money(cl.amount)}[/]",
            )


def render_bilanz(
    report: BilanzReport,
    lang: str = "en",
    console: Console | None = None,
    cfg: config.Config | None = None,
) -> None:
    console = console or ui.console
    console.print()
    console.rule(f"[bold]{label('bilanz_title', lang)}[/]   {label('as_at', lang)} {report.at}")
    console.print(
        f"{(cfg or config.get()).entity_name} · OR Art. 959a · "
        f"{(cfg or config.get()).operating_currency}",
        style="muted",
        justify="center",
    )
    console.print()

    t = _statement_table(lang)
    t.add_row("", f"[bold]{label('assets', lang)}[/]", "")
    for section in ("current_assets", "noncurrent_assets"):
        rows = getattr(report, section)
        if not rows:
            continue
        t.add_section()
        _add_rows(t, rows, lang, report.legal_form)
        t.add_row(
            "",
            f"[bold]{label(section, lang)}[/]",
            f"[bold]{ui.money(sum((r.amount for r in rows), Decimal('0')))}[/]",
        )
    t.add_section()
    t.add_row(
        "", f"[bold]{label('total_assets', lang)}[/]", f"[bold]{ui.money(report.total_assets)}[/]"
    )

    t.add_section()
    t.add_row("", f"[bold]{label('liabilities_equity', lang)}[/]", "")
    for section in ("short_term_liabilities", "long_term_liabilities", "equity"):
        rows = getattr(report, section)
        if not rows and section != "equity":
            continue
        t.add_section()
        _add_rows(t, rows, lang, report.legal_form)
        total = sum((r.amount for r in rows), Decimal("0"))
        if section == "equity":
            if report.retained_prior:
                t.add_row("", label("retained_prior", lang), ui.money(report.retained_prior))
                total += report.retained_prior
            t.add_row("", label("result", lang), ui.money(report.result))
            total += report.result
        t.add_row("", f"[bold]{label(section, lang)}[/]", f"[bold]{ui.money(total)}[/]")
    t.add_section()
    t.add_row(
        "",
        f"[bold]{label('total_liabilities_equity', lang)}[/]",
        f"[bold]{ui.money(report.total_liabilities_equity)}[/]",
    )
    console.print(t)

    if report.converted:
        parts = ", ".join(f"{ui.money(v)} {c}" for c, v in sorted(report.converted.items()))
        note = {
            "en": f"Non-CHF balances valued at the {report.at} rate: {parts}.",
            "de": f"Fremdwährungsbestände zum Kurs per {report.at} bewertet: {parts}.",
        }
        console.print(note.get(lang, note["en"]), style="muted")
    console.print()


def render_erfolg(
    report: ErfolgReport,
    lang: str = "en",
    console: Console | None = None,
    cfg: config.Config | None = None,
) -> None:
    console = console or ui.console
    console.print()
    console.rule(f"[bold]{label('erfolg_title', lang)}[/]   {report.date_from} – {report.date_to}")
    console.print(
        f"{(cfg or config.get()).entity_name} · OR Art. 959b · "
        f"{(cfg or config.get()).operating_currency}",
        style="muted",
        justify="center",
    )
    console.print()

    t = _statement_table(lang)
    _add_rows(t, report.revenue, lang)
    t.add_section()
    for row in report.expenses:
        t.add_row("", label(row.key, lang), ui.money(-row.amount))
        for cl in row.codes:
            t.add_row(
                cl.code, f"[muted]{kmu_name(cl.code, lang)}[/]", f"[muted]{ui.money(-cl.amount)}[/]"
            )
    t.add_section()
    t.add_row("", f"[bold]{label('ebit', lang)}[/]", f"[bold]{ui.money(report.ebit)}[/]")
    t.add_section()
    for row in report.financial_expenses:
        _add_rows(
            t,
            [
                RowLine(
                    row.key,
                    -row.amount,
                    [CodeLine(c.code, -c.amount, c.accounts) for c in row.codes],
                )
            ],
            lang,
        )
    for row in report.financial_income:
        _add_rows(t, [row], lang)
    t.add_section()
    style = "refund" if report.result >= 0 else "owe"
    t.add_row(
        "",
        f"[bold {style}]{label('result', lang)}[/]",
        f"[bold {style}]{ui.money(report.result)}[/]",
    )
    console.print(t)
    console.print()


def render_konten(report: KontenReport, lang: str = "en", console: Console | None = None) -> None:
    console = console or ui.console
    console.print()
    console.rule(f"[bold]{label('konten_title', lang)}[/]   {report.date_from} – {report.date_to}")
    for konto in report.konten:
        console.print()
        t = Table(
            box=box.SIMPLE,
            title=f"{konto.code} — {kmu_name(konto.code, lang)}",
            title_justify="left",
            title_style="bold",
        )
        t.add_column("Datum" if lang == "de" else "Date", style="muted", no_wrap=True)
        t.add_column("Payee")
        t.add_column("Narration", max_width=40)
        t.add_column("Konto" if lang == "de" else "Account", style="muted")
        t.add_column("Original", justify="right", no_wrap=True)
        t.add_column("CHF", justify="right", no_wrap=True)
        for line in konto.lines:
            t.add_row(
                line.date,
                line.payee,
                line.narration,
                line.account.replace("Assets:CH:GmbH:", "")
                .replace("Liabilities:CH:GmbH:", "")
                .replace("Income:CH:GmbH:", "")
                .replace("Expenses:CH:GmbH:", "")
                .replace("Equity:CH:GmbH:", ""),
                f"{line.original:,.2f} {line.currency}",
                ui.money(line.chf),
            )
        t.add_section()
        t.add_row("", "", "[bold]Total[/]", "", "", f"[bold]{ui.money(konto.flow)}[/]")
        console.print(t)
    console.print()
