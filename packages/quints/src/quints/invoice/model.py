"""Invoice data model (Pydantic), multi-format loading, and computation.

Authoring files (invoice, issuer, customer registry) may be YAML, TOML, or
JSON — picked by file extension. The Pydantic models double as the JSON
Schema source (`quints schema`), so the CLI, editors, and any future UI share
one contract.
"""

from __future__ import annotations

import calendar
import json
import re
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    RootModel,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from . import bank

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


class ExtraReference(BaseModel):
    """A labelled reference the customer's side needs quoted on the invoice.

    The long tail nobody can model up front — a Leitweg-ID, an Italian
    CIG/CUP, a cost centre, a contract or framework-agreement number. It is
    printed next to the invoice number with the label as given, so it reads
    the way the customer's accounts-payable department expects it to."""

    label: str = Field(min_length=1, description="Printed as given, e.g. 'Leitweg-ID'.")
    value: str = Field(min_length=1, description="The reference itself.")


class SupplyPeriod(BaseModel):
    """When the supply was made: its first and last day, the same day for one.

    Every invoice states it. Swiss VAT law asks for the date or period of the
    supply wherever it differs from the invoice date (Art. 26 Abs. 2 lit. c
    MWSTG), the EU for the date the supply was made or completed (Art. 226(7)
    VAT Directive), and the payer books input tax by it — which is why it is
    structured rather than free text: it is printed in the invoice's locale and
    carried in the QR-bill's billing information (Swico S1 /31/).

    Written in an invoice file as a day (`2026-07-15`), a calendar month
    (`2026-07`), or a period (`{from: 2026-07-01, to: 2026-09-30}`)."""

    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, extra="forbid")

    start: date = Field(validation_alias="from", description="First day of the supply.")
    end: date = Field(validation_alias="to", description="Last day of the supply.")

    @model_validator(mode="after")
    def _ordered(self) -> SupplyPeriod:
        if self.end < self.start:
            raise ValueError(f"supply period ends ({self.end}) before it starts ({self.start})")
        return self

    @classmethod
    def day(cls, d: date) -> SupplyPeriod:
        return cls(start=d, end=d)

    @classmethod
    def month(cls, year: int, month: int) -> SupplyPeriod:
        last = calendar.monthrange(year, month)[1]
        return cls(start=date(year, month, 1), end=date(year, month, last))

    @property
    def is_month(self) -> bool:
        return self == SupplyPeriod.month(self.start.year, self.start.month)

    def text(self, locale: str) -> str:
        """As printed: `Juli 2026`, `05.06.2026`, `1. Juni – 15. Juli 2026`."""
        from babel.dates import format_date, format_interval, format_skeleton

        if self.start == self.end:
            return format_date(self.start, format="medium", locale=locale)
        if self.is_month:
            return format_skeleton("yMMMM", self.start, locale=locale)
        return format_interval(self.start, self.end, "yMMMd", locale=locale)

    def swico(self) -> str:
        """Swico S1 /31/: `YYMMDD` for a day, `YYMMDDYYMMDD` for a period."""
        first = self.start.strftime("%y%m%d")
        return first if self.start == self.end else first + self.end.strftime("%y%m%d")


_MONTH = re.compile(r"(\d{4})-(\d{2})")
_LEGACY_MONTH = re.compile(r"(\w+)\.?\s+(\d{4})")


def _month_named(word: str) -> int | None:
    """The month a written-out name means in any invoice language, or None."""
    from babel.dates import get_month_names

    from .labels import LABELS

    for lang in LABELS:
        for width in ("wide", "abbreviated"):
            for num, name in get_month_names(width, locale=lang).items():
                if name.lower().rstrip(".") == word.lower():
                    return num
    return None


def _parse_supply(v: object) -> object:
    """A day, a `YYYY-MM` month, or a `{from, to}` period → `SupplyPeriod`.

    YAML hands a day over as a `date`; TOML and JSON as text, so an ISO day
    string is read too. Free text (`Juli 2026`, the pre-structured form) is
    refused with the value to write instead."""
    if isinstance(v, date):
        return SupplyPeriod.day(v)
    if not isinstance(v, str):
        return v  # a {from, to} mapping, or a SupplyPeriod already
    text = v.strip()
    if m := _MONTH.fullmatch(text):
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"supply {v!r}: there is no month {month}")
        return SupplyPeriod.month(year, month)
    try:
        return SupplyPeriod.day(date.fromisoformat(text))
    except ValueError:
        pass
    hint = (
        "supply: 2026-07 (a month), supply: 2026-07-15 (a day), or "
        "supply: {from: 2026-07-01, to: 2026-09-30}"
    )
    if (m := _LEGACY_MONTH.fullmatch(text)) and (month := _month_named(m.group(1))):
        hint = f"supply: {m.group(2)}-{month:02d}"
    raise ValueError(
        f"supply {v!r} is free text, but the supply period is structured — it "
        f"is printed in the invoice's language and carried in the QR-bill for "
        f"the payer's VAT booking. Write {hint}"
    )


Supply = Annotated[
    SupplyPeriod,
    BeforeValidator(
        _parse_supply,
        json_schema_input_type=date
        | Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
        | SupplyPeriod,
    ),
]


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
    supply: Supply = Field(
        description=(
            "When the supply was made — a day (2026-07-15), a calendar month "
            "(2026-07), or a period ({from: 2026-07-01, to: 2026-09-30}). Printed "
            "on the invoice and carried in the QR-bill (Swico S1 /31/)."
        ),
    )
    locale: str = "de_CH"  # CLDR locale for labels + number/date formatting
    vat: VatBlock = Field(default_factory=VatBlock)
    # The payment reference. Unset is the normal case: quints derives it from
    # the invoice number in the scheme the bank account resolves to (see
    # `reference.py`). Set it only to re-issue an invoice that went out with a
    # reference from elsewhere; it is validated at load and must match the
    # account's scheme.
    reference: str | None = Field(
        default=None,
        description=(
            "Payment reference override — a QR reference (all digits) or a SCOR "
            "creditor reference (RF…), validated by its check digits. Leave unset "
            "to derive it from the invoice number."
        ),
    )
    customer_reference: str | None = Field(
        default=None,
        description=(
            "The customer's own reference for this invoice — their PO or order "
            "number. Printed next to the invoice number and carried in the "
            "QR-bill's structured billing information (Swico S1 /20/) for their "
            "accounts-payable software."
        ),
    )
    references: list[ExtraReference] = Field(
        default_factory=list,
        description=(
            "Further labelled references to print, for whatever the customer's "
            "side demands (Leitweg-ID, CIG/CUP, cost centre, contract number)."
        ),
    )
    notes: list[str] = Field(default_factory=list)
    round_5: bool | None = None  # None → 0.05 rounding iff currency is CHF
    terms_days: int | None = 30  # None → no payment-terms line
    # Export only. None → reverse charge expected (EU B2B default); requires the
    # customer's VAT number (Art. 196, 226(4)+(11a) EU VAT Directive). Set false
    # for customers outside a reverse-charge regime (e.g. US) to drop the note.
    reverse_charge: bool | None = None

    # The registry key the customer was resolved from (set by `load_invoice`);
    # private so it never appears in the published authoring-file schema.
    _customer_key: str | None = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_language(cls, data: Any) -> Any:
        if isinstance(data, dict) and "language" in data:
            raise ValueError(
                "`language` is replaced by `locale` — use a full CLDR locale, "
                "e.g. locale: es_ES (Spanish/Spain), de_CH, or en"
            )
        return data

    @field_validator("number")
    @classmethod
    def _usable_number(cls, v: str) -> str:
        # Every payment reference is derived from the number's ASCII letters
        # and digits, so a number with nothing to derive from — or too much for
        # any scheme to carry — fails here rather than at render time.
        from .reference import SCOR_MAX_CHARS, compact_number

        core = compact_number(v)
        if not core:
            raise ValueError(
                f"invoice number {v!r} has no ASCII letter or digit — the payment "
                f"reference is derived from those"
            )
        if len(core) > SCOR_MAX_CHARS:
            raise ValueError(
                f"invoice number {v!r} is {len(core)} alphanumeric characters; a "
                f"payment reference carries at most {SCOR_MAX_CHARS} (12 for a QR "
                f"reference)"
            )
        return v

    @field_validator("reference", mode="before")
    @classmethod
    def _valid_reference(cls, v: object) -> str | None:
        """Validate an override at load, and keep it in its compact form.

        `mode="before"`: an unquoted all-digit QR reference arrives as a YAML
        integer, which is accepted only at its full 27 digits."""
        if v is None:
            return None
        from .reference import QRR_LENGTH, parse_reference, yaml_digits

        raw = yaml_digits(v, QRR_LENGTH, "reference")
        if not raw.strip():
            return None
        return parse_reference(raw).value

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

    def resolve_customer_ref(self, customers: CustomerRegistry) -> None:
        """Replace a registry-key `customer` with the party in force on the
        issue date, remembering the key for `customer_slug`."""
        if isinstance(self.customer, str):
            self._customer_key = self.customer
            self.customer = customers.resolve(self.customer, self.issue_date)

    @property
    def customer_slug(self) -> str:
        """Filename-safe customer part: the registry key when the invoice was
        loaded through one (already short and ASCII), else the slugified name
        — which strips non-ASCII letters entirely (keinois OÜ → keinois-o)."""
        if self._customer_key:
            return self._customer_key
        name = self.customer.name if isinstance(self.customer, Party) else self.customer
        return slugify(name)

    @property
    def vat_rate(self) -> Decimal:
        return self.vat.rate

    @property
    def rounds_to_5(self) -> bool:
        return self.round_5 if self.round_5 is not None else self.currency == "CHF"


class BankAccount(BaseModel):
    """Where an invoice asks to be paid, for one currency.

    Every number here is validated the way a bank would — a transposed IBAN or
    a malformed BIC fails at load, not after the PDF has left the building.
    `bic` is mandatory for any currency you invoice abroad in: rendering an
    export invoice without one is refused, because a customer who has to look
    the BIC up themselves is a customer who can get it wrong and have the
    transfer returned. `quints iban` checks a pair before it ships.
    """

    iban: str = ""  # regular IBAN — SEPA/international credit transfers
    qr_iban: str = ""  # QR-IID variant — Swiss QR-bill with QRR reference ONLY
    bic: str | None = None  # BIC/SWIFT — required on export invoices
    holder: str | None = None  # account holder, when it is not the issuer
    bank_name: str | None = None  # the institution, e.g. "Wise Europe SA, Brussels"
    reference: Literal["scor", "qrr"] | None = Field(
        default=None,
        description=(
            "Which payment reference invoices paid into this account carry. "
            "`qrr` is the Swiss QR reference, numeric, paid into `qr_iban` (CHF "
            "only; needs the bank's `qr_reference_id`); `scor` is the readable "
            "ISO 11649 creditor reference (RF…) paid into the regular `iban`. "
            "Unset: `qrr` when the account has a `qr_iban` and the invoice is in "
            "CHF, otherwise `scor`."
        ),
    )
    qr_reference_id: str | None = Field(
        default=None,
        description=(
            "The six-digit identification your bank assigns you (UBS: BESR-ID / "
            "'ID number'). Banks require it as the first six digits of every QR "
            "reference you issue; without it they are zeros."
        ),
    )

    @field_validator("qr_reference_id", mode="before")
    @classmethod
    def _check_qr_reference_id(cls, v: object) -> str | None:
        # `mode="before"`: `qr_reference_id: 123456` is a YAML integer; fine at
        # six digits, refused (quote it) when a leading zero made it shorter.
        if v is None:
            return None
        from .reference import QRR_ID_DIGITS, check_qr_reference_id, yaml_digits

        raw = yaml_digits(v, QRR_ID_DIGITS, "qr_reference_id")
        if not raw.strip():
            return None
        return check_qr_reference_id(raw)

    @field_validator("iban", "qr_iban")
    @classmethod
    def _check_iban(cls, v: str, info: ValidationInfo) -> str:
        return bank.clean_iban(v, info.field_name or "iban") if v.strip() else ""

    @field_validator("bic")
    @classmethod
    def _check_bic(cls, v: str | None) -> str | None:
        return bank.clean_bic(v) if v and v.strip() else None


# Every hex form Typst's `rgb()` takes: RGB, RGBA, RRGGBB, RRGGBBAA. `accent`
# used to be a bare `str` handed straight to `rgb()`, so anything shorthand that
# rendered before still loads; only strings Typst would have rejected anyway are
# caught here, and now at config-load time instead of mid-render.
HexColor = Annotated[
    str, StringConstraints(pattern=r"^#([0-9A-Fa-f]{3,4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
]


class Brand(BaseModel):
    """Typography and colour tokens the template renders with.

    The defaults are Sealambda's brand — the Tyrian palette and the three OFL
    families bundled with the package (Geist body, Newsreader title, Geist
    Mono figures), so an unconfigured issuer still gets a designed invoice
    with fixed-width, digit-aligned amounts. An issuer with their own palette
    and families overrides them all.
    """

    accent: HexColor = "#6b1f4a"  # title, amount due, leading rules
    font: str = "Geist"
    font_display: str | None = "Newsreader"  # title/wordmark family; None → `font`
    font_display_stretch: int = 100  # CSS-style font-stretch % (125 → Expanded cut)
    # Title weight. Not every display family ships every cut — Mona Sans
    # Expanded starts at Medium, so asking for "regular" silently drops back to
    # the normal-width face; a serif title usually wants "regular".
    font_display_weight: Literal["regular", "medium", "semibold", "bold"] = "regular"
    font_mono: str | None = "Geist Mono"  # figures, IBAN, reference; None → `font`
    font_dir: str | None = None  # bundled fonts dir passed to typst (repo-relative)
    logo: str | None = None
    logo_height: float = 12.0  # mm, as placed in the header

    ink: HexColor = "#1c1618"  # body copy
    subtle: HexColor = "#655753"  # labels and secondary text
    rule: HexColor = "#cfbdb7"  # hairlines
    panel: HexColor = "#ede4e0"  # fill behind bounded blocks


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


def slugify(name: str, fallback: str = "customer") -> str:
    """Filename-safe slug: lowercase ASCII words joined by hyphens.

    The one place the convention lives, so every filename built from a party
    name — a rendered invoice, a fetched one — spells it the same way.
    """
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or fallback


def document_path(inv: Invoice, income_account: str, root: Path = Path("documents")) -> Path:
    """Where the rendered PDF is filed, per the beancount documents convention.

    `option "documents"` discovery wants `<root>/<Account/Tree>/YYYY-MM-DD.…`;
    the date prefix is what links the file to the account, and the
    `<customer>.<number>` tail keeps the folder scannable:
    `documents/Income/…/Domestic/2026-07-02.acme.INV2026014.pdf`.
    """
    name = f"{inv.issue_date.isoformat()}.{inv.customer_slug}.{inv.number}.pdf"
    return root.joinpath(*income_account.split(":")) / name


def _resolve_asset(p: str | None, issuer_dir: Path) -> str | None:
    """Resolve a brand asset path (logo, font_dir) from the issuer config.

    As given first (absolute, or relative to the cwd — the historical
    contract), then relative to the issuer file's directory, then to its
    parent (the project root, for `invoicing/…`-style paths) — so the
    scaffolded config renders with its wordmark from any working directory."""
    if p is None or Path(p).is_absolute() or Path(p).exists():
        return p
    for root in (issuer_dir, issuer_dir.parent):
        if (root / p).exists():
            return str(root / p)
    return p


def load_issuer(path: Path) -> Issuer:
    issuer = Issuer.model_validate(load_mapping(path))
    issuer.brand.logo = _resolve_asset(issuer.brand.logo, path.parent)
    issuer.brand.font_dir = _resolve_asset(issuer.brand.font_dir, path.parent)
    return issuer


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
        inv.resolve_customer_ref(customers)
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
