"""Invoice data model (Pydantic), multi-format loading, and computation.

Authoring files (invoice, issuer, customer registry) may be YAML, TOML, or
JSON — picked by file extension. The Pydantic models double as the JSON
Schema source (`quints schema`), so the CLI, editors, and any future UI share
one contract.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, RootModel, field_validator, model_validator

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def money(v: Decimal, locale: str = "de_CH") -> str:
    """Amount formatted for `locale`, always two decimals.

    de_CH → 5'059.10, en → 5,059.10, es_ES → 5.059,10. CLDR types the Swiss
    group separator as U+2019/U+02BC; we normalise it to a plain ASCII
    apostrophe — how Swiss invoices are typeset, and a stable copy-paste."""
    from babel.numbers import format_decimal

    s = format_decimal(v, format="#,##0.00", locale=locale)
    return s.replace("’", "'").replace("ʼ", "'")


def number(v: Decimal, locale: str = "de_CH") -> str:
    """A bare decimal for `locale`, trailing zeros trimmed (quantities, rates).

    es_ES → 2,5 / 8,1; de_CH → 2.5 / 8.1."""
    from babel.numbers import format_decimal

    return format_decimal(v, locale=locale)


def round_step(v: Decimal, step: Decimal) -> Decimal:
    """Round to the nearest `step` (e.g. 0.05 cash rounding); 0 → 2 decimals."""
    if step == 0:
        return v.quantize(Decimal("0.01"), ROUND_HALF_UP)
    return (v / step).quantize(Decimal("1"), ROUND_HALF_UP) * step


# ── Swiss QRR reference (ESR mod-10 recursive check digit) ─────────────────────

_MOD10 = [0, 9, 4, 6, 8, 2, 7, 1, 3, 5]


def qrr_check_digit(number: str) -> str:
    carry = 0
    for ch in number:
        carry = _MOD10[(carry + int(ch)) % 10]
    return str((10 - carry) % 10)


def make_qrr(base: str) -> str:
    """A valid 27-digit QR-reference from an arbitrary string's digits."""
    digits = "".join(c for c in base if c.isdigit())[:26].rjust(26, "0")
    return digits + qrr_check_digit(digits)


def make_scor(base: str) -> str:
    """ISO 11649 Creditor Reference (SCOR) from an alphanumeric string.

    Valid with a regular IBAN on QR-bills and in SEPA credit transfers
    (check digits per modulo 97-10, like an IBAN)."""
    ref = "".join(c for c in base if c.isalnum()).upper()[:21]
    if not ref:
        raise ValueError("SCOR reference needs at least one alphanumeric character")
    num = "".join(str(int(c, 36)) for c in ref + "RF00")
    return f"RF{98 - int(num) % 97:02d}{ref}"


# ── model ─────────────────────────────────────────────────────────────────────


class Party(BaseModel):
    name: str
    address: list[str] = Field(min_length=1)
    country: str = "CH"
    vat_id: str | None = None

    @model_validator(mode="after")
    def _vat_id_checksum(self):
        if self.vat_id:
            from . import vatid

            vatid.validate(self.vat_id, self.country)
        return self


class CustomerVersion(Party):
    valid_from: date | None = None  # None → valid since forever


class Customer(BaseModel):
    """Registry entry: a flat party, or a dated `versions` history."""

    name: str | None = None
    address: list[str] | None = None
    country: str = "CH"
    vat_id: str | None = None
    versions: list[CustomerVersion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _flat_or_versioned(self):
        if bool(self.versions) == bool(self.name):
            raise ValueError("customer needs either flat fields or `versions`, not both")
        return self

    def at(self, on: date) -> Party:
        """The party data in force on `on` (for re-rendering old invoices)."""
        if not self.versions:
            if self.name is None or self.address is None:
                raise ValueError("flat customer entry needs both `name` and `address`")
            return Party(
                name=self.name, address=self.address, country=self.country, vat_id=self.vat_id
            )
        live = [
            v
            for v in sorted(self.versions, key=lambda v: v.valid_from or date.min)
            if (v.valid_from or date.min) <= on
        ]
        if not live:
            raise ValueError(f"no customer version valid on {on}")
        v = live[-1]
        return Party(name=v.name, address=v.address, country=v.country, vat_id=v.vat_id)


class CustomerRegistry(RootModel[dict[str, Customer]]):
    def resolve(self, ref: str, on: date) -> Party:
        try:
            return self.root[ref].at(on)
        except KeyError:
            raise ValueError(
                f"unknown customer {ref!r} (registry has: {', '.join(sorted(self.root)) or 'none'})"
            ) from None


class LineItem(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    unit: str = ""

    @property
    def total(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"), ROUND_HALF_UP)


class VatBlock(BaseModel):
    rate: Decimal = Decimal("8.1")


class Invoice(BaseModel):
    number: str
    kind: Literal["domestic", "export"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    issue_date: date
    customer: str | Party  # str → key into the customer registry
    items: list[LineItem] = Field(min_length=1)
    supply: str = ""
    locale: str = "de_CH"  # CLDR locale for labels + number/date formatting
    vat: VatBlock = Field(default_factory=VatBlock)
    reference: str | None = None
    notes: list[str] = Field(default_factory=list)
    round_5: bool | None = None  # None → 0.05 rounding iff currency is CHF
    terms_days: int | None = 30  # None → no payment-terms line
    # Export only. None → reverse charge expected (EU B2B default); requires the
    # customer's VAT number (Art. 196, 226(4)+(11a) EU VAT Directive). Set false
    # for customers outside a reverse-charge regime (e.g. US) to drop the note.
    reverse_charge: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_language(cls, data: Any) -> Any:
        if isinstance(data, dict) and "language" in data:
            raise ValueError(
                "`language` is replaced by `locale` — use a full CLDR locale, "
                "e.g. locale: es_ES (Spanish/Spain), de_CH, or en"
            )
        return data

    @field_validator("locale")
    @classmethod
    def _known_locale(cls, v: str) -> str:
        from babel import Locale, UnknownLocaleError

        from .labels import LABELS

        try:
            Locale.parse(v)
        except (ValueError, UnknownLocaleError) as e:
            raise ValueError(f"unknown locale {v!r} (e.g. de_CH, en, es_ES)") from e
        lang = v.split("_")[0]
        if lang not in LABELS:
            raise ValueError(
                f"no invoice labels for language {lang!r} of locale {v!r} "
                f"(available: {', '.join(sorted(LABELS))})"
            )
        return v

    @property
    def language(self) -> str:
        """The label language: the locale's language subtag (es_ES → es)."""
        return self.locale.split("_")[0]

    @property
    def resolved_customer(self) -> Party:
        """The customer as a full `Party`; raises if it is still an unresolved
        registry reference (load via `load_invoice` with a customer registry)."""
        if isinstance(self.customer, str):
            raise ValueError(
                f"invoice {self.number} customer {self.customer!r} is an unresolved "
                f"registry reference — load it through `load_invoice` with a "
                f"customer registry"
            )
        return self.customer

    @property
    def vat_rate(self) -> Decimal:
        return self.vat.rate

    @property
    def rounds_to_5(self) -> bool:
        return self.round_5 if self.round_5 is not None else self.currency == "CHF"


class BankAccount(BaseModel):
    iban: str = ""  # regular IBAN — SEPA/international credit transfers
    qr_iban: str = ""  # QR-IID variant — Swiss QR-bill with QRR reference ONLY
    bic: str | None = None

    @field_validator("iban", "qr_iban")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.replace(" ", "")


class Brand(BaseModel):
    accent: str = "#123c3a"
    font: str = "Liberation Sans"
    font_display: str | None = None  # title/wordmark family; defaults to `font`
    font_display_stretch: int = 100  # CSS-style font-stretch % (125 → Expanded cut)
    font_dir: str | None = None  # bundled fonts dir passed to typst (repo-relative)
    logo: str | None = None


class Issuer(BaseModel):
    name: str
    address: list[str] = Field(min_length=1)
    vat_id: str
    country: str = "CH"
    email: str | None = None
    phone: str | None = None
    bank: dict[str, BankAccount] = Field(default_factory=dict)
    brand: Brand = Field(default_factory=Brand)

    def account(self, currency: str) -> BankAccount:
        try:
            return self.bank[currency]
        except KeyError:
            raise ValueError(
                f"no bank account configured for {currency} "
                f"(issuer has: {', '.join(sorted(self.bank)) or 'none'})"
            ) from None


class Totals(BaseModel):
    subtotal: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    unrounded: Decimal
    rounding: Decimal
    grand_total: Decimal


# ── loading ───────────────────────────────────────────────────────────────────


def load_mapping(path: Path) -> dict[str, object]:
    """Parse a .yaml/.yml/.toml/.json file into a plain mapping."""
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        return yaml.safe_load(path.read_text()) or {}
    if suffix == ".toml":
        with open(path, "rb") as f:
            return tomllib.load(f)
    if suffix == ".json":
        return json.loads(path.read_text())
    raise ValueError(f"unsupported invoice file format {suffix!r} (use .yaml/.toml/.json)")


def document_path(inv: Invoice, income_account: str, root: Path = Path("documents")) -> Path:
    """Where the rendered PDF is filed, per the beancount documents convention.

    `option "documents"` discovery wants `<root>/<Account/Tree>/YYYY-MM-DD.…`;
    the date prefix is what links the file to the account, and the
    `<payee>.<number>` tail keeps the folder scannable:
    `documents/Income/…/Domestic/2026-07-02.acme-ag.INV2026014.pdf`.
    """
    customer = inv.customer.name if isinstance(inv.customer, Party) else inv.customer
    slug = re.sub(r"[^a-z0-9]+", "-", customer.lower()).strip("-") or "customer"
    name = f"{inv.issue_date.isoformat()}.{slug}.{inv.number}.pdf"
    return root.joinpath(*income_account.split(":")) / name


def load_issuer(path: Path) -> Issuer:
    return Issuer.model_validate(load_mapping(path))


def load_customers(path: Path) -> CustomerRegistry:
    return CustomerRegistry.model_validate(load_mapping(path))


def load_invoice(path: Path, customers: CustomerRegistry | None = None) -> Invoice:
    """Load an invoice; a string `customer` is resolved through the registry
    as of the issue date (old invoices keep the address in force back then)."""
    inv = Invoice.model_validate(load_mapping(path))
    if isinstance(inv.customer, str):
        if customers is None:
            raise ValueError(
                f"invoice references customer {inv.customer!r} but no customer "
                f"registry was found (invoicing/customers.yaml)"
            )
        inv.customer = customers.resolve(inv.customer, inv.issue_date)
    return inv


# ── computation ───────────────────────────────────────────────────────────────


def compute(inv: Invoice) -> Totals:
    subtotal = sum((it.total for it in inv.items), Decimal("0"))
    if inv.kind == "export":
        rate = Decimal("0")
        vat = Decimal("0")
    else:
        rate = inv.vat_rate
        vat = (subtotal * rate / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    unrounded = subtotal + vat
    grand = round_step(unrounded, Decimal("0.05") if inv.rounds_to_5 else Decimal("0"))
    return Totals(
        subtotal=subtotal,
        vat_rate=rate,
        vat_amount=vat,
        unrounded=unrounded,
        rounding=grand - unrounded,
        grand_total=grand,
    )
