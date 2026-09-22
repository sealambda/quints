"""Swiss MWST (VAT) report — Form 310, effective method (effektive Methode).

Ziffern, labels and arithmetic follow the official ESTV form **MWST-4470,
"Abrechnung nach der effektiven Methode", gültig ab 01.01.2024**
(<https://www.estv2.admin.ch/mwst/formulare/mwst-form-abr-muster-2024-4470-eff-de.pdf>;
the annual reconciliation ``DM_0550_03 / 01.24`` prints the same Ziffern):

    I. UMSATZ
    200  Total der vereinbarten bzw. vereinnahmten Entgelte (weltweiter Umsatz)
    205  davon optierte Leistungen (Art. 21, Option nach Art. 22) — a memo line
    220  Von der Steuer befreite Leistungen (u.a. Exporte, Art. 23)
    221  Leistungen im Ausland (Ort der Leistung im Ausland)
    225  Übertragung im Meldeverfahren (Art. 38)
    230  Von der Steuer ausgenommene Inlandleistungen (Art. 21, ohne Option)
    235  Entgeltsminderungen (Skonti, Rabatte, Debitorenverluste)
    280  Diverses
    289  Total Abzüge            = 220 + 221 + 225 + 230 + 235 + 280
    299  Steuerbarer Gesamtumsatz = 200 − 289

    II. STEUERBERECHNUNG        ab 01.01.2024      bis 31.12.2023
    Normalsatz                  303   8.1 %        302   7.7 %
    Reduzierter Satz            313   2.6 %        312   2.5 %
    Beherbergung                343   3.8 %        342   3.7 %
    Bezugsteuer (Art. 45 ff.)   383                382
    399  Total geschuldete Steuer = Steuer 302…383
    400  Vorsteuer auf Material- und Dienstleistungsaufwand
    405  Vorsteuer auf Investitionen und übrigem Betriebsaufwand
    410  Einlageentsteuerung (Art. 32)                                    +
    415  Vorsteuerkorrekturen: gemischte Verwendung, Eigenverbrauch (30/31) −
    420  Vorsteuerkürzungen: Nicht-Entgelte (Art. 33 Abs. 2)              −
    479  Total Vorsteuer          = 400 + 405 + 410 − 415 − 420
    500  Zu bezahlender Betrag    = 399 − 479, when positive
    510  Guthaben der steuerpflichtigen Person = 479 − 399, when positive

    III. ANDERE MITTELFLÜSSE (Art. 18 Abs. 2)
    900  Subventionen, Tourismusabgaben, Entsorgungs-/Wasserwerkbeiträge
    910  Spenden, Dividenden, Schadenersatz usw.

Rates are law and live date-ranged in :data:`quints.ledger.VAT_RATE_CLASSES`
(Art. 25 MWSTG). The form carries two vintages side by side, so a supply made
before 2024 and declared later still files under 302/312/342/382: quints takes
the vintage from the transaction date, or from an explicit ``mwst: "old_rate"``.

**Where a booking lands** — deterministic, in this order:

1. ``mwst:`` metadata on the posting, else on the transaction: a
   space-separated list of tokens from :data:`VOCABULARY`. An unknown token is
   reported as a violation, never ignored.
2. the account's ``kmu:`` code (:func:`quints.kmu.kmu_map`): Erlösminderungen
   3800–3899 are Ziffer 235; an income account outside 3000–3899 (FX gains,
   rounding, Bestandesänderungen) is not Entgelt and stays out entirely; an
   input-VAT counter-leg in Kontenklasse 4 is Ziffer 400, anything else
   (investments 1400–1799, übriger Betriebsaufwand 5/6/7) Ziffer 405.
3. the income-account markers from ``quints.toml`` (``export_goods_marker``
   → 220, ``export_marker`` → 221, ``exempt_marker`` → 230,
   ``optioned_marker`` → 205, ``reduced_marker``/``lodging_marker`` → rate
   class).
4. otherwise: domestic turnover at the standard rate.

VAT is computed on an **accrual** basis directly from ledger entries: output
VAT from OutputVAT movements (credits accrue, a credit note's debit reverses),
input VAT from debits to InputVAT, Bezugsteuer from credits to the Bezugsteuer
liability (Art. 45 ff.; the matching InputVAT debit is deducted on its own, so
the pair is cash-neutral). A settlement transaction — one touching
``payable_vat``, or carrying a ``^VAT-*`` link — is skipped wholesale, so the
quarterly flush never re-enters the return. InputVAT *credits* count only when
tagged 415/420, which leaves the one-off pre-liability reversal ignored as
before.

Every transaction with turnover is checked: the output VAT it posts must equal
net × rate within :func:`_tolerance`. Mismatches are listed as
:class:`Violation` (text and ``--json``) rather than silently mis-filed.

Computation (`compute`) is separated from presentation (`render`) so a future
web/TUI front-end can consume the :class:`MwstReport` dataclass directly.

JSON contract: every ``zNNN`` field is a Form-310 Ziffer. ``z500`` is the
*signed* net — the figure the settlement posts, negative when the ESTV owes
you; the form splits it into 500 (owed) and 510 (credit), and ``z510`` carries
the credit as a positive magnitude.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from beancount.core import convert as bc_convert
from beancount.core import data
from beancount.core import prices as bc_prices
from beancount.core.amount import Amount
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, kmu, ledger, ui

_QUARTER_MONTHS = {
    1: ("01-01", "03-31"),
    2: ("04-01", "06-30"),
    3: ("07-01", "09-30"),
    4: ("10-01", "12-31"),
}

# The date the form's two rate vintages meet (AHV-Zusatzfinanzierung).
RATE_CHANGE = Date(2024, 1, 1)
_BEFORE_CHANGE = RATE_CHANGE - timedelta(days=1)

# Metadata key carrying the VAT vocabulary, on a posting or a transaction.
META_KEY = "mwst"

# rate class → (Ziffer ab 01.01.2024, Ziffer bis 31.12.2023, label, short label)
RATE_ZIFFERN: dict[str, tuple[str, str, str, str]] = {
    "standard": ("303", "302", "Leistungen zum Normalsatz", "Normalsatz"),
    "reduced": ("313", "312", "Leistungen zum reduzierten Satz", "Reduzierter Satz"),
    "lodging": ("343", "342", "Beherbergungsleistungen", "Beherbergung"),
}
BEZUGSTEUER_ZIFFERN = ("383", "382")  # (ab 01.01.2024, bis 31.12.2023)
# The Saldosteuersatz form has one turnover line per vintage; the granted rates
# are split across it on the "Beiblatt zu den Ziffern 322 und 323".
SALDO_ZIFFERN = ("323", "322")  # (ab 01.01.2024, bis 31.12.2023)
SSS_PREFIX = "sss="  # mwst: "sss=6.2" pins a posting to a granted rate

METHOD_NAMES = {
    "effective": "Effektive Abrechnungsmethode",
    "saldo": "Saldosteuersatzmethode (Art. 37 MWSTG)",
}

FORMS = {
    "effective": "Formular 310 (MWST-4470, ab 01.01.2024)",
    "saldo": "Abrechnung SSS (MWST-Info 12, ab 01.01.2025)",
}

# Turnover buckets → the Ziffer they are deducted under. "taxable" is the
# residual: it feeds the rate rows and, through them, Ziffer 299.
TURNOVER_ZIFFERN: dict[str, str] = {
    "export_goods": "220",
    "export": "221",
    "meldeverfahren": "225",
    "exempt": "230",
    "reduction": "235",
    "diverses": "280",
}
# Buckets outside Ziffer 200 entirely (Nicht-Entgelte, Art. 18 Abs. 2).
FLOW_ZIFFERN: dict[str, str] = {"subvention": "900", "donation": "910"}

# Input-VAT tokens → Ziffer, corrections first (they win over 400/405).
INPUT_ZIFFERN: dict[str, str] = {
    "einlageentsteuerung": "410",
    "vorsteuerkorrektur": "415",
    "vorsteuerkuerzung": "420",
    "material": "400",
    "investment": "405",
}
MINUS_ZIFFERN = ("415", "420")  # the form subtracts these from Ziffer 479

VOCABULARY = frozenset(
    {"taxable", "standard", "reduced", "lodging", "optioned", "old_rate"}
    | set(TURNOVER_ZIFFERN)
    | set(FLOW_ZIFFERN)
    | set(INPUT_ZIFFERN)
)

# KMU Kontenrahmen (veb.ch) code ranges the classification relies on.
REDUCTION_CODES = ("3800", "3899")  # Erlösminderungen, Verluste Forderungen
# Betrieblicher Ertrag aus Lieferungen und Leistungen, up to the
# Erlösminderungen block. 39xx Bestandesänderungen are a valuation adjustment,
# not Entgelt, and stay out of the return like any non-Klasse-3 income.
TURNOVER_CODES = ("3000", "3899")
INPUT_COUNTER_CODES = (("1400", "1799"), ("4000", "7999"))  # Anlagen, Aufwand


_HALF_MONTHS = {1: ("01-01", "06-30"), 2: ("07-01", "12-31")}
_PERIOD_SPEC = re.compile(r"^(\d{4})(?:-?([QH])([1-4]))?$")


def period_range(period: str) -> tuple[str, str]:
    """'2026-Q2' → ('2026-04-01', '2026-06-30'); also '2026-H1' and '2026'.

    Quarters are the effective method's Abrechnungsperioden, half-years the
    Saldosteuersatz method's (Art. 35 MWSTG); a bare year is the annual
    settlement (Art. 35a MWSTG, on request since 2025).
    """
    m = _PERIOD_SPEC.match(period.upper().replace(" ", ""))
    if not m:
        raise ValueError(f"bad period {period!r}, expected e.g. 2026-Q2, 2026-H1 or 2026")
    year, kind, n = int(m.group(1)), m.group(2), m.group(3)
    if kind is None:
        return f"{year}-01-01", f"{year}-12-31"
    index = int(n)
    months = _QUARTER_MONTHS if kind == "Q" else _HALF_MONTHS
    if index not in months:
        raise ValueError(f"bad period {period!r}: there is no {kind}{index}")
    start, end = months[index]
    return f"{year}-{start}", f"{year}-{end}"


def period_label(period: str) -> str:
    """Normalise a period spec for the ``^VAT-<label>`` link: '2026q3' → '2026-Q3'."""
    m = _PERIOD_SPEC.match(period.upper().replace(" ", ""))
    if not m:
        raise ValueError(f"bad period {period!r}, expected e.g. 2026-Q2, 2026-H1 or 2026")
    year, kind, n = m.group(1), m.group(2), m.group(3)
    return year if kind is None else f"{year}-{kind}{n}"


def quarter_range(quarter: str) -> tuple[str, str]:
    """Back-compatible alias for :func:`period_range`."""
    return period_range(quarter)


# ── data ─────────────────────────────────────────────────────────────────────


@dataclass
class VatLine:
    date: str
    payee: str
    narration: str
    original: Decimal
    currency: str
    rate: Decimal  # CHF per unit of `currency`
    chf: Decimal  # signed contribution to its Ziffer
    ziffer: str = "400"


@dataclass
class RevenueLine:
    date: str
    payee: str
    original: Decimal
    currency: str
    chf: Decimal
    ziffer: str = "299"
    rate_class: str = "standard"


@dataclass
class RateRow:
    """One "Leistungen / Steuer" line of section II."""

    ziffer: str
    rate_class: str  # "standard"/"reduced"/"lodging", or "saldo"
    rate: Decimal
    net: Decimal  # the form's "Leistungen" column — gross incl. MWST under SSS
    tax: Decimal
    current: bool  # the ab-01.01.2024 vintage
    label: str = ""  # the granted Saldosteuersatz this row is for, e.g. "6.2"


@dataclass
class Violation:
    """A booking the form cannot represent — listed, never silently filed."""

    date: str
    payee: str
    narration: str
    message: str
    expected: Decimal = Decimal("0")
    posted: Decimal = Decimal("0")


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
    # Section I — the remaining deductions and the Art. 22 memo.
    z205: Decimal = Decimal("0")
    z220: Decimal = Decimal("0")
    z225: Decimal = Decimal("0")
    z230: Decimal = Decimal("0")
    z235: Decimal = Decimal("0")
    z280: Decimal = Decimal("0")
    # Section II — the remaining rate rows (see RATE_ZIFFERN).
    z302_net: Decimal = Decimal("0")
    z302_tax: Decimal = Decimal("0")
    z312_net: Decimal = Decimal("0")
    z312_tax: Decimal = Decimal("0")
    z313_net: Decimal = Decimal("0")
    z313_tax: Decimal = Decimal("0")
    z342_net: Decimal = Decimal("0")
    z342_tax: Decimal = Decimal("0")
    z343_net: Decimal = Decimal("0")
    z343_tax: Decimal = Decimal("0")
    z383_net: Decimal = Decimal("0")
    z383_tax: Decimal = Decimal("0")
    # Section II — input VAT.
    z405: Decimal = Decimal("0")
    z410: Decimal = Decimal("0")
    z415: Decimal = Decimal("0")  # positive magnitude; the form subtracts it
    z420: Decimal = Decimal("0")  # positive magnitude; the form subtracts it
    z510: Decimal = Decimal("0")  # credit, positive; 0 when z500 is owed
    # Saldosteuersatz method — the turnover lines of its own form.
    z322_net: Decimal = Decimal("0")
    z322_tax: Decimal = Decimal("0")
    z323_net: Decimal = Decimal("0")
    z323_tax: Decimal = Decimal("0")
    # Section III — Nicht-Entgelte.
    z900: Decimal = Decimal("0")
    z910: Decimal = Decimal("0")
    vat_method: str = "effective"
    form: str = ""
    # The period's OutputVAT account movement — what the settlement flushes.
    # Under SSS this is the statutory VAT invoiced, not the SSS owed.
    output_vat: Decimal = Decimal("0")
    rate_rows: list[RateRow] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    vat_lines: list[VatLine] = field(default_factory=list)
    bezugsteuer_lines: list[VatLine] = field(default_factory=list)
    domestic: list[RevenueLine] = field(default_factory=list)
    export: list[RevenueLine] = field(default_factory=list)
    other_revenue: list[RevenueLine] = field(default_factory=list)

    @property
    def domestic_total(self) -> Decimal:
        return sum((r.chf for r in self.domestic), Decimal("0"))

    @property
    def export_total(self) -> Decimal:
        return sum((r.chf for r in self.export), Decimal("0"))

    @property
    def bezugsteuer_tax(self) -> Decimal:
        """Bezugsteuer across both vintages (Ziffern 382 + 383)."""
        return self.z382_tax + self.z383_tax


# ── compute ───────────────────────────────────────────────────────────────────


def _to_chf(units: Amount, date: Date, price_map: bc_prices.PriceMap) -> Decimal:
    """Value an amount in CHF at ``date`` (prior-date fallback via price map)."""
    if units.number is None:  # incomplete amount — cannot occur in a loaded ledger
        return Decimal("0")
    if units.currency == "CHF":
        return units.number
    conv = bc_convert.convert_amount(units, "CHF", price_map, date=date)
    if conv.currency == "CHF" and conv.number is not None:
        return conv.number
    return Decimal("0")


def _weight_chf(p: data.Posting, on: Date, price_map: bc_prices.PriceMap) -> Decimal:
    """CHF weight of a posting — its own @/@@ rate wins, then the price map."""
    weight = bc_convert.get_weight(p)
    if weight.number is None:  # incomplete posting — cannot occur in a loaded ledger
        return Decimal("0")
    if weight.currency == "CHF":
        return weight.number
    conv = bc_convert.convert_amount(weight, "CHF", price_map, date=on)
    if conv.currency == "CHF" and conv.number is not None:
        return conv.number
    return Decimal("0")


def _parse_tokens(raw: object) -> frozenset[str]:
    if not isinstance(raw, str):
        return frozenset()
    return frozenset(raw.replace(",", " ").split())


def _txn_tokens(e: data.Transaction) -> frozenset[str]:
    """The ``mwst:`` vocabulary on a transaction."""
    return _parse_tokens((e.meta or {}).get(META_KEY))


def _tokens(p: data.Posting, e: data.Transaction) -> frozenset[str]:
    """The ``mwst:`` vocabulary on a posting, falling back to its transaction."""
    raw = (p.meta or {}).get(META_KEY)
    return _parse_tokens(raw) if raw is not None else _txn_tokens(e)


def _unknown_tokens(tokens: frozenset[str], cfg: config.Config) -> set[str]:
    """Tokens the vocabulary does not contain — including an unknown ``sss=``."""
    bad: set[str] = set()
    for token in tokens:
        if token.startswith(SSS_PREFIX):
            if not any(g.name == token[len(SSS_PREFIX) :] for g in cfg.saldo):
                bad.add(token)
        elif token not in VOCABULARY:
            bad.add(token)
    return bad


def _saldo_for(account: str, tokens: frozenset[str], cfg: config.Config) -> config.SaldoRate | None:
    """Which granted Saldosteuersatz a supply falls under.

    ``mwst: "sss=6.2"`` pins it; otherwise the first granted rate whose marker
    the income account carries; otherwise the default rate. An ``sss=`` naming
    a rate that was never granted falls through to the default and is reported
    by :func:`_unknown_tokens`.
    """
    for token in tokens:
        if token.startswith(SSS_PREFIX):
            name = token[len(SSS_PREFIX) :]
            for granted in cfg.saldo:
                if granted.name == name:
                    return granted
    for granted in cfg.saldo:
        if granted.marker and granted.marker in account:
            return granted
    return cfg.saldo_default


def _in(code: str | None, lo: str, hi: str) -> bool:
    return code is not None and lo <= code <= hi


def _acc(into: dict[str, Decimal], key: str, value: Decimal) -> None:
    into[key] = into.get(key, Decimal("0")) + value


def _rate_class(account: str, tokens: frozenset[str], cfg: config.Config) -> str:
    """Art. 25 rate class: metadata, then account marker, then standard."""
    for name in ("reduced", "lodging", "standard"):
        if name in tokens:
            return name
    if cfg.reduced_marker and cfg.reduced_marker in account:
        return "reduced"
    if cfg.lodging_marker and cfg.lodging_marker in account:
        return "lodging"
    return "standard"


def _turnover_bucket(
    account: str, code: str | None, tokens: frozenset[str], cfg: config.Config
) -> str | None:
    """Which section-I line an income posting belongs to (None = not turnover)."""
    if account == cfg.saldo_difference:
        # The SSS settlement books the gap between the statutory VAT invoiced
        # and the SSS owed here. The gross Entgelt was already declared when
        # the sale was booked, so this is never turnover — by identity, so it
        # cannot be forgotten on a hand-written settlement.
        return None
    for name in FLOW_ZIFFERN:
        if name in tokens:
            return name
    for name in TURNOVER_ZIFFERN:
        if name in tokens:
            return name
    if "taxable" in tokens or "optioned" in tokens:
        return "taxable"
    if _in(code, *REDUCTION_CODES):
        return "reduction"
    if code is not None and not _in(code, *TURNOVER_CODES):
        return None  # booked as income, but not Entgelt (FX gain, rounding)
    if cfg.export_goods_marker and cfg.export_goods_marker in account:
        return "export_goods"
    if cfg.export_marker and cfg.export_marker in account:
        return "export"
    if cfg.exempt_marker and cfg.exempt_marker in account:
        return "exempt"
    return "taxable"


def _input_ziffer(
    e: data.Transaction,
    p: data.Posting,
    tokens: frozenset[str],
    codes: dict[str, str],
    price_map: bc_prices.PriceMap,
    cfg: config.Config,
) -> str:
    """400 (Material/DL) vs 405 (Investitionen, übriger Betriebsaufwand).

    The KMU code of the dominant counter-leg decides: Kontenklasse 4 is the
    form's "Material- und Dienstleistungsaufwand", everything else (1400–1799
    investments, 5/6/7 operating expenses) is 405. Dominant = the largest CHF
    weight, ties to the lowest code; an uncoded, untagged purchase is 405.
    """
    for name, ziffer in INPUT_ZIFFERN.items():
        if name in tokens:
            return ziffer
    best: tuple[Decimal, str] | None = None
    for q in e.postings:
        if q is p or q.account == cfg.bezugsteuer_expense:
            continue  # the SSS Bezugsteuer cost is a tax, not a supply
        code = codes.get(q.account)
        if code is None or not any(_in(code, lo, hi) for lo, hi in INPUT_COUNTER_CODES):
            continue
        weight = abs(_weight_chf(q, e.date, price_map))
        if best is None or weight > best[0] or (weight == best[0] and code < best[1]):
            best = (weight, code)
    return "400" if best is not None and best[1].startswith("4") else "405"


def _tolerance(net: Decimal) -> Decimal:
    """How far posted VAT may sit from net × rate: rounding, plus FX drift.

    Per-line Rappen rounding costs a few centimes; a foreign-currency supply
    valued at the day rate drifts a little more. 0.1 % of the net still
    separates 8.1 % from 7.7 % on any invoice worth checking.
    """
    return max(Decimal("0.05"), abs(net) * Decimal("0.001"))


def _rate_on(current: bool, rate_class: str) -> Decimal:
    return ledger.vat_rate(RATE_CHANGE if current else _BEFORE_CHANGE, rate_class)


def _vintage_ziffer(rate_class: str, current: bool) -> str:
    new, old = RATE_ZIFFERN[rate_class][:2]
    return new if current else old


def _rate_rows(
    nets: dict[tuple[str, bool], Decimal],
    taxes: dict[tuple[str, bool], Decimal],
    d0: Date,
    d1: Date,
) -> list[RateRow]:
    """The section-II rate lines: those in force in the period, plus any used."""
    rows: list[RateRow] = []
    for current in (True, False):
        in_force = (d1 >= RATE_CHANGE) if current else (d0 < RATE_CHANGE)
        for rate_class in RATE_ZIFFERN:
            key = (rate_class, current)
            net, tax = nets.get(key, Decimal("0")), taxes.get(key, Decimal("0"))
            if not in_force and not net and not tax:
                continue
            rows.append(
                RateRow(
                    ziffer=_vintage_ziffer(rate_class, current),
                    rate_class=rate_class,
                    rate=_rate_on(current, rate_class),
                    net=net,
                    tax=tax,
                    current=current,
                )
            )
    return rows


def _is_settlement(e: data.Transaction, cfg: config.Config) -> bool:
    """A period flush, not an accrual: it moves VAT into or out of PayableVAT.

    Touching ``payable_vat`` is the whole test — every block
    :func:`quints.settlement.settlement_text` emits posts to it, and so does
    the payment that clears it. Matching on the ``^VAT-*`` link instead would
    drop any business transaction that happens to carry such a link, silently.
    """
    return any(p.account == cfg.payable_vat for p in e.postings)


@dataclass
class _Totals:
    """Accumulators for one :func:`compute` pass."""

    turnover: dict[str, Decimal] = field(default_factory=dict)  # Ziffer → amount
    flows: dict[str, Decimal] = field(default_factory=dict)  # 900 / 910
    inputs: dict[str, Decimal] = field(default_factory=dict)  # 400…420
    nets: dict[tuple[str, bool], Decimal] = field(default_factory=dict)
    taxes: dict[tuple[str, bool], Decimal] = field(default_factory=dict)
    bezug_tax: dict[bool, Decimal] = field(default_factory=dict)
    bezug_net: dict[bool, Decimal] = field(default_factory=dict)
    # Saldosteuersatz: (granted rate's name, vintage) → Entgelt incl. MWST.
    sss_nets: dict[tuple[str, bool], Decimal] = field(default_factory=dict)
    gross: Decimal = Decimal("0")  # Ziffer 200
    output_vat: Decimal = Decimal("0")  # the OutputVAT account movement


@dataclass
class _Detail:
    """The per-posting tables the text report shows beneath the Ziffern."""

    violations: list[Violation] = field(default_factory=list)
    vat_lines: list[VatLine] = field(default_factory=list)
    bezugsteuer_lines: list[VatLine] = field(default_factory=list)
    domestic: list[RevenueLine] = field(default_factory=list)
    export: list[RevenueLine] = field(default_factory=list)
    other_revenue: list[RevenueLine] = field(default_factory=list)


@dataclass
class _TxnState:
    """What one transaction contributed, for the rate-consistency check."""

    current: bool
    expected_tax: Decimal = Decimal("0")
    posted_tax: Decimal = Decimal("0")
    rate_class: str = "standard"
    weight: Decimal = Decimal("-1")  # of the dominant taxable leg
    has_turnover: bool = False
    # Saldosteuersatz: the dominant leg's bucket and granted rate, which the
    # transaction's output VAT follows into Ziffer 299 or Ziffer 235.
    bucket: str = "taxable"
    sss: str = ""


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
    codes = kmu.kmu_map(entries, cfg.entity_marker)

    t, detail = _Totals(), _Detail()
    for e in entries:
        if not isinstance(e, data.Transaction) or not (d0 <= e.date <= d1):
            continue
        if _is_settlement(e, cfg):
            continue
        _transaction(e, cfg, codes, price_map, t, detail)
    return _assemble(date_from, date_to, d0, d1, t, detail, cfg)


def _transaction(
    e: data.Transaction,
    cfg: config.Config,
    codes: dict[str, str],
    price_map: bc_prices.PriceMap,
    t: _Totals,
    detail: _Detail,
) -> None:
    """Route one transaction's postings into the Ziffern, then check its VAT."""
    txn_tokens = _txn_tokens(e)
    # The rate vintage is a property of the whole transaction: one "old_rate"
    # anywhere in it (transaction or posting) files it under 302/312/342/382.
    old_rate = "old_rate" in txn_tokens or any("old_rate" in _tokens(p, e) for p in e.postings)
    state = _TxnState(current=e.date >= RATE_CHANGE and not old_rate)
    unknown: set[str] = _unknown_tokens(txn_tokens, cfg)

    for p in e.postings:
        if p.units is None or p.units.number is None:
            continue  # incomplete posting — cannot occur in a loaded ledger
        n = p.units.number
        acct = p.account
        tokens = _tokens(p, e)
        unknown |= _unknown_tokens(tokens, cfg)

        if acct == cfg.output_vat:
            state.posted_tax += -n  # credits accrue, a credit note's debit reverses
        elif acct == cfg.bezugsteuer and n < 0:
            _bezugsteuer(e, p, -n, tokens, cfg, state.current, t, detail)
        elif acct == cfg.input_vat:
            _input_vat(e, p, n, tokens, codes, price_map, cfg, t, detail)
        elif acct.startswith(cfg.income_prefix):
            _turnover(e, p, n, tokens, codes, price_map, cfg, state, t, detail)

    if cfg.vat_method == "saldo":
        _saldo_gross(e, state, t, detail)
    _check(e, state, t, detail)
    for token in sorted(unknown):
        detail.violations.append(
            Violation(
                str(e.date),
                e.payee or "",
                e.narration or "",
                f'unknown mwst: token "{token}" — see the VAT guide',
            )
        )


def _bezugsteuer(
    e: data.Transaction,
    p: data.Posting,
    tax: Decimal,
    tokens: frozenset[str],
    cfg: config.Config,
    current: bool,
    t: _Totals,
    detail: _Detail,
) -> None:
    """Reverse charge on a foreign supply (Art. 45 ff. MWSTG) — Ziffer 383/382."""
    rate_class = _rate_class(p.account, tokens, cfg)
    net = ledger.rappen(tax / ledger.vat_rate(e.date, rate_class))
    t.bezug_tax[current] = t.bezug_tax.get(current, Decimal("0")) + tax
    t.bezug_net[current] = t.bezug_net.get(current, Decimal("0")) + net
    original, currency, rate = _original(p, tax)
    detail.bezugsteuer_lines.append(
        VatLine(
            str(e.date),
            e.payee or "",
            e.narration or "",
            original,
            currency,
            rate,
            tax,
            BEZUGSTEUER_ZIFFERN[0 if current else 1],
        )
    )


def _input_vat(
    e: data.Transaction,
    p: data.Posting,
    n: Decimal,
    tokens: frozenset[str],
    codes: dict[str, str],
    price_map: bc_prices.PriceMap,
    cfg: config.Config,
    t: _Totals,
    detail: _Detail,
) -> None:
    """Deductible input VAT — Ziffern 400/405, or a tagged 410/415/420."""
    if cfg.vat_method == "saldo":
        # The SSS already compensates the input tax (Art. 37 MWSTG): purchases
        # are booked gross and the form has no 400-479 block at all.
        detail.violations.append(
            Violation(
                str(e.date),
                e.payee or "",
                e.narration or "",
                "input VAT is not deductible under the Saldosteuersatz method — book gross",
                Decimal("0"),
                n,
            )
        )
        return
    ziffer = _input_ziffer(e, p, tokens, codes, price_map, cfg)
    if ziffer in MINUS_ZIFFERN:
        _acc(t.inputs, ziffer, -n)  # a credit becomes a positive magnitude
    elif n <= 0:
        return  # settlement flush or the one-off pre-liability reversal
    else:
        _acc(t.inputs, ziffer, n)
    original, currency, rate = _original(p, abs(n))
    detail.vat_lines.append(
        VatLine(str(e.date), e.payee or "", e.narration or "", original, currency, rate, n, ziffer)
    )


def _turnover(
    e: data.Transaction,
    p: data.Posting,
    n: Decimal,
    tokens: frozenset[str],
    codes: dict[str, str],
    price_map: bc_prices.PriceMap,
    cfg: config.Config,
    state: _TxnState,
    t: _Totals,
    detail: _Detail,
) -> None:
    """One income posting → its section-I line, and its share of Ziffer 299."""
    acct = p.account
    bucket = _turnover_bucket(acct, codes.get(acct), tokens, cfg)
    if bucket is None:
        return  # income, but not Entgelt — outside the return
    currency = p.units.currency if p.units is not None else "CHF"
    chf = _to_chf(Amount(-n, currency), e.date, price_map)
    line = RevenueLine(str(e.date), e.payee or "", -n, currency, chf)

    if bucket in FLOW_ZIFFERN:  # Nicht-Entgelte: section III, not Ziffer 200
        _acc(t.flows, FLOW_ZIFFERN[bucket], chf)
        line.ziffer = FLOW_ZIFFERN[bucket]
        detail.other_revenue.append(line)
        return

    if "optioned" in tokens or (cfg.optioned_marker and cfg.optioned_marker in acct):
        _acc(t.turnover, "205", chf)  # memo only — an opted supply stays taxable
        bucket = "taxable"

    state.has_turnover = True
    if bucket == "reduction":
        # Gross turnover was declared when the sale was booked; the reduction
        # is a deduction (positive on the form) that also shrinks its rate row.
        _acc(t.turnover, "235", -chf)
    else:
        t.gross += chf
        if bucket != "taxable":
            _acc(t.turnover, TURNOVER_ZIFFERN[bucket], chf)

    if bucket not in ("taxable", "reduction"):
        line.ziffer = TURNOVER_ZIFFERN[bucket]
        (detail.export if bucket == "export" else detail.other_revenue).append(line)
        return

    rate_class = _rate_class(acct, tokens, cfg)
    key = (rate_class, state.current)
    t.nets[key] = t.nets.get(key, Decimal("0")) + chf
    state.expected_tax += chf * _rate_on(state.current, rate_class)
    granted = _saldo_for(acct, tokens, cfg) if cfg.vat_method == "saldo" else None
    if granted is not None:
        sss_key = (granted.name, state.current)
        t.sss_nets[sss_key] = t.sss_nets.get(sss_key, Decimal("0")) + chf
    if abs(chf) > state.weight:
        state.weight, state.rate_class, state.bucket = abs(chf), rate_class, bucket
        state.sss = granted.name if granted is not None else ""
    line.rate_class = rate_class
    if bucket == "reduction":
        line.ziffer = "235"
        detail.other_revenue.append(line)
    else:
        line.ziffer = _vintage_ziffer(rate_class, state.current)
        detail.domestic.append(line)


def _saldo_gross(e: data.Transaction, state: _TxnState, t: _Totals, detail: _Detail) -> None:
    """Fold the transaction's output VAT into its gross Entgelt (SSS only).

    Under the Saldosteuersatz method the declared values are *inclusive* of
    MWST (MWST-Info 12, Ziff. 18.1.1), so the statutory VAT a sale charged is
    part of Ziffer 200 — and the VAT a credit note reversed is part of the
    Ziffer 235 deduction. Both follow the transaction's dominant income leg,
    which is the single place that decides taxable vs. reduction.
    """
    if not state.posted_tax:
        return
    if not state.has_turnover:
        detail.violations.append(
            Violation(
                str(e.date),
                e.payee or "",
                e.narration or "",
                "output VAT without turnover — the SSS form taxes the gross Entgelt, "
                "so it must be booked to an income account",
                Decimal("0"),
                state.posted_tax,
            )
        )
        return
    key = (state.sss, state.current)
    t.sss_nets[key] = t.sss_nets.get(key, Decimal("0")) + state.posted_tax
    if state.bucket == "reduction":
        _acc(t.turnover, "235", -state.posted_tax)
    else:
        t.gross += state.posted_tax


def _check(e: data.Transaction, state: _TxnState, t: _Totals, detail: _Detail) -> None:
    """File the transaction's output VAT, and flag it if net × rate disagrees."""
    if not state.posted_tax and not state.expected_tax:
        return
    t.output_vat += state.posted_tax
    key = (state.rate_class, state.current)
    t.taxes[key] = t.taxes.get(key, Decimal("0")) + state.posted_tax
    if not state.has_turnover:
        # VAT with no income leg at all (a fixed-asset sale, a manual
        # correction): declare it at the standard rate, but there is nothing to
        # check it against — stay quiet rather than cry wolf.
        return
    if abs(state.posted_tax - state.expected_tax) > _tolerance(state.expected_tax):
        rate = _rate_on(state.current, state.rate_class) * 100
        detail.violations.append(
            Violation(
                str(e.date),
                e.payee or "",
                e.narration or "",
                f"output VAT does not match net × {rate:.1f} %",
                ledger.rappen(state.expected_tax),
                state.posted_tax,
            )
        )


def _original(p: data.Posting, chf: Decimal) -> tuple[Decimal, str, Decimal]:
    """(amount, currency, CHF rate) — e.g. "7.52 CHF @@ 8.10 EUR" → 8.10 EUR."""
    if p.price is not None and p.price.number is not None:
        original = (chf * p.price.number).quantize(Decimal("0.01"))
        rate = (chf / original) if original else Decimal("0")
        return original, p.price.currency, rate
    currency = p.units.currency if p.units is not None else "CHF"
    return chf, currency, Decimal("1")


def _saldo_rows(t: _Totals, d0: Date, d1: Date, cfg: config.Config) -> list[RateRow]:
    """The Saldosteuersatz turnover lines — Ziffern 323 (and 322 for 2023).

    One row per granted rate and vintage. The base is the Entgelt **incl.
    MWST**; the tax is base x SSS, the multiplication the form does for you
    (MWST-Info 12, Ziff. 18.1.4).
    """
    rows: list[RateRow] = []
    for current in (True, False):
        in_force = (d1 >= RATE_CHANGE) if current else (d0 < RATE_CHANGE)
        for granted in cfg.saldo:
            key = (granted.name, current)
            base = t.sss_nets.get(key, Decimal("0"))
            if not in_force and not base:
                continue
            rows.append(
                RateRow(
                    ziffer=SALDO_ZIFFERN[0 if current else 1],
                    rate_class="saldo",
                    rate=granted.rate,
                    net=base,
                    tax=ledger.rappen(base * granted.rate),
                    current=current,
                    label=granted.name,
                )
            )
    return rows


def _assemble(
    date_from: str,
    date_to: str,
    d0: Date,
    d1: Date,
    t: _Totals,
    detail: _Detail,
    cfg: config.Config,
) -> MwstReport:
    """Totals, cross-checks, and the flat Ziffer fields the JSON contract pins."""
    saldo = cfg.vat_method == "saldo"

    def z(key: str) -> Decimal:
        return t.turnover.get(key, Decimal("0"))

    rows = _saldo_rows(t, d0, d1, cfg) if saldo else _rate_rows(t.nets, t.taxes, d0, d1)

    def row(ziffer: str, index: int) -> Decimal:
        total = Decimal("0")
        for r in rows:
            if r.ziffer == ziffer:
                total += r.net if index == 0 else r.tax
        return total

    z289 = sum((z(k) for k in ("220", "221", "225", "230", "235", "280")), Decimal("0"))
    z299 = t.gross - z289
    rate_net_total = sum((r.net for r in rows), Decimal("0"))
    if rate_net_total != z299:
        detail.violations.append(
            Violation(
                date_to,
                "",
                "",
                "Ziffer 299 ≠ the turnover rows' total — turnover landed nowhere",
                z299,
                rate_net_total,
            )
        )
    z383_tax = t.bezug_tax.get(True, Decimal("0"))
    z382_tax = t.bezug_tax.get(False, Decimal("0"))
    z399 = sum((r.tax for r in rows), Decimal("0")) + z383_tax + z382_tax
    z400 = t.inputs.get("400", Decimal("0"))
    z405 = t.inputs.get("405", Decimal("0"))
    z410 = t.inputs.get("410", Decimal("0"))
    z415 = t.inputs.get("415", Decimal("0"))
    z420 = t.inputs.get("420", Decimal("0"))
    z479 = z400 + z405 + z410 - z415 - z420
    z500 = z399 - z479
    return MwstReport(
        date_from=date_from,
        date_to=date_to,
        z200=t.gross,
        z205=z("205"),
        z220=z("220"),
        z221=z("221"),
        z225=z("225"),
        z230=z("230"),
        z235=z("235"),
        z280=z("280"),
        z289=z289,
        z299=z299,
        z302_net=row("302", 0),
        z302_tax=row("302", 1),
        z303_net=row("303", 0),
        z303_tax=row("303", 1),
        z312_net=row("312", 0),
        z312_tax=row("312", 1),
        z313_net=row("313", 0),
        z313_tax=row("313", 1),
        z342_net=row("342", 0),
        z342_tax=row("342", 1),
        z343_net=row("343", 0),
        z343_tax=row("343", 1),
        z382_net=t.bezug_net.get(False, Decimal("0")),
        z382_tax=z382_tax,
        z383_net=t.bezug_net.get(True, Decimal("0")),
        z383_tax=z383_tax,
        z399=z399,
        z400=z400,
        z405=z405,
        z410=z410,
        z415=z415,
        z420=z420,
        z479=z479,
        z500=z500,
        z510=-z500 if z500 < 0 else Decimal("0"),
        z322_net=row("322", 0),
        z322_tax=row("322", 1),
        z323_net=row("323", 0),
        z323_tax=row("323", 1),
        z900=t.flows.get("900", Decimal("0")),
        z910=t.flows.get("910", Decimal("0")),
        vat_method=cfg.vat_method,
        form=FORMS.get(cfg.vat_method, ""),
        output_vat=t.output_vat,
        rate_rows=rows,
        violations=detail.violations,
        vat_lines=detail.vat_lines,
        bezugsteuer_lines=detail.bezugsteuer_lines,
        domestic=detail.domestic,
        export=detail.export,
        other_revenue=detail.other_revenue,
    )


# ── render ────────────────────────────────────────────────────────────────────

_SECTION_I: tuple[tuple[str, str, bool], ...] = (
    ("205", "davon optierte Leistungen (Art. 22)", False),
    ("220", "Von der Steuer befreite Leistungen (Art. 23)", False),
    ("221", "Leistungen im Ausland (Ort der Leistung im Ausland)", True),
    ("225", "Übertragung im Meldeverfahren (Art. 38)", False),
    ("230", "Von der Steuer ausgenommene Inlandleistungen (Art. 21)", False),
    ("235", "Entgeltsminderungen (Skonti, Rabatte, Verluste)", False),
    ("280", "Diverses", False),
)

_INPUT_ROWS: tuple[tuple[str, str, bool], ...] = (
    ("400", "Vorsteuer auf Material- und Dienstleistungsaufwand", True),
    ("405", "Vorsteuer auf Investitionen und übrigem Betriebsaufwand", True),
    ("410", "Einlageentsteuerung (Art. 32)", False),
    ("415", "Vorsteuerkorrekturen (Art. 30/31)", False),
    ("420", "Vorsteuerkürzungen (Art. 33 Abs. 2)", False),
)


def render(
    report: MwstReport, console: Console | None = None, cfg: config.Config | None = None
) -> None:
    cfg = cfg or config.get()
    console = console or ui.console
    console.print()
    console.rule(f"[bold]MWST-Abrechnung[/]   {report.date_from} – {report.date_to}")
    method = METHOD_NAMES.get(report.vat_method, report.vat_method).split(" (")[0]
    console.print(
        f"{cfg.entity_name} · {method} · {report.form}",
        style="muted",
        justify="center",
    )
    console.print()
    console.print(_main_table(report))

    if report.violations:
        console.print()
        console.print(_violations_table(report.violations))
    if report.vat_lines:
        console.print()
        console.print(
            _vat_table(report.vat_lines, report.z479, "Vorsteuer (Input VAT) — Ziffern 400–420")
        )
    if report.vat_method == "saldo" and report.bezugsteuer_tax:
        console.print()
        console.print(
            "[muted]Bezugsteuer is owed at the statutory rate and is not deductible under "
            "the SSS method — it is a cost.[/]"
        )
    if report.bezugsteuer_lines:
        console.print()
        console.print(
            _vat_table(
                report.bezugsteuer_lines,
                report.bezugsteuer_tax,
                "Bezugsteuer (reverse charge) — Ziffern 383/382",
            )
        )
    if report.domestic or report.export or report.other_revenue:
        console.print()
        console.print(_revenue_table(report))
    console.print()


def _main_table(report: MwstReport) -> Table:
    """The Form-310 Ziffern, in the order the ESTV portal asks for them."""
    main = Table(box=box.SIMPLE_HEAVY, pad_edge=False, expand=False)
    main.add_column("Ziffer", justify="right", style="ziffer", no_wrap=True)
    main.add_column("Position")
    main.add_column("Umsatz CHF", justify="right", no_wrap=True)
    main.add_column("Steuer CHF", justify="right", no_wrap=True)

    def row(
        z: str,
        label: str,
        umsatz: Decimal | None = None,
        steuer: Decimal | None = None,
        style: str | None = None,
    ) -> None:
        u = ui.money(umsatz) if umsatz is not None else ""
        s = ui.money(steuer) if steuer is not None else ""
        if style:
            label, u, s = (f"[{style}]{x}[/]" if x else x for x in (label, u, s))
        main.add_row(z, label, u, s)

    values: dict[str, Decimal] = {
        "205": report.z205,
        "220": report.z220,
        "221": report.z221,
        "225": report.z225,
        "230": report.z230,
        "235": report.z235,
        "280": report.z280,
        "400": report.z400,
        "405": report.z405,
        "410": report.z410,
        "415": report.z415,
        "420": report.z420,
    }
    row("200", "Total der vereinbarten Entgelte (weltweit)", report.z200)
    for ziffer, label, always in _SECTION_I:
        if always or values[ziffer]:
            row(ziffer, label, values[ziffer])
    row("289", "Total Abzüge", report.z289)
    row("299", "Steuerbarer Gesamtumsatz", report.z299)
    main.add_section()
    for r in report.rate_rows:
        vintage = "" if r.current else " (bis 31.12.2023)"
        if r.rate_class == "saldo":
            name = f"Leistungen zum Saldosteuersatz {r.label} %"
        else:
            _new, _old, long_label, short = RATE_ZIFFERN[r.rate_class]
            name = f"{long_label if r.current else short} {r.rate * 100:.1f} %"
        row(r.ziffer, f"{name}{vintage}", r.net, r.tax)
    d0 = Date.fromisoformat(report.date_from)
    d1 = Date.fromisoformat(report.date_to)
    for current in (True, False):
        tax = report.z383_tax if current else report.z382_tax
        in_force = (d1 >= RATE_CHANGE) if current else (d0 < RATE_CHANGE)
        if not tax and not in_force:
            continue
        net = report.z383_net if current else report.z382_net
        label = "Bezugsteuer (Art. 45 ff. MWSTG)" if current else "Bezugsteuer (bis 31.12.2023)"
        row(BEZUGSTEUER_ZIFFERN[0 if current else 1], label, net, tax)
    # The Saldosteuersatz form has no total-tax line and no input-VAT block:
    # part II goes straight from the turnover rows and the Bezugsteuer to the
    # Steuerforderung (MWST-Info 12, Ziff. 18.1.1 and 18.1.4).
    if report.vat_method != "saldo":
        row("399", "Total geschuldete Steuer", None, report.z399)
        main.add_section()
        for ziffer, label, always in _INPUT_ROWS:
            if always or values[ziffer]:
                shown = -values[ziffer] if ziffer in MINUS_ZIFFERN else values[ziffer]
                row(ziffer, label, None, shown)
        row("479", "Total Vorsteuer", None, report.z479)
    main.add_section()
    owed = report.z500 >= 0
    row(
        "500" if owed else "510",
        "Zu bezahlender Betrag" if owed else "Guthaben der steuerpflichtigen Person",
        None,
        report.z500 if owed else report.z510,
        style="owe" if owed else "refund",
    )
    if report.z900 or report.z910:
        main.add_section()
        if report.z900:
            row("900", "Subventionen, Tourismusabgaben u.a. (Art. 18 II a–c)", report.z900)
        if report.z910:
            row("910", "Spenden, Dividenden, Schadenersatz (Art. 18 II d–l)", report.z910)
    return main


def _violations_table(violations: list[Violation]) -> Table:
    t = Table(
        box=box.SIMPLE,
        title="Prüfung — Buchungen, die so nicht ins Formular passen",
        title_justify="left",
        title_style="warn",
    )
    t.add_column("Datum", style="muted", no_wrap=True)
    t.add_column("Payee")
    t.add_column("Narration")
    t.add_column("Problem")
    t.add_column("Erwartet", justify="right", no_wrap=True)
    t.add_column("Gebucht", justify="right", no_wrap=True)
    for v in violations:
        t.add_row(
            v.date,
            v.payee,
            v.narration,
            f"[warn]{v.message}[/]",
            ui.money(v.expected),
            ui.money(v.posted),
        )
    return t


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
    t.add_column("Ziffer", style="ziffer", justify="right", no_wrap=True)
    t.add_column("Original", justify="right", no_wrap=True)
    t.add_column("Kurs CHF", justify="right", no_wrap=True)
    t.add_column("CHF", justify="right", no_wrap=True)
    for r in lines:
        t.add_row(
            r.date,
            r.payee,
            r.narration,
            r.ziffer,
            f"{r.original:,.2f} {r.currency}",
            f"{r.rate:.5f}" if r.currency != "CHF" else "—",
            ui.money(r.chf),
        )
    t.add_section()
    t.add_row("", "", "[bold]Total[/]", "", "", "", f"[bold]{ui.money(total)}[/]")
    return t


# Group titles stay short: they sit in the (no-wrap) date column, so a long
# one would squeeze the payee out of an 80-column terminal.
_REVENUE_GROUPS: tuple[tuple[str, str], ...] = (
    ("220", "Exporte (Art. 23)"),
    ("225", "Meldeverfahren (Art. 38)"),
    ("230", "Ausgenommen (Art. 21)"),
    ("235", "Entgeltsminderungen"),
    ("280", "Diverses"),
    ("900", "Subventionen u.a."),
    ("910", "Spenden, Dividenden u.a."),
)


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

    def group(title: str, rows: list[RevenueLine], subtotal: Decimal, ziffer: str) -> None:
        t.add_row(f"[bold]{title}[/]", "", "", "")
        for r in rows:
            t.add_row(r.date, r.payee, f"{r.original:,.2f} {r.currency}", ui.money(r.chf))
        t.add_row("", "", f"[muted]Ziffer {ziffer}[/]", f"[bold]{ui.money(subtotal)}[/]")

    def group_saldo() -> None:
        t.add_row("[bold]Inland (steuerbar, brutto)[/]", "", "", "")
        for r in report.domestic:
            t.add_row(r.date, r.payee, f"{r.original:,.2f} {r.currency}", ui.money(r.chf))
        t.add_row("", "", "[muted]+ MWST (gesetzl. Satz)[/]", ui.money(report.output_vat))
        t.add_row("", "", "[muted]Ziffer 299[/]", f"[bold]{ui.money(report.z299)}[/]")

    if report.vat_method == "saldo":
        # Under SSS the declared Entgelt includes the statutory MWST, so show
        # it as its own line or the group would not add up to Ziffer 299.
        group_saldo()
    else:
        group("Inland (steuerbar)", report.domestic, report.z299, "299")
    if report.export:
        t.add_section()
        group("Ausland (Export, zero-rated)", report.export, report.z221, "221")
    totals: dict[str, Decimal] = {
        "220": report.z220,
        "225": report.z225,
        "230": report.z230,
        "235": report.z235,
        "280": report.z280,
        "900": report.z900,
        "910": report.z910,
    }
    for ziffer, title in _REVENUE_GROUPS:
        rows = [r for r in report.other_revenue if r.ziffer == ziffer]
        if not rows:
            continue
        t.add_section()
        group(title, rows, totals[ziffer], ziffer)
    return t
