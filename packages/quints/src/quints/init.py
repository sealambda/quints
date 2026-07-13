"""Scaffold a new quints project: questionnaire answers → a runnable ledger.

This is deterministic on purpose. ``quints`` is a tool; the AI is always a
*caller* of it, never a callee. So ``init`` emits a **backbone** — the parts
of a Swiss GmbH's books that are a published standard, not a judgement call:
the directory layout, ``quints.toml``, the statutory accounts (InputVAT /
OutputVAT / Bezugsteuer / VAT-due, FX gain/loss) and a KMU Kontenrahmen
skeleton. Every ``*:CH:GmbH:*`` account is opened with a four-digit ``kmu:``
code (seeded from :data:`quints.kmu.KMU_NAMES`) so ``quints.plugins.kmu`` and
the statutory statements work out of the box.

The **leaves** — business-specific income/expense sub-trees, importer rules —
are left for an agent (Claude Code, Codex, the Agent SDK) to tailor by
conversation, guided by the ``AGENTS.md`` this emits into the project.

The compute layer (:func:`plan`) is presentation-free: answers in, an ordered
list of :class:`ScaffoldFile` out, no I/O. :func:`write` does the I/O. Running
:func:`plan` with ``include_samples=True`` produces the repo's ``examples/``
project, which doubles as the CI smoke test — so the example is regenerated,
never hand-maintained.
"""

from __future__ import annotations

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

# Accounts the backbone adds beyond the configurable set in `config.Config`.
_SHARE_CAPITAL = "Equity:CH:GmbH:Capital:Share"
_IT_HOSTING = "Expenses:CH:GmbH:IT:Hosting"
_PRIMARY_BANK = "Assets:CH:GmbH:Current:UBS:CHF"
_WISE_EUR = "Assets:CH:GmbH:Current:Wise:EUR"

_KNOWN_IMPORTERS = ("ubs", "wise", "stripe")


@dataclass(frozen=True)
class Answers:
    """The questionnaire result — everything ``plan`` needs, nothing more."""

    entity_name: str = "Example GmbH"
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


def _cfg(answers: Answers) -> config.Config:
    """The `config.Config` the emitted `quints.toml` resolves to."""
    return config.Config(
        entity_name=answers.entity_name,
        vat_method=answers.vat_method,
        vat_registered_since=answers.vat_registered_since,
        operating_currency=answers.operating_currency,
        report_language=answers.report_language,
    )


def _backbone(answers: Answers) -> list[_Account]:
    """The KMU account skeleton, deduped, in a stable order.

    Every account carries a ``kmu:`` code that exists in
    :data:`quints.kmu.KMU_NAMES`, so the statutory statements name it and the
    plugin accepts it.
    """
    cfg = _cfg(answers)
    oc = answers.operating_currency
    accounts: list[_Account] = [
        _Account(_PRIMARY_BANK, "1020", ("CHF",)),
        _Account(cfg.receivable, "1100"),
        _Account(cfg.input_vat, "1170", (oc,)),
        _Account(cfg.output_vat, "2200", (oc,)),
        _Account(cfg.bezugsteuer, "2200", (oc,)),
        _Account(cfg.payable_vat, "2200", (oc,)),
        _Account(_SHARE_CAPITAL, "2800", (oc,)),
        _Account(cfg.income_domestic, "3400"),
        _Account(cfg.income_export, "3400"),
        _Account(_IT_HOSTING, "6570"),
        _Account(cfg.fx_loss, "6900", (oc,)),
        _Account(cfg.rounding_income, "6950", (oc,)),
        _Account(cfg.fx_gain, "6950", (oc,)),
    ]
    if "wise" in answers.importers:
        for ccy, acct in config.WiseImport().accounts:
            accounts.append(_Account(acct, "1020", (ccy,)))
        accounts.append(_Account(config.WiseImport().fees_account, "6940"))
    if "stripe" in answers.importers:
        for ccy, acct in config.StripeImport().accounts:
            accounts.append(_Account(acct, "1020", (ccy,)))
        accounts.append(_Account(config.StripeImport().fees_account, "6940"))
    # The demo quarter settles a EUR reverse-charge purchase, so it needs a
    # EUR bank even when the Wise importer is off.
    if answers.include_samples:
        accounts.append(_Account(_WISE_EUR, "1020", ("EUR",)))

    seen: set[str] = set()
    unique: list[_Account] = []
    for a in accounts:
        if a.name not in seen:
            seen.add(a.name)
            unique.append(a)
    return unique


# ── .bean rendering ──────────────────────────────────────────────────────────


def _open_directive(open_date: Date, account: _Account) -> str:
    constraint = f" {','.join(account.currencies)}" if account.currencies else ""
    name = kmu.kmu_name(account.code, "en")
    return f'{open_date} open {account.name}{constraint}\n  kmu: "{account.code}"  ; {name}\n'


def _main_bean(answers: Answers) -> str:
    cfg = _cfg(answers)
    open_date = answers.vat_registered_since or Date(2026, 1, 1)
    lines: list[str] = [
        f"; {answers.entity_name} — books managed with quints (https://github.com/sealambda/quints)",
        ";",
        "; Backbone generated by `quints init`. Extend the chart by adding more",
        "; `open` directives — every :CH:GmbH: account needs a four-digit kmu: code",
        "; (see `quints report konten` and AGENTS.md). Validate with `quints check`.",
        "",
        f'option "title" "{answers.entity_name}"',
        f'option "operating_currency" "{answers.operating_currency}"',
        "",
        'plugin "quints.plugins.kmu"',
        "",
        f'include "{cfg.ledger_prices}"',
        "",
        "; ── chart of accounts (KMU Kontenrahmen) ──────────────────────────────",
        "",
    ]
    for account in _backbone(answers):
        lines.append(_open_directive(open_date, account).rstrip("\n"))
    if answers.include_samples:
        lines.append("")
        lines.append(_sample_quarter(open_date))
    return "\n".join(lines).rstrip("\n") + "\n"


def _sample_quarter(open_date: Date) -> str:
    """A small, balancing quarter of activity so every command has data.

    Mirrors the conventions in test_mwst: a domestic sale with 8.1% output
    VAT, a zero-rated EUR export, and a EUR reverse-charge purchase
    (Bezugsteuer). Amounts are literals — deterministic, no rate lookups.
    """
    year = open_date.year
    return "\n".join(
        [
            "; ── sample activity (remove once you book your own) ───────────────────",
            "",
            f'{year}-01-02 * "Founders" "Share capital paid in"',
            "  Assets:CH:GmbH:Current:UBS:CHF               20000.00 CHF",
            "  Equity:CH:GmbH:Capital:Share                -20000.00 CHF",
            "",
            f'{year}-07-02 * "Acme AG" "Consulting — July" ^INV{year}014',
            "  Assets:CH:GmbH:Receivable:Trade               1081.00 CHF",
            "  Income:CH:GmbH:Consulting:External:Domestic  -1000.00 CHF",
            "  Liabilities:CH:GmbH:Tax:OutputVAT              -81.00 CHF",
            "",
            f'{year}-07-20 * "Acme AG" "Payment INV{year}014" ^INV{year}014',
            "  Assets:CH:GmbH:Current:UBS:CHF                1081.00 CHF",
            "  Assets:CH:GmbH:Receivable:Trade              -1081.00 CHF",
            "",
            f'{year}-08-05 * "Globex Ltd" "Export consulting" ^INV{year}015',
            "  Assets:CH:GmbH:Receivable:Trade                500.00 EUR",
            "  Income:CH:GmbH:Consulting:External:Export     -500.00 EUR",
            "",
            f'{year}-08-12 * "Foreign SaaS" "Cloud hosting (reverse charge)"',
            "  Expenses:CH:GmbH:IT:Hosting                    100.00 EUR",
            "  Assets:CH:GmbH:Tax:InputVAT                      7.53 CHF @@ 8.10 EUR",
            "  Liabilities:CH:GmbH:Tax:Bezugsteuer            -7.53 CHF @@ 8.10 EUR",
            "  Assets:CH:GmbH:Current:Wise:EUR               -100.00 EUR",
        ]
    )


def _prices_bean(answers: Answers) -> str:
    header = (
        "; FX rates (CHF per unit). Populate with `quints prices sync`\n"
        "; (official BAZG/EZV daily rates).\n"
    )
    if not answers.include_samples:
        return header
    year = (answers.vat_registered_since or Date(2026, 1, 1)).year
    return header + "\n".join(
        [
            "",
            f"{year}-07-01 price EUR 0.93 CHF",
            f"{year}-08-01 price EUR 0.94 CHF",
            "",
        ]
    )


# ── quints.toml rendering ────────────────────────────────────────────────────


def _quints_toml(answers: Answers) -> str:
    since = answers.vat_registered_since
    lines: list[str] = [
        "# quints.toml — everything entity-specific lives here, next to main.bean.",
        "# Account names below are the built-in defaults (CH:GmbH namespacing);",
        "# uncomment and edit in quints.toml + main.bean together to change them.",
        "",
        "[entity]",
        f'name = "{answers.entity_name}"',
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
        "[report]",
        f'language = "{answers.report_language}"                     # or "de"; --lang overrides',
    ]
    for importer in answers.importers:
        lines.append("")
        lines.append(_import_section(importer))
    return "\n".join(lines).rstrip("\n") + "\n"


def _import_section(importer: str) -> str:
    if importer == "ubs":
        d = config.UbsImport()
        return "\n".join(
            [
                "# MT940 statements → staging/. '*' = booked as drafted, '!' = still",
                "# needs a VAT decision + linked document before it reaches your books.",
                "[import.ubs]",
                f'account = "{d.account}"',
                'iban = "CH9300762011623852957"   # your IBAN, as in the MT940 :25: field',
                "rules = [",
                '    [\'\\bacme\\b\', "Assets:CH:GmbH:Receivable:Trade", "*"],',
                "]",
            ]
        )
    if importer == "wise":
        d = config.WiseImport()
        rows = "\n".join(f'{ccy} = "{acct}"' for ccy, acct in d.accounts)
        return "\n".join(
            [
                "# Fetching (`quints import wise --fetch`) needs QUINTS_WISE_API_TOKEN",
                "# (and QUINTS_WISE_PRIVATE_KEY for SCA-protected profiles) in .env.",
                "[import.wise]",
                f'fees_account = "{d.fees_account}"',
                "rules = [",
                '    ["cloudflare", "Expenses:CH:GmbH:IT:Hosting", "!"],',
                "]",
                "",
                "[import.wise.accounts]",
                rows,
            ]
        )
    if importer == "stripe":
        d = config.StripeImport()
        rows = "\n".join(f'{ccy} = "{acct}"' for ccy, acct in d.accounts)
        return "\n".join(
            [
                "# Fetching needs QUINTS_STRIPE_API_KEY in .env (a restricted key with",
                "# Balance transaction sources: Read and Charges: Read).",
                "[import.stripe]",
                'account_id = "acct_XXXXXXXXXXXX"   # guard: refuse a key for another account',
                f'fees_account = "{d.fees_account}"',
                f'tax_account = "{d.tax_account}"   # VAT inside Stripe\'s monthly fee debits',
                "rules = [",
                '    [\'\\bpayout\\b\', "Assets:CH:GmbH:Current:Wise:EUR", "*"],',
                "]",
                "",
                "[import.stripe.accounts]",
                rows,
            ]
        )
    raise InitError(f"unknown importer {importer!r} (known: {', '.join(_KNOWN_IMPORTERS)})")


# ── AGENTS.md — the AI-first payload ─────────────────────────────────────────


def _agents_md(answers: Answers) -> str:
    return f"""# Working on {answers.entity_name}'s books with an AI agent

These are plain-text ([beancount](https://beancount.github.io)) books managed
with [`quints`](https://github.com/sealambda/quints). **`quints` is a
deterministic tool — you drive it, it never calls a model.** Your job is to
extend and maintain the ledger; `quints` validates and reports on it.

## Layout

- `main.bean` — the ledger (accounts + transactions). `plugin
  "quints.plugins.kmu"` is enabled: every `*:CH:GmbH:*` account **must** be
  opened with a four-digit `kmu:` code (Swiss KMU Kontenrahmen).
- `prices.bean` — FX rates; refresh with `quints prices sync`.
- `quints.toml` — entity config (name, VAT, importer rules). VAT *rates* are
  law and live in code, not here.
- `staging/` — importer drafts land here; `inbox/` — source documents.

## Extending the chart of accounts (the part that needs judgement)

Add income/expense sub-trees for this business as `open` directives, each with
the KMU code it rolls up to, e.g.:

```beancount
2026-01-01 open Expenses:CH:GmbH:Marketing:Ads CHF
  kmu: "6600"  ; Advertising
```

Run `quints report konten` to see the codes already in use. Pick codes from the
KMU Kontenrahmen; `quints check` fails on a `:CH:GmbH:` account with no valid
`kmu:` code.

## The loop

1. Draft bank/PSP activity: `quints import ubs <file>` → `staging/`.
2. Review, add the VAT decision (InputVAT / Bezugsteuer / none) and a linked
   document, then move drafts into `main.bean`.
3. **Always** `quints check` before you consider the books consistent.

## Machine-readable surfaces (prefer these over scraping text)

- Every reporting command takes `--json`: `quints mwst -q 2026-Q3 --json`,
  `quints status --json`, `quints report bilanz --json`.
- `quints schema` writes JSON Schemas for the invoice/issuer/customer files.

Never invent VAT numbers or rates — compute them with `quints mwst`.
"""


def _gitignore() -> str:
    return "\n".join(
        [
            "# quints working directories",
            "/staging/",
            ".env",
            "__pycache__/",
            "*.pdf",  # rendered invoices/statements — regenerate from source
            "",
        ]
    )


# ── plan / write ─────────────────────────────────────────────────────────────


def plan(answers: Answers) -> list[ScaffoldFile]:
    """Answers → the ordered set of files to materialise. Pure, no I/O."""
    _validate(answers)
    files = [
        ScaffoldFile(Path("quints.toml"), _quints_toml(answers)),
        ScaffoldFile(Path("main.bean"), _main_bean(answers)),
        ScaffoldFile(Path("prices.bean"), _prices_bean(answers)),
        ScaffoldFile(Path("AGENTS.md"), _agents_md(answers)),
        ScaffoldFile(Path(".gitignore"), _gitignore()),
        ScaffoldFile(Path("inbox/.gitkeep"), ""),
        ScaffoldFile(Path("staging/.gitkeep"), ""),
        ScaffoldFile(Path("documents/.gitkeep"), ""),
    ]
    return files


def _validate(answers: Answers) -> None:
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
