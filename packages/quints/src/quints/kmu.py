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
from beancount.core.inventory import Inventory
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, ledger, ui

# ── static structure tables ──────────────────────────────────────────────────
# KMU code → name, per language. Only codes in use (or expected) are listed;
# an unknown code renders as the code itself, never an error. The 15xx block
# (mobile Sachanlagen and their Wertberichtigungen) follows the official
# Schulversion of the veb.ch Schweizer Kontenrahmen KMU: an x9 code is the
# contra account of the asset code before it, which is how indirect
# depreciation (`quints close depreciation`) stays inside one Bilanz row.
KMU_NAMES: dict[str, dict[str, str]] = {
    "1020": {"en": "Bank deposits", "de": "Bankguthaben"},
    "1100": {"en": "Trade receivables", "de": "Forderungen aus Lieferungen und Leistungen"},
    "1140": {"en": "Receivables from shareholders", "de": "Forderungen gegenüber Beteiligten"},
    "1170": {"en": "Input VAT", "de": "Vorsteuer"},
    "1480": {"en": "Participations", "de": "Beteiligungen"},
    "1500": {"en": "Machinery and apparatus", "de": "Maschinen und Apparate"},
    "1509": {
        "en": "Valuation allowance, machinery and apparatus",
        "de": "Wertberichtigungen Maschinen und Apparate",
    },
    "1510": {"en": "Furniture and equipment", "de": "Mobiliar und Einrichtungen"},
    "1519": {
        "en": "Valuation allowance, furniture and equipment",
        "de": "Wertberichtigungen Mobiliar und Einrichtungen",
    },
    "1520": {
        "en": "Office machines, IT, communications",
        "de": "Büromaschinen, Informatik, Kommunikationstechnologie",
    },
    "1529": {
        "en": "Valuation allowance, office machines and IT",
        "de": "Wertberichtigungen Büromaschinen, Informatik, Kommunikationstechnologie",
    },
    "1530": {"en": "Vehicles", "de": "Fahrzeuge"},
    "1539": {"en": "Valuation allowance, vehicles", "de": "Wertberichtigungen Fahrzeuge"},
    "1540": {"en": "Tools and equipment", "de": "Werkzeuge und Geräte"},
    "1549": {
        "en": "Valuation allowance, tools and equipment",
        "de": "Wertberichtigungen Werkzeuge und Geräte",
    },
    "1850": {
        "en": "Unpaid share capital",
        "de": "Nicht einbezahltes Aktien-, Stamm-, Anteilschein- oder Stiftungskapital",
    },
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
    "2955": {"en": "Revaluation reserve", "de": "Aufwertungsreserve"},
    "2960": {"en": "Voluntary retained earnings", "de": "Freiwillige Gewinnreserven"},
    "2965": {"en": "Own shares (deducted)", "de": "Eigene Kapitalanteile (Minusposten)"},
    "2970": {"en": "Retained earnings", "de": "Gewinnvortrag oder Verlustvortrag"},
    "2979": {"en": "Profit or loss for the year", "de": "Jahresgewinn oder Jahresverlust"},
    "3400": {"en": "Revenue from services", "de": "Dienstleistungserlöse"},
    "3600": {
        "en": "Other revenue from goods and services",
        "de": "Übrige Erlöse aus Lieferungen und Leistungen",
    },
    "4400": {"en": "Purchased services", "de": "Aufwand für bezogene Dienstleistungen"},
    "5000": {"en": "Wages and salaries", "de": "Lohnaufwand"},
    "5700": {"en": "Social security expenses", "de": "Sozialversicherungsaufwand"},
    "6500": {"en": "Administrative expenses", "de": "Verwaltungsaufwand"},
    "6530": {"en": "Accounting and consulting fees", "de": "Buchführungs- und Beratungsaufwand"},
    "6570": {"en": "IT expenses", "de": "Informatikaufwand"},
    "6600": {"en": "Advertising", "de": "Werbeaufwand"},
    "6700": {"en": "Other operating expenses", "de": "Sonstiger betrieblicher Aufwand"},
    "6620": {"en": "Commissions", "de": "Provisionen"},
    "6640": {"en": "Travel expenses", "de": "Reisespesen"},
    "6800": {"en": "Depreciation", "de": "Abschreibungen"},
    "6900": {"en": "Financial expenses", "de": "Finanzaufwand"},
    "6940": {"en": "Bank charges", "de": "Bankspesen"},
    "6950": {"en": "Financial income", "de": "Finanzertrag"},
    "7000": {"en": "Ancillary business income", "de": "Ertrag Nebenbetrieb"},
    "7010": {"en": "Ancillary business expenses", "de": "Aufwand Nebenbetrieb"},
    "7500": {"en": "Operating property income", "de": "Ertrag betriebliche Liegenschaft"},
    "7510": {"en": "Operating property expenses", "de": "Aufwand betriebliche Liegenschaft"},
    "8000": {"en": "Non-operating expenses", "de": "Betriebsfremder Aufwand"},
    "8100": {"en": "Non-operating income", "de": "Betriebsfremder Ertrag"},
    "8500": {
        "en": "Extraordinary, non-recurring or prior-period expenses",
        "de": "Ausserordentlicher, einmaliger oder periodenfremder Aufwand",
    },
    "8510": {
        "en": "Extraordinary, non-recurring or prior-period income",
        "de": "Ausserordentlicher, einmaliger oder periodenfremder Ertrag",
    },
    "8900": {"en": "Direct taxes", "de": "Direkte Steuern"},
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
    ("financial_assets", "1400", "1479"),
    ("participations", "1480", "1499"),
    ("tangible_assets", "1500", "1699"),
    ("intangible_assets", "1700", "1799"),
    ("unpaid_capital", "1800", "1899"),
    ("trade_payables", "2000", "2099"),
    ("short_term_debt", "2100", "2199"),
    ("other_short_term", "2200", "2299"),
    ("accrued_liabilities", "2300", "2399"),
    ("long_term_debt", "2400", "2499"),
    ("other_long_term", "2500", "2599"),
    ("provisions", "2600", "2699"),
    ("share_capital", "2800", "2899"),
    ("capital_reserve", "2900", "2949"),
    ("retained_reserve", "2950", "2954"),
    ("revaluation_reserve", "2955", "2959"),  # Art. 725c: shown under the Gewinnreserve
    ("voluntary_reserves", "2960", "2964"),
    ("own_shares", "2965", "2969"),  # a Minusposten (Art. 959a Abs. 2 Ziff. 3 lit. e)
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
    "noncurrent_assets": (
        "financial_assets",
        "participations",
        "tangible_assets",
        "intangible_assets",
        "unpaid_capital",
    ),
    "short_term_liabilities": (
        "trade_payables",
        "short_term_debt",
        "other_short_term",
        "accrued_liabilities",
    ),
    "long_term_liabilities": ("long_term_debt", "other_long_term", "provisions"),
    "equity": (
        "share_capital",
        "capital_reserve",
        "retained_reserve",
        "revaluation_reserve",
        "voluntary_reserves",
        "own_shares",
        "retained_earnings",
    ),
}

# Art. 959b Abs. 2 OR (Gesamtkostenverfahren), in its order, on the classes of
# the Schweizer Kontenrahmen KMU (Schulversion, SwissAccounting): 39xx
# Bestandesänderungen, 7 betrieblicher Nebenerfolg (7000/7010, 7500/7510), 8000
# betriebsfremder Aufwand, 8100 Ertrag, 8500 ausserordentlicher, einmaliger
# oder periodenfremder Aufwand, 8510 Ertrag, 8900 direkte Steuern. A code in a
# gap (8200-8499, 8600-8899, class 9) is in no row — a violation, never dropped.
ERFOLG_ROWS: tuple[tuple[str, str, str], ...] = (
    ("revenue", "3000", "3899"),
    ("inventory_changes", "3900", "3999"),
    ("materials_services", "4000", "4999"),
    ("personnel", "5000", "5999"),
    ("other_operating", "6000", "6799"),
    ("depreciation", "6800", "6899"),
    ("financial_expenses", "6900", "6949"),
    ("financial_income", "6950", "6999"),
    ("ancillary", "7000", "7999"),
    ("non_operating_expenses", "8000", "8099"),
    ("non_operating_income", "8100", "8199"),
    ("extraordinary_expenses", "8500", "8509"),
    ("extraordinary_income", "8510", "8599"),
    ("direct_taxes", "8900", "8999"),
)
# Below the financial result, each row is shown as what it adds to the result
# (income positive, expense negative) — so all are credit-signed.
OTHER_RESULT_ROWS = (
    "ancillary",
    "non_operating_expenses",
    "non_operating_income",
    "extraordinary_expenses",
    "extraordinary_income",
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
        "capital_reserve",
        "retained_reserve",
        "revaluation_reserve",
        "voluntary_reserves",
        "own_shares",
        "retained_earnings",
        "revenue",
        "inventory_changes",
        "financial_income",
        *OTHER_RESULT_ROWS,
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
    "participations": {"en": "Participations", "de": "Beteiligungen"},
    "unpaid_capital": {
        "en": "Unpaid share capital",
        "de": "Nicht einbezahltes Grund-, Gesellschafter- oder Stiftungskapital",
    },
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
    "capital_reserve": {"en": "Statutory capital reserve", "de": "Gesetzliche Kapitalreserve"},
    "retained_reserve": {
        "en": "Statutory retained earnings reserve",
        "de": "Gesetzliche Gewinnreserve",
    },
    "revaluation_reserve": {"en": "Revaluation reserve", "de": "Aufwertungsreserve"},
    "voluntary_reserves": {"en": "Voluntary retained earnings", "de": "Freiwillige Gewinnreserven"},
    "own_shares": {"en": "Own shares", "de": "Eigene Kapitalanteile"},
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
    "inventory_changes": {
        "en": "Changes in inventories and unbilled services",
        "de": "Bestandesänderungen an unfertigen und fertigen Erzeugnissen sowie an "
        "nicht fakturierten Dienstleistungen",
    },
    "ebit": {"en": "Operating result (EBIT)", "de": "Betriebsergebnis (EBIT)"},
    "ebt": {
        "en": "Operating result before taxes (EBT)",
        "de": "Betriebliches Ergebnis vor Steuern (EBT)",
    },
    "ancillary": {"en": "Ancillary operating result", "de": "Betrieblicher Nebenerfolg"},
    "non_operating_expenses": {"en": "Non-operating expenses", "de": "Betriebsfremder Aufwand"},
    "non_operating_income": {"en": "Non-operating income", "de": "Betriebsfremder Ertrag"},
    "extraordinary_expenses": {
        "en": "Extraordinary, non-recurring or prior-period expenses",
        "de": "Ausserordentlicher, einmaliger oder periodenfremder Aufwand",
    },
    "extraordinary_income": {
        "en": "Extraordinary, non-recurring or prior-period income",
        "de": "Ausserordentlicher, einmaliger oder periodenfremder Ertrag",
    },
    "result_before_taxes": {
        "en": "Profit/loss before taxes",
        "de": "Jahresgewinn/-verlust vor Steuern",
    },
    "direct_taxes": {"en": "Direct taxes", "de": "Direkte Steuern"},
    "financial_expenses": {"en": "Financial expenses", "de": "Finanzaufwand"},
    "financial_income": {"en": "Financial income", "de": "Finanzertrag"},
    "unrealized_fx": {
        "en": "of which unrealized currency translation (not booked)",
        "de": "davon nicht verbuchte Kursdifferenzen (unrealisiert)",
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
    # A subtotal's positions (only `reserves`): shown, never added again.
    parts: list[RowLine] = field(default_factory=list)


@dataclass
class BilanzReport:
    at: str
    current_assets: list[RowLine]
    noncurrent_assets: list[RowLine]
    short_term_liabilities: list[RowLine]
    long_term_liabilities: list[RowLine]
    equity: list[RowLine]
    retained_prior: Decimal  # unbooked P&L before the fiscal year (Gewinnvortrag)
    result: Decimal  # the fiscal year's share of the balancing figure (Jahresgewinn)
    total_assets: Decimal
    total_liabilities_equity: Decimal
    converted: dict[str, Decimal] = field(
        default_factory=dict
    )  # ccy → units valued at report-date rate
    legal_form: str = "gmbh"  # picks the Klasse-28 label variant when rendering
    # Report-date value − book value of the non-CHF positions: the part of
    # `result` the Erfolgsrechnung doesn't show until the revaluation is booked.
    unrealized_fx: Decimal = Decimal("0")
    violations: list[ledger.Violation] = field(default_factory=list)


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
    # Art. 959b Abs. 2 Ziff. 8-10 OR: below the financial result.
    ebt: Decimal = Decimal("0")
    other_results: list[RowLine] = field(default_factory=list)  # signed: + income, − expense
    result_before_taxes: Decimal = Decimal("0")
    direct_taxes: list[RowLine] = field(default_factory=list)
    violations: list[ledger.Violation] = field(default_factory=list)


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
    violations: list[ledger.Violation] = field(default_factory=list)


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


def _posting_chf(
    p: data.Posting, on: Date, fallback: Date, price_map: bc_prices.PriceMap
) -> Decimal:
    """CHF value of a P&L posting at its transaction date.

    The posting's own ``@``/``@@`` annotation wins (it is the booked rate);
    otherwise the price map at ``on``, then at ``fallback`` — old entries may
    predate the BAZG price coverage. Raises :class:`ledger.RateUnavailable`
    when neither has a rate: valuing it at 0 would corrupt totals.
    """
    weight = bc_convert.get_weight(p)
    try:
        return ledger.to_chf(weight, on, price_map)
    except ledger.RateUnavailable:
        return ledger.to_chf(weight, fallback, price_map)


def _left_out(
    e: data.Transaction | None, what: str, exc: ledger.RateUnavailable
) -> ledger.Violation:
    """A figure with no rate to value it: listed, and left out of the totals."""
    return ledger.Violation(
        str(e.date) if e else "",
        (e.payee or "") if e else "",
        (e.narration or "") if e else "",
        f"{exc} — {what} is left out; run `quints prices sync`",
    )


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


def _unmapped(
    per_code: dict[str, tuple[Decimal, set[str]]],
    structure: tuple[tuple[str, str, str], ...],
    statement: str,
) -> list[ledger.Violation]:
    """Codes that land in no row of ``statement``: listed, never dropped quietly."""
    out: list[ledger.Violation] = []
    for code, (amount, accounts) in sorted(per_code.items()):
        if _row_for(code, structure) is None and ledger.rappen(amount):
            out.append(
                ledger.Violation(
                    "",
                    "",
                    "",
                    f"kmu: {code} ({', '.join(sorted(accounts))}) maps to no row of the "
                    f"{statement} — {ledger.rappen(amount)} CHF is left out; use a KMU "
                    "Kontenrahmen code",
                    Decimal("0"),
                    ledger.rappen(amount),
                )
            )
    return out


@dataclass
class _Positions:
    """The balance-sheet accounts as of the report date, and the P&L before the year."""

    inventories: dict[str, Inventory] = field(default_factory=dict)
    book: dict[str, Decimal] = field(default_factory=dict)  # CHF at the postings' dates
    unbooked: set[str] = field(default_factory=set)  # a posting no rate can value
    prior_flows: Decimal = Decimal("0")  # P&L before the fiscal year, at txn-date rates
    # What beancount's balancing tolerance left over: each transaction's
    # postings sum to within half a unit of the last digit, not to zero. It
    # stays in the balancing figure and never reaches the Erfolgsrechnung.
    residual: Decimal = Decimal("0")


def _positions(
    entries: data.Directives,
    mapping: dict[str, str],
    on: Date,
    fy_start: Date,
    price_map: bc_prices.PriceMap,
    problems: list[ledger.Violation],
) -> _Positions:
    pos = _Positions()
    for e in entries:
        if not isinstance(e, data.Transaction) or e.date > on:
            continue
        pos.residual += _residual(e, on, price_map)
        for p in e.postings:
            if p.account not in mapping:
                continue
            root = p.account.split(":", 1)[0]
            if root in ("Assets", "Liabilities", "Equity"):
                pos.inventories.setdefault(p.account, Inventory()).add_position(p)
                try:
                    pos.book[p.account] = pos.book.get(p.account, Decimal("0")) + _posting_chf(
                        p, e.date, on, price_map
                    )
                except ledger.RateUnavailable:
                    # No rate on or before `on` either, so the report-date
                    # valuation below fails too and lists this balance.
                    pos.unbooked.add(p.account)
            elif e.date < fy_start:
                try:
                    pos.prior_flows += _posting_chf(p, e.date, on, price_map)
                except ledger.RateUnavailable as exc:
                    problems.append(_left_out(e, f"{p.account} {p.units}", exc))
    return pos


def _residual(e: data.Transaction, on: Date, price_map: bc_prices.PriceMap) -> Decimal:
    """The CHF the transaction's postings leave over, all of them, coded or not."""
    try:
        return sum((_posting_chf(p, e.date, on, price_map) for p in e.postings), Decimal("0"))
    except ledger.RateUnavailable:
        # A leg no rate can value: the statements list it where they use it,
        # and its transaction can't be measured here.
        return Decimal("0")


def _value(
    pos: _Positions,
    mapping: dict[str, str],
    on: Date,
    price_map: bc_prices.PriceMap,
    problems: list[ledger.Violation],
) -> tuple[dict[str, tuple[Decimal, set[str]]], dict[str, Decimal], Decimal]:
    """(per-code CHF at ``on``, foreign units converted, report-date − book value)."""
    per_code: dict[str, tuple[Decimal, set[str]]] = {}
    converted: dict[str, Decimal] = {}
    market_minus_book = Decimal("0")
    for account, inv in pos.inventories.items():
        total = Decimal("0")
        valued = True
        for position in inv:
            units = position.units
            try:
                total += ledger.to_chf(units, on, price_map)
            except ledger.RateUnavailable as exc:
                problems.append(_left_out(None, f"the {account} balance {units}", exc))
                valued = False
                continue
            if units.currency != "CHF" and units.number:
                converted[units.currency] = (
                    converted.get(units.currency, Decimal("0")) + units.number
                )
        if valued and account not in pos.unbooked:
            market_minus_book += total - pos.book[account]
        code = mapping[account]
        amount, accounts = per_code.get(code, (Decimal("0"), set()))
        per_code[code] = (amount + total, accounts | {account})
    return per_code, converted, market_minus_book


# Codes 2900-2969, own shares included: one `reserves` row until Art. 959a
# split them, and still a stable JSON key — now the subtotal of its parts.
RESERVE_PARTS = (
    "capital_reserve",
    "retained_reserve",
    "revaluation_reserve",
    "voluntary_reserves",
    "own_shares",
)


def _reserves_subtotal(equity: list[RowLine]) -> list[RowLine]:
    """Fold the reserve positions into one ``reserves`` row carrying them as parts."""
    parts = [r for r in equity if r.key in RESERVE_PARTS]
    if not parts:
        return equity
    reserves = RowLine(
        "reserves",
        sum((p.amount for p in parts), Decimal("0")),
        [c for p in parts for c in p.codes],
        parts,
    )
    first = equity.index(parts[0])
    rest = [r for r in equity if r.key not in RESERVE_PARTS]
    return [*rest[:first], reserves, *rest[first:]]


def compute_bilanz(
    ledger_path: Path, at: str, cfg: config.Config | None = None, *, date_from: str | None = None
) -> BilanzReport:
    cfg = cfg or config.get()
    on = Date.fromisoformat(at)
    fy_start = Date.fromisoformat(date_from) if date_from else Date(on.year, 1, 1)
    if fy_start > on:
        raise ValueError(f"fiscal year start {fy_start} is after the balance-sheet date {on}")
    entries, problems = ledger.load(ledger_path)
    price_map = bc_prices.build_price_map(entries)
    mapping = kmu_map(entries, cfg.entity_marker)
    pos = _positions(entries, mapping, on, fy_start, price_map, problems)
    per_code, converted, market_minus_book = _value(pos, mapping, on, price_map, problems)
    problems += _unmapped(per_code, BILANZ_ROWS, "Bilanz")

    assets_rows = _rows_from(
        per_code,
        BILANZ_ROWS,
        BILANZ_SECTIONS["current_assets"] + BILANZ_SECTIONS["noncurrent_assets"],
    )
    current = [r for r in assets_rows if r.key in BILANZ_SECTIONS["current_assets"]]
    noncurrent = [r for r in assets_rows if r.key in BILANZ_SECTIONS["noncurrent_assets"]]
    short = _rows_from(per_code, BILANZ_ROWS, BILANZ_SECTIONS["short_term_liabilities"])
    long_ = _rows_from(per_code, BILANZ_ROWS, BILANZ_SECTIONS["long_term_liabilities"])
    equity = _reserves_subtotal(_rows_from(per_code, BILANZ_ROWS, BILANZ_SECTIONS["equity"]))

    total_assets = sum((r.amount for r in current + noncurrent), Decimal("0"))
    liabilities_and_equity = sum((r.amount for r in short + long_ + equity), Decimal("0"))
    # Balancing figure = all not-yet-closed P&L since inception. Split it into
    # prior years (Gewinnvortrag) and the fiscal year (Jahresgewinn); the fiscal
    # year's share absorbs the unrealized FX of report-date valuation.
    balancing = total_assets - liabilities_and_equity
    retained_prior = ledger.rappen(-pos.prior_flows)
    result = ledger.rappen(balancing) - retained_prior
    unrealized = ledger.rappen(market_minus_book)

    erfolg_problems: list[ledger.Violation] = []
    erfolg = _erfolg_from(entries, price_map, mapping, fy_start, on, erfolg_problems)
    # The Erfolgsrechnung's codes in no row are a likely cause of a gap: name them.
    problems += [v for v in erfolg_problems if v.message.startswith("kmu: ")]
    expected = erfolg.result + unrealized + ledger.rappen(pos.residual)
    problems += _cross_check(result, expected, erfolg, len(per_code), at)

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
        unrealized_fx=unrealized,
        violations=problems,
    )


def _cross_check(
    result: Decimal, expected: Decimal, erfolg: ErfolgReport, bilanz_codes: int, at: str
) -> list[ledger.Violation]:
    """The Bilanz result is the Erfolgsrechnung result plus unrealized FX, plus
    the residual beancount's balancing tolerance left in the books.

    Double entry makes it an identity for every account both statements see,
    so a gap means one of them can't see something — an account without a
    ``kmu:`` code, a code in no row, an amount with no rate. That is the
    books, not quints: a violation.
    """
    # Each per-code amount is rounded to the Rappen once (±0.005), as are the
    # balancing figure, the prior-year result, the unrealized FX and the residual.
    erfolg_codes = sum(len(r.codes) for r in _erfolg_rows(erfolg))
    tolerance = Decimal("0.005") * (bilanz_codes + erfolg_codes + 4)
    if abs(result - expected) <= tolerance:
        return []
    return [
        ledger.Violation(
            at,
            "",
            "",
            "Bilanz result ≠ Erfolgsrechnung result + unrealized FX — an account without "
            "a kmu: code, in no row, or without a rate is seen by one statement only",
            expected,
            result,
        )
    ]


def _erfolg_rows(report: ErfolgReport) -> list[RowLine]:
    return (
        report.revenue
        + report.expenses
        + report.financial_expenses
        + report.financial_income
        + report.other_results
        + report.direct_taxes
    )


def _total(rows: list[RowLine]) -> Decimal:
    return sum((r.amount for r in rows), Decimal("0"))


def _erfolg_from(
    entries: data.Directives,
    price_map: bc_prices.PriceMap,
    mapping: dict[str, str],
    d0: Date,
    d1: Date,
    problems: list[ledger.Violation],
) -> ErfolgReport:
    """The Erfolgsrechnung over ``d0``..``d1`` (Art. 959b Abs. 2 OR order)."""
    per_code: dict[str, tuple[Decimal, set[str]]] = {}
    for e in entries:
        if not isinstance(e, data.Transaction) or not (d0 <= e.date <= d1):
            continue
        for p in e.postings:
            if p.account not in mapping:
                continue
            if p.account.split(":", 1)[0] not in ("Income", "Expenses"):
                continue
            try:
                chf = _posting_chf(p, e.date, d1, price_map)
            except ledger.RateUnavailable as exc:
                problems.append(_left_out(e, f"{p.account} {p.units}", exc))
                continue
            code = mapping[p.account]
            amount, accounts = per_code.get(code, (Decimal("0"), set()))
            per_code[code] = (amount + chf, accounts | {p.account})
    problems += _unmapped(per_code, ERFOLG_ROWS, "Erfolgsrechnung")

    revenue = _rows_from(per_code, ERFOLG_ROWS, ("revenue", "inventory_changes"))
    expenses = _rows_from(
        per_code,
        ERFOLG_ROWS,
        ("materials_services", "personnel", "other_operating", "depreciation"),
    )
    fin_exp = _rows_from(per_code, ERFOLG_ROWS, ("financial_expenses",))
    fin_inc = _rows_from(per_code, ERFOLG_ROWS, ("financial_income",))
    others = _rows_from(per_code, ERFOLG_ROWS, OTHER_RESULT_ROWS)
    taxes = _rows_from(per_code, ERFOLG_ROWS, ("direct_taxes",))

    ebit = ledger.rappen(_total(revenue) - _total(expenses))
    ebt = ledger.rappen(ebit - _total(fin_exp) + _total(fin_inc))
    before_taxes = ledger.rappen(ebt + _total(others))
    return ErfolgReport(
        date_from=str(d0),
        date_to=str(d1),
        revenue=revenue,
        expenses=expenses,
        ebit=ebit,
        financial_expenses=fin_exp,
        financial_income=fin_inc,
        result=ledger.rappen(before_taxes - _total(taxes)),
        ebt=ebt,
        other_results=others,
        result_before_taxes=before_taxes,
        direct_taxes=taxes,
        violations=problems,
    )


def compute_erfolg(
    ledger_path: Path, date_from: str, date_to: str, cfg: config.Config | None = None
) -> ErfolgReport:
    cfg = cfg or config.get()
    d0, d1 = Date.fromisoformat(date_from), Date.fromisoformat(date_to)
    entries, problems = ledger.load(ledger_path)
    price_map = bc_prices.build_price_map(entries)
    mapping = kmu_map(entries, cfg.entity_marker)
    report = _erfolg_from(entries, price_map, mapping, d0, d1, problems)
    report.date_from, report.date_to = date_from, date_to
    return report


def compute_konten(
    ledger_path: Path, date_from: str, date_to: str, cfg: config.Config | None = None
) -> KontenReport:
    cfg = cfg or config.get()
    d0, d1 = Date.fromisoformat(date_from), Date.fromisoformat(date_to)
    entries, problems = ledger.load(ledger_path)
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
            try:
                chf = _posting_chf(p, e.date, d1, price_map)
            except ledger.RateUnavailable as exc:
                problems.append(_left_out(e, f"{p.account} {p.units}", exc))
                continue
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
    return KontenReport(date_from, date_to, [konten[c] for c in sorted(konten)], problems)


# ── render ───────────────────────────────────────────────────────────────────


_PROBLEMS_TITLE = {"en": "Problems in the books", "de": "Prüfung — Probleme in der Buchhaltung"}


def _render_violations(console: Console, violations: list[ledger.Violation], lang: str) -> None:
    if violations:
        title = _PROBLEMS_TITLE.get(lang, _PROBLEMS_TITLE["en"])
        console.print(ui.violations_table(violations, title, lang))
        console.print()


def _statement_table(lang: str) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, pad_edge=False, expand=False, show_header=False)
    t.add_column("KMU", justify="right", style="ziffer", no_wrap=True)
    t.add_column("Position", min_width=44)
    t.add_column("CHF", justify="right", no_wrap=True)
    return t


def _add_rows(t: Table, rows: list[RowLine], lang: str, form: str | None = None) -> None:
    for row in rows:
        t.add_row("", label(row.key, lang, form), ui.money(row.amount))
        if row.parts:  # a subtotal: its positions carry the codes
            for part in row.parts:
                t.add_row("", f"  {label(part.key, lang, form)}", ui.money(part.amount))
                _add_codes(t, part.codes, lang, form)
            continue
        _add_codes(t, row.codes, lang, form)


def _add_codes(t: Table, codes: list[CodeLine], lang: str, form: str | None) -> None:
    for cl in codes:
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

    _bilanz_notes(report, lang, console)
    console.print()
    _render_violations(console, report.violations, lang)


def _bilanz_notes(report: BilanzReport, lang: str, console: Console) -> None:
    """How the foreign balances were valued, and what of the result is unrealized FX."""
    if report.converted:
        parts = ", ".join(f"{ui.money(v)} {c}" for c, v in sorted(report.converted.items()))
        note = {
            "en": f"Non-CHF balances valued at the {report.at} rate: {parts}.",
            "de": f"Fremdwährungsbestände zum Kurs per {report.at} bewertet: {parts}.",
        }
        console.print(note.get(lang, note["en"]), style="muted")
    if report.unrealized_fx:
        console.print(
            f"{label('unrealized_fx', lang)}: {ui.money(report.unrealized_fx)}", style="muted"
        )


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
    if report.other_results or report.direct_taxes:
        # Art. 959b Abs. 2 Ziff. 8-10 OR — shown only when the books use them.
        t.add_section()
        t.add_row("", f"[bold]{label('ebt', lang)}[/]", f"[bold]{ui.money(report.ebt)}[/]")
        t.add_section()
        _add_rows(t, report.other_results, lang)
        t.add_section()
        t.add_row(
            "",
            f"[bold]{label('result_before_taxes', lang)}[/]",
            f"[bold]{ui.money(report.result_before_taxes)}[/]",
        )
        for row in report.direct_taxes:
            negated = [CodeLine(c.code, -c.amount, c.accounts) for c in row.codes]
            _add_rows(t, [RowLine(row.key, -row.amount, negated)], lang)
    t.add_section()
    style = "refund" if report.result >= 0 else "owe"
    t.add_row(
        "",
        f"[bold {style}]{label('result', lang)}[/]",
        f"[bold {style}]{ui.money(report.result)}[/]",
    )
    console.print(t)
    console.print()
    _render_violations(console, report.violations, lang)


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
    _render_violations(console, report.violations, lang)
