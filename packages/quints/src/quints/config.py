"""quints configuration — the entity lives in ``quints.toml``, not in code.

Defaults are a neutral Swiss GmbH chart of accounts, so any repo following
the ``CH:GmbH`` namespacing works out of the box; everything entity-specific
(name, VAT registration, importer credentials/rules) comes from
``quints.toml`` — a stranger with their own beancount file and a
``quints.toml`` gets correct reports without touching this package.

Precedence: an explicit ``--config`` path > ``./quints.toml`` > built-in
defaults. VAT *rates* are deliberately not configurable — they are law and
live in :data:`quints.ledger.VAT_RATES`.

Modules take a ``cfg`` parameter (tests construct :class:`Config` inline) and
fall back to :func:`get`, the process-wide config the CLI resolves once.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from datetime import date as Date
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEFAULT_PATH = Path("quints.toml")


@dataclass(frozen=True)
class UbsImport:
    """[import.ubs] — MT940 statements for one CHF bank account."""

    account: str = "Assets:CH:GmbH:Current:UBS:CHF"
    iban: str | None = None                 # required to identify statements
    currency: str = "CHF"
    rules: tuple[tuple[str, str, str], ...] = ()   # (payee regex, account, flag)


@dataclass(frozen=True)
class WiseImport:
    """[import.wise] — Wise balance statements, one account per currency."""

    accounts: tuple[tuple[str, str], ...] = (      # (currency, account)
        ("CHF", "Assets:CH:GmbH:Current:Wise:CHF"),
        ("EUR", "Assets:CH:GmbH:Current:Wise:EUR"),
        ("USD", "Assets:CH:GmbH:Current:Wise:USD"),
    )
    fees_account: str = "Expenses:CH:GmbH:BankFees:Wise"
    holder: str | None = None               # businessName filter (multi-profile tokens)
    rules: tuple[tuple[str, str, str], ...] = ()

    @property
    def account_map(self) -> dict[str, str]:
        return dict(self.accounts)


@dataclass(frozen=True)
class StripeImport:
    """[import.stripe] — Stripe balance transactions for one account."""

    accounts: tuple[tuple[str, str], ...] = (("EUR", "Assets:CH:GmbH:Current:Stripe:EUR"),)
    fees_account: str = "Expenses:CH:GmbH:BankFees:Stripe"
    tax_account: str = "Assets:CH:GmbH:Tax:InputVAT"  # VAT within Stripe's own fees
    account_id: str | None = None            # required: acct_… this key must belong to
    rules: tuple[tuple[str, str, str], ...] = ()

    @property
    def account_map(self) -> dict[str, str]:
        return dict(self.accounts)


@dataclass(frozen=True)
class Config:
    # [entity]
    entity_name: str = "Example GmbH"
    vat_method: str = "effective"           # "saldo" would need a different Form-310 mapping
    vat_registered_since: Date | None = None
    operating_currency: str = "CHF"
    # [ledger]
    ledger_main: Path = Path("main.bean")
    ledger_prices: Path = Path("prices.bean")
    # [accounts]
    input_vat: str = "Assets:CH:GmbH:Tax:InputVAT"
    output_vat: str = "Liabilities:CH:GmbH:Tax:OutputVAT"
    bezugsteuer: str = "Liabilities:CH:GmbH:Tax:Bezugsteuer"
    payable_vat: str = "Liabilities:CH:GmbH:Tax:PayableVAT"
    income_prefix: str = "Income:CH:GmbH"
    export_marker: str = ":Export"
    entity_marker: str = ":CH:GmbH:"
    fx_gain: str = "Income:CH:GmbH:FX:CurrencyGain"
    fx_loss: str = "Expenses:CH:GmbH:FX:CurrencyLoss"
    receivable: str = "Assets:CH:GmbH:Receivable:Trade"
    rounding_income: str = "Income:CH:GmbH:Rounding"
    income_domestic: str = "Income:CH:GmbH:Consulting:External:Domestic"
    income_export: str = "Income:CH:GmbH:Consulting:External:Export"
    # [report]
    report_language: str = "en"
    # [import.*] — statement importers (plan 2); None = not configured
    import_ubs: UbsImport | None = None
    import_wise: WiseImport | None = None
    import_stripe: StripeImport | None = None


def _rules(section: dict) -> tuple[tuple[str, str, str], ...]:
    return tuple((r[0], r[1], r[2]) for r in section.get("rules", ()))


def _accounts_map(section: dict, default) -> tuple[tuple[str, str], ...]:
    if "accounts" not in section:
        return default
    return tuple(sorted(section["accounts"].items()))


def _import_sections(raw: dict) -> dict:
    imports = raw.get("import") or {}
    updates: dict = {}
    if "ubs" in imports:
        s = imports["ubs"]
        updates["import_ubs"] = UbsImport(
            account=s.get("account", UbsImport.account),
            iban=s.get("iban"),
            currency=s.get("currency", UbsImport.currency),
            rules=_rules(s),
        )
    if "wise" in imports:
        s = imports["wise"]
        updates["import_wise"] = WiseImport(
            accounts=_accounts_map(s, WiseImport.accounts),
            fees_account=s.get("fees_account", WiseImport.fees_account),
            holder=s.get("holder"),
            rules=_rules(s),
        )
    if "stripe" in imports:
        s = imports["stripe"]
        updates["import_stripe"] = StripeImport(
            accounts=_accounts_map(s, StripeImport.accounts),
            fees_account=s.get("fees_account", StripeImport.fees_account),
            tax_account=s.get("tax_account", StripeImport.tax_account),
            account_id=s.get("account_id"),
            rules=_rules(s),
        )
    return updates


def _from_mapping(raw: dict) -> Config:
    entity = raw.get("entity") or {}
    ledger_ = raw.get("ledger") or {}
    accounts = raw.get("accounts") or {}
    report = raw.get("report") or {}
    cfg = Config()
    updates: dict = {}

    def take(section: dict, key: str, field: str, cast=None):
        if key in section:
            value = section[key]
            updates[field] = cast(value) if cast else value

    take(entity, "name", "entity_name")
    take(entity, "vat_method", "vat_method")
    take(entity, "vat_registered_since", "vat_registered_since")
    take(entity, "operating_currency", "operating_currency")
    take(ledger_, "main", "ledger_main", Path)
    take(ledger_, "prices", "ledger_prices", Path)
    for key in ("input_vat", "output_vat", "bezugsteuer", "payable_vat",
                "income_prefix", "export_marker", "entity_marker",
                "fx_gain", "fx_loss", "receivable", "rounding_income",
                "income_domestic", "income_export"):
        take(accounts, key, key)
    take(report, "language", "report_language")
    updates.update(_import_sections(raw))
    return replace(cfg, **updates)


def load(path: Path | None = None) -> Config:
    """Load ``path``, or ``./quints.toml`` if present, else defaults."""
    if path is None:
        path = DEFAULT_PATH if DEFAULT_PATH.exists() else None
    if path is None:
        return Config()
    with open(path, "rb") as f:
        return _from_mapping(tomllib.load(f))


_current: Config | None = None
_path: Path | None = None


def set_path(path: Path | None) -> None:
    """CLI hook: remember --config and invalidate the cached config."""
    global _path, _current
    _path = path
    _current = None


def get() -> Config:
    """The process-wide config (cached; honors a previous :func:`set_path`)."""
    global _current
    if _current is None:
        _current = load(_path)
    return _current
