"""Scaffold a new quints project: questionnaire answers → a runnable ledger.

This is deterministic on purpose. ``quints`` is a tool; the AI is always a
*caller* of it, never a callee. So ``init`` emits a **backbone** — the parts
of a Swiss entity's books that are a published standard, not a judgement
call: the directory layout, ``quints.toml``, a ``pyproject.toml`` (so ``uv
sync`` makes plain ``bean-check``/``fava`` work, not only ``quints``), the
statutory accounts (InputVAT / OutputVAT / Bezugsteuer / VAT-due, FX
gain/loss) and a KMU Kontenrahmen skeleton. The ledger follows the reference
layout: ``main.bean`` holds options and includes, the chart lives in
``accounts.bean``, transactions in ``books/<year>.bean`` (one file per fiscal
year, glob-included).

The legal form (``gmbh``, ``ag``, ``einzelfirma``) follows the official KMU
Kontenrahmen, which prints per-form variants only for Klasse 28 (equity): a
GmbH/AG gets share capital (2800), an Einzelunternehmen gets owner's equity
(2800), capital contributions (2820) and the Privat account (2850). The form
also names the account namespace (``:CH:GmbH:``, ``:CH:AG:``,
``:CH:Einzelfirma:``). Every entity account is opened with a four-digit
``kmu:`` code (seeded from :data:`quints.kmu.KMU_NAMES`) so
``quints.plugins.kmu`` and the statutory statements work out of the box.

The **leaves** — business-specific income/expense sub-trees, importer rules —
are left for an agent (Claude Code, Codex, the Agent SDK) to tailor by
conversation, guided by the ``AGENTS.md`` this emits into the project.

The compute layer (:func:`plan`) is presentation-free: answers in, an ordered
list of :class:`ScaffoldFile` out, no I/O. :func:`write` does the I/O. Running
:func:`plan` with ``include_samples=True`` produces the repo's ``examples/``
project, which doubles as the CI smoke test — so the example is regenerated,
never hand-maintained. The samples include an ``invoicing/`` set (issuer,
customer registry, a domestic QR-bill and a reverse-charge export invoice)
tied to the demo quarter's bookings, so ``quints invoice`` reconciles clean.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from . import config, kmu

# Accounts the backbone adds beyond the configurable set in `config.Config`,
# written in the GmbH namespace; `_sub` re-homes them for other legal forms.
_IT_HOSTING = "Expenses:CH:GmbH:IT:Hosting"
_PRIMARY_BANK = "Assets:CH:GmbH:Current:UBS:CHF"
_WISE_EUR = "Assets:CH:GmbH:Current:Wise:EUR"

_KNOWN_IMPORTERS = ("ubs", "wise", "stripe")


@dataclass(frozen=True)
class Answers:
    """The questionnaire result — everything ``plan`` needs, nothing more."""

    entity_name: str = "Example GmbH"
    legal_form: str = "gmbh"  # key into config.LEGAL_FORMS
    vat_method: str = "effective"  # "saldo" is not supported yet
    vat_registered_since: Date | None = Date(2026, 1, 1)
    operating_currency: str = "CHF"
    report_language: str = "en"
    importers: tuple[str, ...] = ()  # subset of _KNOWN_IMPORTERS
    include_samples: bool = False


def answers_from_mapping(raw: dict[str, Any]) -> Answers:
    """Build :class:`Answers` from a parsed ``answers.toml`` mapping.

    Keys mirror the field names; unknown keys are ignored, missing keys keep
    their default. ``vat_registered_since`` accepts a ``datetime.date`` (TOML
    date literal) or an ISO string; ``importers`` is a list of names.
    """
    d = Answers()
    since = raw.get("vat_registered_since", d.vat_registered_since)
    if isinstance(since, str):
        since = Date.fromisoformat(since)
    importers = tuple(raw.get("importers", d.importers))
    return Answers(
        entity_name=raw.get("entity_name", d.entity_name),
        legal_form=str(raw.get("legal_form", d.legal_form)).lower(),
        vat_method=raw.get("vat_method", d.vat_method),
        vat_registered_since=since,
        operating_currency=raw.get("operating_currency", d.operating_currency),
        report_language=raw.get("report_language", d.report_language),
        importers=importers,
        include_samples=raw.get("include_samples", d.include_samples),
    )


def load_answers(path: Path) -> Answers:
    """Parse a TOML answer-file into :class:`Answers` (3.10-safe TOML)."""
    with open(path, "rb") as fh:
        return answers_from_mapping(tomllib.load(fh))


@dataclass(frozen=True)
class ScaffoldFile:
    """One file to materialise, path relative to the project root."""

    path: Path
    content: str


@dataclass(frozen=True)
class _Account:
    name: str
    code: str  # four-digit KMU Kontenrahmen code
    currencies: tuple[str, ...] = ()  # () = no constraint (multi-currency)


class InitError(ValueError):
    """Answers that cannot produce a valid project."""


# ── the backbone ─────────────────────────────────────────────────────────────


def _component(answers: Answers) -> str:
    """The account-name component for the legal form, e.g. ``GmbH``."""
    return config.LEGAL_FORMS[answers.legal_form]


def _sub(name: str, component: str) -> str:
    """Re-home a default (GmbH-namespaced) account name to the legal form."""
    return name.replace(":CH:GmbH", f":CH:{component}")


def _open_date(answers: Answers) -> Date:
    return answers.vat_registered_since or Date(2026, 1, 1)


def _cfg(answers: Answers) -> config.Config:
    """The `config.Config` the emitted `quints.toml` resolves to."""
    base = config.Config()
    c = _component(answers)
    return config.Config(
        entity_name=answers.entity_name,
        legal_form=answers.legal_form,
        vat_method=answers.vat_method,
        vat_registered_since=answers.vat_registered_since,
        operating_currency=answers.operating_currency,
        report_language=answers.report_language,
        input_vat=_sub(base.input_vat, c),
        output_vat=_sub(base.output_vat, c),
        bezugsteuer=_sub(base.bezugsteuer, c),
        payable_vat=_sub(base.payable_vat, c),
        income_prefix=_sub(base.income_prefix, c),
        entity_marker=f":CH:{c}:",
        fx_gain=_sub(base.fx_gain, c),
        fx_loss=_sub(base.fx_loss, c),
        receivable=_sub(base.receivable, c),
        rounding_income=_sub(base.rounding_income, c),
        income_domestic=_sub(base.income_domestic, c),
        income_export=_sub(base.income_export, c),
    )


def _equity(answers: Answers) -> list[_Account]:
    """The Klasse-28 block for the legal form (the official per-form variant).

    Juristische Personen open share capital (2800); an Einzelunternehmen has
    no share capital or statutory reserves — owner's equity (2800), capital
    contributions/withdrawals (2820) and the Privat account (2850) instead.
    """
    c = _component(answers)
    oc = answers.operating_currency
    if answers.legal_form == "einzelfirma":
        return [
            _Account(f"Equity:CH:{c}:Capital", "2800", (oc,)),
            _Account(f"Equity:CH:{c}:Contributions", "2820", (oc,)),
            _Account(f"Equity:CH:{c}:Private", "2850", (oc,)),
        ]
    return [_Account(f"Equity:CH:{c}:Capital:Share", "2800", (oc,))]


def _backbone(answers: Answers) -> list[_Account]:
    """The KMU account skeleton, deduped, in a stable order.

    Every account carries a ``kmu:`` code that exists in
    :data:`quints.kmu.KMU_NAMES`, so the statutory statements name it and the
    plugin accepts it.
    """
    cfg = _cfg(answers)
    c = _component(answers)
    oc = answers.operating_currency
    accounts: list[_Account] = [
        _Account(_sub(_PRIMARY_BANK, c), "1020", ("CHF",)),
        _Account(cfg.receivable, "1100"),
        _Account(cfg.input_vat, "1170", (oc,)),
        _Account(cfg.output_vat, "2200", (oc,)),
        _Account(cfg.bezugsteuer, "2200", (oc,)),
        _Account(cfg.payable_vat, "2200", (oc,)),
        *_equity(answers),
        _Account(cfg.income_domestic, "3400"),
        _Account(cfg.income_export, "3400"),
        _Account(_sub(_IT_HOSTING, c), "6570"),
        _Account(cfg.fx_loss, "6900", (oc,)),
        _Account(cfg.rounding_income, "6950", (oc,)),
        _Account(cfg.fx_gain, "6950", (oc,)),
    ]
    if "wise" in answers.importers:
        for ccy, acct in config.WiseImport().accounts:
            accounts.append(_Account(_sub(acct, c), "1020", (ccy,)))
        accounts.append(_Account(_sub(config.WiseImport().fees_account, c), "6940"))
    if "stripe" in answers.importers:
        for ccy, acct in config.StripeImport().accounts:
            accounts.append(_Account(_sub(acct, c), "1020", (ccy,)))
        accounts.append(_Account(_sub(config.StripeImport().fees_account, c), "6940"))
    # The demo quarter settles a EUR reverse-charge purchase, so it needs a
    # EUR bank even when the Wise importer is off.
    if answers.include_samples:
        accounts.append(_Account(_sub(_WISE_EUR, c), "1020", ("EUR",)))

    seen: set[str] = set()
    unique: list[_Account] = []
    for a in accounts:
        if a.name not in seen:
            seen.add(a.name)
            unique.append(a)
    return unique


# ── .bean rendering ──────────────────────────────────────────────────────────


def _open_directive(open_date: Date, account: _Account, form: str) -> str:
    constraint = f" {','.join(account.currencies)}" if account.currencies else ""
    name = kmu.kmu_name(account.code, "en", form)
    return f'{open_date} open {account.name}{constraint}\n  kmu: "{account.code}"  ; {name}\n'


def _main_bean(answers: Answers) -> str:
    cfg = _cfg(answers)
    year = _open_date(answers).year
    return "\n".join(
        [
            f"; {answers.entity_name} — books managed with quints (https://github.com/sealambda/quints)",
            ";",
            "; Generated by `quints init`. Layout:",
            ";   accounts.bean      chart of accounts (KMU Kontenrahmen)",
            ";   commodities.bean   currencies and their price sources",
            ";   prices.bean        FX rates — refresh with `quints prices sync`",
            f";   books/{year}.bean    transactions, one file per fiscal year",
            "; Validate with `quints check`; after `uv sync`, plain",
            "; `bean-check main.bean` and `fava main.bean` work too.",
            "",
            f'plugin "quints.plugins.kmu" "{cfg.entity_marker}"',
            'plugin "fava.plugins.link_documents"',
            "",
            f'{_open_date(answers)} custom "fava-extension" "quints.fava"',
            "",
            'include "accounts.bean"',
            'include "commodities.bean"',
            'include "prices.bean"',
            'include "books/*.bean"',
            "",
            f'option "title" "{answers.entity_name}"',
            f'option "operating_currency" "{answers.operating_currency}"',
            'option "documents" "documents/"',
            "",
        ]
    )


def _accounts_bean(answers: Answers) -> str:
    cfg = _cfg(answers)
    open_date = _open_date(answers)
    lines: list[str] = [
        f"; Chart of accounts — Swiss KMU Kontenrahmen. Every *{cfg.entity_marker}* account",
        "; must carry the four-digit kmu: code it rolls up to (enforced by",
        "; quints.plugins.kmu). Extend the chart by adding more open directives;",
        "; see AGENTS.md and `quints report konten`.",
        "",
    ]
    for account in _backbone(answers):
        lines.append(_open_directive(open_date, account, answers.legal_form).rstrip("\n"))
    return "\n".join(lines).rstrip("\n") + "\n"


def _commodities_bean(answers: Answers) -> str:
    open_date = _open_date(answers)
    currencies: set[str] = {answers.operating_currency}
    for account in _backbone(answers):
        currencies.update(account.currencies)
    ordered = sorted(currencies, key=lambda ccy: (ccy != answers.operating_currency, ccy))
    lines: list[str] = [
        "; Currencies in use. Non-CHF commodities carry the beanprice source",
        "; (official BAZG/EZV daily CHF rates) that `quints prices sync` and",
        "; `bean-price` read.",
        "",
    ]
    for ccy in ordered:
        lines.append(f"{open_date} commodity {ccy}")
        if ccy != "CHF":
            lines.append(f'  price: "CHF:beanprice_bazg/{ccy}"')
    return "\n".join(lines) + "\n"


def _books_bean(answers: Answers) -> str:
    year = _open_date(answers).year
    header = (
        f"; {year} — transactions for the fiscal year. Reviewed importer drafts\n"
        f"; move here from staging/. main.bean includes books/*.bean, so a new\n"
        f"; year just needs a new books/<year>.bean file.\n"
    )
    if not answers.include_samples:
        return header
    return header + "\n" + _sample_quarter(answers) + "\n"


def _txn(header: str, postings: list[tuple[str, str, str]], width: int) -> list[str]:
    lines = [header]
    for account, number, rest in postings:
        lines.append(f"  {account:<{width}}{number:>10} {rest}")
    return lines


def _sample_quarter(answers: Answers) -> str:
    """A small, balancing quarter of activity so every command has data.

    Mirrors the conventions in test_mwst: a domestic sale with 8.1% output
    VAT, a zero-rated EUR export, and a EUR reverse-charge purchase
    (Bezugsteuer). Amounts are literals — deterministic, no rate lookups.
    """
    cfg = _cfg(answers)
    c = _component(answers)
    year = _open_date(answers).year
    bank = _sub(_PRIMARY_BANK, c)
    if answers.legal_form == "einzelfirma":
        opening_header = f'{year}-01-02 * "Owner" "Capital contribution"'
        opening_equity = f"Equity:CH:{c}:Contributions"
    else:
        opening_header = f'{year}-01-02 * "Founders" "Share capital paid in"'
        opening_equity = f"Equity:CH:{c}:Capital:Share"
    txns: list[tuple[str, list[tuple[str, str, str]]]] = [
        (
            opening_header,
            [
                (bank, "20000.00", "CHF"),
                (opening_equity, "-20000.00", "CHF"),
            ],
        ),
        (
            f'{year}-07-02 * "Acme AG" "Consulting — July" ^INV{year}014',
            [
                (cfg.receivable, "1081.00", "CHF"),
                (cfg.income_domestic, "-1000.00", "CHF"),
                (cfg.output_vat, "-81.00", "CHF"),
            ],
        ),
        (
            f'{year}-07-20 * "Acme AG" "Payment INV{year}014" ^INV{year}014',
            [
                (bank, "1081.00", "CHF"),
                (cfg.receivable, "-1081.00", "CHF"),
            ],
        ),
        (
            f'{year}-08-05 * "Globex Ltd" "Export consulting" ^INV{year}015',
            [
                (cfg.receivable, "500.00", "EUR"),
                (cfg.income_export, "-500.00", "EUR"),
            ],
        ),
        (
            f'{year}-08-12 * "Foreign SaaS" "Cloud hosting (reverse charge)"',
            [
                (_sub(_IT_HOSTING, c), "100.00", "EUR"),
                (cfg.input_vat, "7.53", "CHF @@ 8.10 EUR"),
                (cfg.bezugsteuer, "-7.53", "CHF @@ 8.10 EUR"),
                (_sub(_WISE_EUR, c), "-100.00", "EUR"),
            ],
        ),
    ]
    width = max(len(account) for _, postings in txns for account, _, _ in postings) + 3
    lines: list[str] = ["; ── sample activity (remove once you book your own) ───────────────────"]
    for header, postings in txns:
        lines.append("")
        lines.extend(_txn(header, postings, width))
    return "\n".join(lines)


def _prices_bean(answers: Answers) -> str:
    header = (
        "; FX rates (CHF per unit). Populate with `quints prices sync`\n"
        "; (official BAZG/EZV daily rates).\n"
    )
    if not answers.include_samples:
        return header
    year = _open_date(answers).year
    return header + "\n".join(
        [
            "",
            f"{year}-07-01 price EUR 0.93 CHF",
            f"{year}-08-01 price EUR 0.94 CHF",
            "",
        ]
    )


# ── invoicing samples ────────────────────────────────────────────────────────

# Checksum-valid demo identifiers (stdnum-verified) — obviously not real.
_SAMPLE_VAT_ID = "CHE-267.359.056 MWST"
_SAMPLE_QR_IBAN = "CH44 3199 9123 0008 8901 2"  # QR-IID range 30000–31999
_SAMPLE_IBAN = "CH93 0076 2011 6238 5295 7"
_SAMPLE_CUSTOMER_VAT_ID = "IE1234567T"


def _modeline(schema: str) -> str:
    """A yaml-language-server modeline pointing at the hosted JSON Schema, so
    editors and agents get field-level validation without running `quints
    schema` first. The schemas are published by the docs build."""
    return f"# yaml-language-server: $schema={config.DOCS_URL}/schema/{schema}.schema.json"


def _issuer_yaml(answers: Answers) -> str:
    return "\n".join(
        [
            _modeline("issuer"),
            "# Issuer identity for `quints invoice` — name, address, VAT ID, and one",
            "# bank account per invoicing currency. Sample data: replace the VAT ID",
            "# and IBANs with your own before issuing a real invoice.",
            f"name: {answers.entity_name}",
            "address:",
            "  - Beispielstrasse 1",
            "  - 8000 Zürich",
            f"vat_id: {_SAMPLE_VAT_ID}",
            "email: billing@example.ch",
            "bank:",
            "  CHF:",
            "    # QR-IBAN (QR-IID variant) — a Swiss QR-bill with a QRR reference.",
            f"    qr_iban: {_SAMPLE_QR_IBAN}",
            "  EUR:",
            "    # Regular IBAN — foreign transfers can't use the QR-bill scheme.",
            f"    iban: {_SAMPLE_IBAN}",
            "",
        ]
    )


def _customers_yaml(_answers: Answers) -> str:
    return "\n".join(
        [
            _modeline("customers"),
            "# Customer registry — invoices reference these entries by key. A customer",
            "# is a flat entry, or a dated `versions` history for address changes.",
            "acme:",
            "  name: Acme AG",
            "  address:",
            "    - Bahnhofstrasse 1",
            "    - 8001 Zürich",
            "globex:",
            "  name: Globex Ltd",
            "  country: IE",
            "  # Reverse-charge exports must carry the customer's VAT number.",
            f"  vat_id: {_SAMPLE_CUSTOMER_VAT_ID}",
            "  address:",
            "    - 1 Liffey Street",
            "    - Dublin 1",
            "",
        ]
    )


def _invoice_acme_yaml(answers: Answers) -> str:
    year = _open_date(answers).year
    return "\n".join(
        [
            _modeline("invoice"),
            f"# Sample domestic invoice — a Swiss QR-bill. It ties to the ^INV{year}014",
            f"# booking in books/{year}.bean (net 1'000.00 + 8.1% VAT = 1'081.00), so",
            "# `quints invoice` cross-checks it clean against the ledger.",
            f"number: INV{year}014",
            "kind: domestic",
            "currency: CHF",
            f"issue_date: {year}-07-02",
            f"supply: Juli {year}",
            "customer: acme",
            "items:",
            "  - description: Consulting — July",
            "    quantity: 1",
            "    unit_price: 1000.00",
            "    unit: Pauschal",
            "locale: de_CH",
            "",
        ]
    )


def _invoice_globex_yaml(answers: Answers) -> str:
    year = _open_date(answers).year
    return "\n".join(
        [
            _modeline("invoice"),
            "# Sample export invoice — foreign currency, reverse charge, no QR part.",
            f"# Ties to the ^INV{year}015 booking in books/{year}.bean (500.00 EUR).",
            f"number: INV{year}015",
            "kind: export",
            "currency: EUR",
            f"issue_date: {year}-08-05",
            f"supply: August {year}",
            "customer: globex",
            "items:",
            "  - description: Export consulting",
            "    quantity: 1",
            "    unit_price: 500.00",
            "locale: en",
            "",
        ]
    )


# ── project metadata rendering ───────────────────────────────────────────────


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "bookkeeping"


def _pyproject_toml(answers: Answers) -> str:
    return "\n".join(
        [
            "# `uv sync` installs quints — which brings beancount, fava, the statement",
            "# importers, and the BAZG price source along as its own dependencies — so",
            "# the standard beancount toolchain (`bean-check main.bean`, `fava",
            "# main.bean`, `bean-query`) works in this repo, not only the quints CLI.",
            "[project]",
            f'name = "{_slug(answers.entity_name)}"',
            'version = "0.1.0"',
            'requires-python = ">=3.10"',
            "dependencies = [",
            '    "quints",',
            "]",
            "",
            "[tool.uv]",
            "package = false",
            "",
        ]
    )


def _quints_toml(answers: Answers) -> str:
    cfg = _cfg(answers)
    since = answers.vat_registered_since
    lines: list[str] = [
        "# quints.toml — everything entity-specific lives here, next to main.bean.",
        "# Account names mirror accounts.bean; change them in both files together.",
        "",
        "[entity]",
        f'name = "{answers.entity_name}"',
        f'legal_form = "{answers.legal_form}"           # gmbh | ag | einzelfirma',
        f'vat_method = "{answers.vat_method}"            # "saldo" is not supported yet',
    ]
    if since is not None:
        lines.append(
            f"vat_registered_since = {since.isoformat()}   # earlier periods are pre-liability"
        )
    lines += [
        f'operating_currency = "{answers.operating_currency}"',
        "",
        "[ledger]",
        'main = "main.bean"',
        'prices = "prices.bean"',
        "",
        "# `quints prices sync` reads the commodity price metadata in",
        "# commodities.bean (same as bean-price). A [prices] section here only",
        "# matters for ledgers that declare none — see the FX guide.",
        "",
        "[accounts]",
        f'entity_marker = "{cfg.entity_marker}"         # scopes KMU statements and the plugin',
        f'input_vat = "{cfg.input_vat}"',
        f'output_vat = "{cfg.output_vat}"',
        f'bezugsteuer = "{cfg.bezugsteuer}"',
        f'payable_vat = "{cfg.payable_vat}"',
        f'receivable = "{cfg.receivable}"',
        f'income_prefix = "{cfg.income_prefix}"',
        f'export_marker = "{cfg.export_marker}"           # income sub-account marker → Ziffer 221',
        f'income_domestic = "{cfg.income_domestic}"',
        f'income_export = "{cfg.income_export}"',
        f'fx_gain = "{cfg.fx_gain}"',
        f'fx_loss = "{cfg.fx_loss}"',
        f'rounding_income = "{cfg.rounding_income}"',
        "",
        "[report]",
        f'language = "{answers.report_language}"                     # or "de"; --lang overrides',
    ]
    for importer in answers.importers:
        lines.append("")
        lines.append(_import_section(importer, answers))
    return "\n".join(lines).rstrip("\n") + "\n"


def _import_section(importer: str, answers: Answers) -> str:
    c = _component(answers)
    if importer == "ubs":
        d = config.UbsImport()
        return "\n".join(
            [
                "# MT940 statements → staging/. '*' = booked as drafted, '!' = still",
                "# needs a VAT decision + linked document before it reaches your books.",
                "[import.ubs]",
                f'account = "{_sub(d.account, c)}"',
                'iban = "CH9300762011623852957"   # your IBAN, as in the MT940 :25: field',
                "rules = [",
                f'    [\'\\bacme\\b\', "{_sub("Assets:CH:GmbH:Receivable:Trade", c)}", "*"],',
                "]",
            ]
        )
    if importer == "wise":
        d = config.WiseImport()
        rows = "\n".join(f'{ccy} = "{_sub(acct, c)}"' for ccy, acct in d.accounts)
        return "\n".join(
            [
                "# Fetching (`quints import wise --fetch`) needs QUINTS_WISE_API_TOKEN",
                "# (and QUINTS_WISE_PRIVATE_KEY for SCA-protected profiles) in .env.",
                "[import.wise]",
                f'fees_account = "{_sub(d.fees_account, c)}"',
                "rules = [",
                f'    ["cloudflare", "{_sub(_IT_HOSTING, c)}", "!"],',
                "]",
                "",
                "[import.wise.accounts]",
                rows,
            ]
        )
    if importer == "stripe":
        d = config.StripeImport()
        rows = "\n".join(f'{ccy} = "{_sub(acct, c)}"' for ccy, acct in d.accounts)
        return "\n".join(
            [
                "# Fetching needs QUINTS_STRIPE_API_KEY in .env (a restricted key with",
                "# Balance transaction sources: Read and Charges: Read).",
                "[import.stripe]",
                'account_id = "acct_XXXXXXXXXXXX"   # guard: refuse a key for another account',
                f'fees_account = "{_sub(d.fees_account, c)}"',
                f'tax_account = "{_sub(d.tax_account, c)}"   # VAT inside Stripe\'s own fees',
                "rules = [",
                f'    [\'\\bpayout\\b\', "{_sub(_WISE_EUR, c)}", "*"],',
                "]",
                "",
                "[import.stripe.accounts]",
                rows,
            ]
        )
    raise InitError(f"unknown importer {importer!r} (known: {', '.join(_KNOWN_IMPORTERS)})")


# ── AGENTS.md — the AI-first payload ─────────────────────────────────────────

# Per-importer usage lines for AGENTS.md — command first, credentials after,
# so an agent can run the roster without probing quints.toml or .env.
_IMPORTER_USAGE = {
    "ubs": (
        "`quints import ubs <statement.mt940>` — the MT940 export from UBS "
        "e-banking; no credentials."
    ),
    "wise": (
        "`quints import wise --fetch --from <date> --to <date>` — needs "
        "`QUINTS_WISE_API_TOKEN` in `.env` (plus `QUINTS_WISE_PRIVATE_KEY` for "
        "SCA-protected profiles; the key pair lives in `.wise/`, git-ignored)."
    ),
    "stripe": (
        "`quints import stripe --fetch --from <date> --to <date>` — needs "
        "`QUINTS_STRIPE_API_KEY` in `.env` (a restricted read-only key for the "
        "`[import.stripe]` account)."
    ),
}


def _agents_import_step(answers: Answers) -> str:
    if not answers.importers:
        return (
            "1. Draft bank/PSP activity into `staging/` with `quints import`. No\n"
            "   importer is configured yet — add an `[import.<name>]` section to\n"
            "   `quints.toml` (supported: ubs, wise, stripe)."
        )
    lines = ["1. Draft bank/PSP activity into `staging/`. Configured importers:"]
    lines += [f"   - {_IMPORTER_USAGE[name]}" for name in answers.importers]
    return "\n".join(lines)


def _agents_sample_section(answers: Answers) -> str:
    if not answers.include_samples:
        return ""
    year = _open_date(answers).year
    lines = [
        "",
        "## Sample data — replace before the books are real",
        "",
        "The scaffold seeded a demo quarter so every command has data. Before",
        "booking real activity:",
        "",
        f"- [ ] `invoicing/issuer.yaml` — the VAT ID ({_SAMPLE_VAT_ID}) and both",
        "      IBANs are checksum-valid fakes; put the real ones in.",
        "- [ ] `invoicing/customers.yaml` — replace the demo customers (acme, globex).",
        f"- [ ] `invoicing/acme-{year}-07.yaml` and `invoicing/globex-{year}-08.yaml`",
        "      — delete the demo invoices.",
        f"- [ ] `books/{year}.bean` — delete the block marked *sample activity*.",
        "- [ ] `prices.bean` — drop the demo EUR rates, then `quints prices sync`.",
    ]
    if "ubs" in answers.importers:
        lines.append("- [ ] `quints.toml` — the placeholder IBAN under `[import.ubs]`.")
    return "\n".join(lines) + "\n"


def _agents_md(answers: Answers) -> str:
    cfg = _cfg(answers)
    marker = cfg.entity_marker
    year = _open_date(answers).year
    bank = _sub(_PRIMARY_BANK, _component(answers))
    return f"""# Working on {answers.entity_name}'s books with an AI agent

These are plain-text ([beancount](https://beancount.github.io)) books managed
with [`quints`](https://github.com/sealambda/quints). **`quints` is a
deterministic tool — you drive it, it never calls a model.** Your job is to
extend and maintain the ledger; `quints` validates and reports on it.

## Setup

`uv sync` once — it installs quints, which brings beancount and fava along.
Then `uv run quints check` (or activate the venv and call `quints` and the
standard beancount tools directly).

This project is a git repository; `quints init` committed the pristine
scaffold. Work in reviewable steps: `git diff` before moving drafts into
`books/`, commit once `quints check` passes — the history is the audit trail.

## Layout

- `main.bean` — options, plugins, includes; the entry point every tool loads.
  `plugin "quints.plugins.kmu"` is enabled: every `*{marker}*` account
  **must** be opened with a four-digit `kmu:` code (Swiss KMU Kontenrahmen).
- `accounts.bean` — the chart of accounts (all `open` directives).
- `books/{year}.bean` — transactions, one file per fiscal year. `main.bean`
  includes `books/*.bean`, so a new year just needs a new file.
- `commodities.bean` — currencies; `prices.bean` — FX rates, refresh with
  `quints prices sync`.
- `quints.toml` — entity config (name, legal form, VAT, importer rules). VAT
  *rates* are law and live in code, not here.
- `staging/` — importer drafts land here (git-ignored, transient).
- `inbox/` — incoming source documents, not yet filed.
- `documents/` — filed documents, mirroring the account tree as
  `documents/<Account/Tree>/YYYY-MM-DD.payee.description.pdf`. Committed:
  the ledger links to these files (`fava.plugins.link_documents`).
- `invoicing/` — issuer identity (`issuer.yaml`), customer registry
  (`customers.yaml`), one YAML per issued invoice.

## Extending the chart of accounts (the part that needs judgement)

Add income/expense sub-trees for this business as `open` directives in
`accounts.bean`, each with the KMU code it rolls up to, e.g.:

```beancount
{_open_date(answers)} open Expenses:CH:{_component(answers)}:Marketing:Ads CHF
  kmu: "6600"  ; Advertising
```

See the codes already in use:

```bash
quints report konten --year {year}
```

Pick codes from the KMU Kontenrahmen; `quints check` fails on a `{marker}`
account with no valid `kmu:` code.

## The loop — money out (statements → books)

{_agents_import_step(answers)}
2. Review each draft in `staging/`. A draft is a flagged (`!`) transaction
   with only the cash leg known:

```beancount
{year}-07-20 ! "ACME AG" "Payment order"
  {bank}  -250.00 CHF
```

   Complete the counter leg, decide the VAT treatment (InputVAT /
   Bezugsteuer / none), link the source document, flip `!` to `*`, and move
   it into `books/{year}.bean`. `quints match` scores staging drafts and
   inbox documents against invoices and bookings.
3. **Always** `quints check` before you consider the books consistent.

## The loop — money in (invoice → receivable → payment)

1. Describe the invoice as a YAML file in `invoicing/` (each file carries a
   `$schema` modeline, so schema-aware editors validate it as you type).
2. `quints invoice invoicing/<file>.yaml` renders the PDF into `documents/`
   under the income account and cross-checks the total against the ledger.
   Not booked yet? It prints the receivable draft to paste into
   `books/{year}.bean`.
3. The payment arrives with the next bank import; the draft is matched to
   the open invoice by its QR/SCOR reference. `quints receivables` shows
   what is still open.

## Machine-readable surfaces (prefer these over scraping text)

Every reporting command takes `--json` — stable keys, ISO dates, decimal
strings:

```bash
quints check --json
quints mwst -q {year}-Q3 --json
quints status --json
quints report bilanz --at {year}-12-31 --json
quints receivables --json
```

JSON Schemas for the invoicing files are hosted at
{config.DOCS_URL}/schema/ (`quints schema` writes them
locally to `invoicing/schema/`).

Never invent VAT numbers or rates — compute them with `quints mwst`.
{_agents_sample_section(answers)}"""


def _claude_md() -> str:
    # Claude Code auto-loads CLAUDE.md; the @-include pulls AGENTS.md into
    # context so the instructions work without the agent going looking.
    return "@AGENTS.md\n"


def _gitignore() -> str:
    return "\n".join(
        [
            "# quints working directories. documents/ (filed sources and rendered",
            "# invoices) is deliberately NOT ignored — the ledger links to it.",
            "/staging/",
            ".env",
            "__pycache__/",
            ".venv/",
            ".DS_Store",
            ".wise/",  # Wise SCA signing keys
            "",
        ]
    )


# ── plan / write ─────────────────────────────────────────────────────────────


def plan(answers: Answers) -> list[ScaffoldFile]:
    """Answers → the ordered set of files to materialise. Pure, no I/O."""
    _validate(answers)
    year = _open_date(answers).year
    invoicing: list[ScaffoldFile] = []
    if answers.include_samples:
        # One QR-bill and one export invoice, reconciling against the sample
        # quarter — so `quints invoice` is testable out of the box.
        invoicing = [
            ScaffoldFile(Path("invoicing/issuer.yaml"), _issuer_yaml(answers)),
            ScaffoldFile(Path("invoicing/customers.yaml"), _customers_yaml(answers)),
            ScaffoldFile(Path(f"invoicing/acme-{year}-07.yaml"), _invoice_acme_yaml(answers)),
            ScaffoldFile(Path(f"invoicing/globex-{year}-08.yaml"), _invoice_globex_yaml(answers)),
        ]
    files = [
        ScaffoldFile(Path("quints.toml"), _quints_toml(answers)),
        ScaffoldFile(Path("pyproject.toml"), _pyproject_toml(answers)),
        ScaffoldFile(Path("main.bean"), _main_bean(answers)),
        ScaffoldFile(Path("accounts.bean"), _accounts_bean(answers)),
        ScaffoldFile(Path("commodities.bean"), _commodities_bean(answers)),
        ScaffoldFile(Path("prices.bean"), _prices_bean(answers)),
        ScaffoldFile(Path(f"books/{year}.bean"), _books_bean(answers)),
        *invoicing,
        ScaffoldFile(Path("AGENTS.md"), _agents_md(answers)),
        ScaffoldFile(Path("CLAUDE.md"), _claude_md()),
        ScaffoldFile(Path(".gitignore"), _gitignore()),
        ScaffoldFile(Path("inbox/.gitkeep"), ""),
        ScaffoldFile(Path("staging/.gitkeep"), ""),
        ScaffoldFile(Path("documents/.gitkeep"), ""),
    ]
    return files


def _validate(answers: Answers) -> None:
    if answers.legal_form not in config.LEGAL_FORMS:
        raise InitError(
            f"unknown legal_form {answers.legal_form!r} — supported: "
            f"{', '.join(config.LEGAL_FORMS)} (the KMU Kontenrahmen's Klasse-28 "
            f"variants; Personengesellschaft is not supported yet)"
        )
    if answers.vat_method != "effective":
        raise InitError(
            f"vat_method {answers.vat_method!r} is not supported yet (only 'effective')"
        )
    unknown = [i for i in answers.importers if i not in _KNOWN_IMPORTERS]
    if unknown:
        raise InitError(
            f"unknown importer(s) {', '.join(unknown)} (known: {', '.join(_KNOWN_IMPORTERS)})"
        )


@dataclass
class WriteResult:
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)


def write(target: Path, files: list[ScaffoldFile], *, force: bool = False) -> WriteResult:
    """Materialise ``files`` under ``target``. Never overwrites unless ``force``."""
    result = WriteResult()
    for f in files:
        dest = target / f.path
        if dest.exists() and not force:
            result.skipped.append(dest)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f.content)
        result.written.append(dest)
    return result


@dataclass(frozen=True)
class GitResult:
    initialized: bool  # a fresh repository was created
    committed: bool  # the pristine scaffold is the initial commit
    detail: str = ""  # why a step was skipped or failed, for the caller to show


def init_git(target: Path) -> GitResult:
    """``git init`` + an initial commit of the pristine scaffold.

    The repository is what makes agent edits reviewable — diff before drafts
    move into ``books/``, revert when a change was wrong — and the initial
    commit makes "what did the agent change" answerable from day one. A
    ``target`` already inside a work tree (e.g. scaffolding a subdirectory of
    an existing repo) is left alone.
    """
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        return GitResult(False, False, "git not installed")
    inside = subprocess.run(  # noqa: S603 — fixed argv, git resolved via shutil.which
        [git, "-C", str(target), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if inside.returncode == 0 and inside.stdout.strip() == "true":
        return GitResult(False, False, "already inside a git repository")
    for argv in ([git, "init", "--quiet"], [git, "add", "--all"]):
        step = subprocess.run(argv, cwd=target, capture_output=True, text=True)  # noqa: S603 — fixed argv
        if step.returncode != 0:
            return GitResult(True, False, step.stderr.strip() or f"git {argv[1]} failed")
    commit = subprocess.run(  # noqa: S603 — fixed argv
        [git, "commit", "--quiet", "-m", "Scaffold books with quints init"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        # Most likely no git identity — leave the repo with everything staged.
        return GitResult(True, False, commit.stderr.strip() or "git commit failed")
    return GitResult(True, True)
