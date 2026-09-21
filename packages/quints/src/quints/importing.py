"""Statement imports → staging drafts (docs/plans/02).

Importers never write to ``books/``: drafts land in ``staging/`` (git-ignored)
where a human (or agent, via the reconcile workflow) completes the counter leg,
decides VAT treatment, links the document, and cut-pastes into the ledger.

Dedup is two-layered:

- the importers themselves skip source references (``ubs_ref:`` / ``wise_id:``
  / ``stripe_id:``) already present in the ledger — re-imports are idempotent
  once entries carry references;
- entries booked **before** the importers existed carry no reference, so a
  second pass drops drafts whose cash-account posting matches an existing one
  by amount within a ±3-day window (reported, so the match is auditable).

``fetch_stripe_invoices`` files the customer **invoice** PDF (Stripe's
``invoice_pdf``) into ``inbox/`` for each charge. That is the document with the
customer's name, address and tax number on it, which is what substantiates the
place of supply behind an export booking; the payment itself is already
evidenced by the ``stripe_id:`` reference on the draft, so no payment receipt
is needed. Stripe does not reliably link an invoice to the balance transaction
that settles it, so the pairing is correlated and ties are refused.

Wise statements are fetched through the SCA-capable client in
``beangulp_wise`` and saved as JSON next to the drafts — every import stays
auditable and replayable offline. Credentials come from ``.env``:
``QUINTS_WISE_API_TOKEN`` and ``QUINTS_WISE_PRIVATE_KEY`` (path to the RSA key
whose public half is registered in Wise → Settings → API tokens).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as TodayDate
from decimal import Decimal
from pathlib import Path
from typing import TypeVar

from beancount.core import data
from beancount.parser import printer

from beangulp_mt940 import Importer as Mt940Importer
from beangulp_stripe import (
    CHARGE_TYPES,
    CREATED_TOLERANCE_SECONDS,
    FEE_TYPES,
    StripeClient,
    StripeError,
    correlate_invoices,
)
from beangulp_stripe import Importer as StripeImporter
from beangulp_wise import Importer as WiseImporter
from beangulp_wise import ScaChallenge, WiseClient, merge_conversions
from beangulp_yapeal import Importer as YapealImporter

from . import config, ledger
from . import receivables as recv_mod
from .invoice.model import slugify

LEGACY_WINDOW_DAYS = 3
DEFAULT_STAGING = Path("staging")


_Section = TypeVar(
    "_Section", config.UbsImport, config.YapealImport, config.WiseImport, config.StripeImport
)


def _require(section: _Section | None, name: str, *keys: str) -> _Section:
    """The importer's entity data ([import.<name>] in quints.toml) is config."""
    if section is None:
        raise ValueError(f"[import.{name}] is not configured — add it to quints.toml")
    for key in keys:
        if getattr(section, key) in (None, ""):
            raise ValueError(f"[import.{name}] needs '{key}' in quints.toml")
    return section


# Rule flags: '*' = clearing legs that are complete as drafted; '!' = direct
# expenses that still need a VAT decision and a linked document before books/.


def ubs_importer(cfg: config.Config | None = None) -> Mt940Importer:
    s = _require((cfg or config.get()).import_ubs, "ubs", "iban")
    return Mt940Importer(
        s.account,
        iban=s.iban,
        currency=s.currency,
        meta_key="ubs_ref",
        payee_rules=s.rules,
    )


def yapeal_importer(cfg: config.Config | None = None) -> YapealImporter:
    s = _require((cfg or config.get()).import_yapeal, "yapeal")
    return YapealImporter(
        s.account,
        iban=s.iban,
        currency=s.currency,
        meta_key="bank_ref",
        payee_rules=s.rules,
    )


def wise_importer(cfg: config.Config | None = None) -> WiseImporter:
    s = _require((cfg or config.get()).import_wise, "wise")
    return WiseImporter(
        s.account_map,
        fees_account=s.fees_account,
        holder=s.holder,
        meta_key="wise_id",
        payee_rules=s.rules,
    )


def stripe_importer(cfg: config.Config | None = None) -> StripeImporter:
    s = _require((cfg or config.get()).import_stripe, "stripe", "account_id")
    return StripeImporter(
        s.account_map,
        fees_account=s.fees_account,
        tax_account=s.tax_account,
        account_id=s.account_id,
        meta_key="stripe_id",
        payee_rules=s.rules,
    )


# ── shared pipeline ──────────────────────────────────────────────────────────


@dataclass
class ImportResult:
    source: str
    out_path: Path | None = None
    drafts: list[data.Transaction] = field(default_factory=list)  # written to staging
    balances: list[data.Balance] = field(default_factory=list)  # closing-balance assertions
    skipped_ref: int = 0  # deduped by reference metadata
    legacy_matches: list[tuple[data.Transaction, TodayDate]] = field(
        default_factory=list
    )  # (draft, booked date) pairs
    receivable_matches: list[tuple[str, data.Transaction]] = field(
        default_factory=list
    )  # (invoice number, draft)
    fee_tax_periods: list[str] = field(default_factory=list)  # YYYY-MM, see _fee_tax_periods


def _cash_pool(
    entries: Sequence[data.Directive], accounts: set[str]
) -> list[tuple[TodayDate, Decimal, str]]:
    """Mutable pool of (date, amount, account) cash postings for legacy matching."""
    pool: list[tuple[TodayDate, Decimal, str]] = []
    for e in entries:
        if not isinstance(e, data.Transaction):
            continue
        for p in e.postings:
            if p.account in accounts and p.units is not None and p.units.number is not None:
                pool.append((e.date, p.units.number, p.account))
    return pool


def _split(
    result: ImportResult,
    extracted: Sequence[data.Directive],
    pool: list[tuple[TodayDate, Decimal, str]],
) -> None:
    """Sort extracted entries into drafts / legacy matches / balance assertions."""
    for entry in extracted:
        if isinstance(entry, data.Balance):
            result.balances.append(entry)
            continue
        if not isinstance(entry, data.Transaction):
            continue
        cash = entry.postings[0]
        if cash.units is None or cash.units.number is None:
            result.drafts.append(entry)  # no amount to match on — review by hand
            continue
        cash_number = cash.units.number
        match = next(
            (
                (d, n, a)
                for d, n, a in pool
                if a == cash.account
                and n == cash_number
                and abs((d - entry.date).days) <= LEGACY_WINDOW_DAYS
            ),
            None,
        )
        if match:
            pool.remove(match)  # a booked posting can absorb only one draft
            result.legacy_matches.append((entry, match[0]))
        else:
            result.drafts.append(entry)


def match_receivables(
    result: ImportResult, existing: Sequence[data.Directive], cfg: config.Config
) -> None:
    """Link incoming drafts to open invoices (docs/plans/02 §2.1).

    `quints invoice` derives the QRR (QR-bill) and SCOR/RF (SEPA) references
    deterministically from the invoice number, so a payment carrying either —
    or the plain number — identifies its invoice exactly. On a match the
    draft's counter leg becomes the receivable clearing, linked `^<number>`
    and flagged `*` (complete as drafted). A reference that fits more than one
    open invoice matches nothing: the draft stays flagged `!` for a human,
    which is the only honest outcome."""
    from .match import find_invoice, payment_text, reference_index

    opens = recv_mod.compute_from_entries(existing, TodayDate.today(), cfg)
    if not opens:
        return
    index = reference_index(opens)

    for i, draft in enumerate(result.drafts):
        cash = draft.postings[0]
        if cash.units is None or cash.units.number is None or cash.units.number <= 0:
            continue  # only incoming payments clear receivables
        hit = find_invoice(index, *payment_text(draft))
        if hit is None or hit.number is None:
            continue
        number = hit.number
        postings = list(draft.postings)
        if len(postings) == 1:
            # Only the cash leg is known — add the elided receivable clearing
            # posting so the matched draft still balances.
            postings.append(data.Posting(cfg.receivable, None, None, None, None, None))
        elif len(postings) == 2:
            postings[1] = postings[1]._replace(account=cfg.receivable)
        else:
            continue  # ambiguous shape — leave the draft unmatched, still flagged for review
        meta = dict(draft.meta or {})
        meta["invoice"] = number
        matched = draft._replace(
            flag="*",
            meta=meta,
            links=frozenset(draft.links or ()) | {number},
            postings=postings,
        )
        result.drafts[i] = matched
        result.receivable_matches.append((number, matched))


def _write_staging(result: ImportResult, out_dir: Path, source: str) -> None:
    if not (result.drafts or result.balances):
        return
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{TodayDate.today().isoformat()}-{source}.bean"
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"; drafts from {result.source} — review, complete, move to books/, delete\n")
        f.write("; legs to fill: counter account, VAT (InputVAT/Bezugsteuer/none), document:\n\n")
        for entry in result.drafts:
            f.write(printer.format_entry(entry) + "\n")
        for balance in result.balances:
            f.write(printer.format_entry(balance))
    result.out_path = out


def run_yapeal(
    statement: Path,
    ledger_path: Path,
    out_dir: Path = DEFAULT_STAGING,
    cfg: config.Config | None = None,
) -> ImportResult:
    cfg = cfg or config.get()
    importer = yapeal_importer(cfg)
    yapeal = _require(cfg.import_yapeal, "yapeal")
    if not importer.identify(str(statement)):
        raise ValueError(f"{statement} is not a Yapeal CSV statement for {yapeal.account}")

    existing, _ = ledger.load_entries(ledger_path)
    total = len(importer.extract(str(statement), existing=[]))
    extracted = importer.extract(str(statement), existing=existing)

    result = ImportResult(source=str(statement))
    result.skipped_ref = total - len(extracted)
    _split(result, extracted, _cash_pool(existing, {yapeal.account}))
    match_receivables(result, existing, cfg)
    _write_staging(result, out_dir, "yapeal")
    return result


def run_ubs(
    statement: Path,
    ledger_path: Path,
    out_dir: Path = DEFAULT_STAGING,
    cfg: config.Config | None = None,
) -> ImportResult:
    cfg = cfg or config.get()
    importer = ubs_importer(cfg)
    ubs = _require(cfg.import_ubs, "ubs", "iban")
    if not importer.identify(str(statement)):
        raise ValueError(f"{statement} is not an MT940 statement for {ubs.iban}")

    existing, _ = ledger.load_entries(ledger_path)
    total = len(importer.extract(str(statement), existing=[]))
    extracted = importer.extract(str(statement), existing=existing)

    result = ImportResult(source=str(statement))
    result.skipped_ref = total - len(extracted)
    _split(result, extracted, _cash_pool(existing, {ubs.account}))
    match_receivables(result, existing, cfg)
    _write_staging(result, out_dir, "ubs")
    return result


def run_wise(
    statements: list[Path],
    ledger_path: Path,
    out_dir: Path = DEFAULT_STAGING,
    cfg: config.Config | None = None,
) -> ImportResult:
    cfg = cfg or config.get()
    importer = wise_importer(cfg)
    wise = _require(cfg.import_wise, "wise")
    for statement in statements:
        if not importer.identify(str(statement)):
            raise ValueError(
                f"{statement} is not a Wise balance statement for {wise.holder} "
                f"in {sorted(wise.account_map)}"
            )

    existing, _ = ledger.load_entries(ledger_path)
    raw: data.Directives = []  # without ledger dedup, to count reference skips
    deduped: data.Directives = []
    for statement in statements:
        raw += importer.extract(str(statement), existing=[])
        deduped += importer.extract(str(statement), existing=existing)

    result = ImportResult(source=", ".join(str(s) for s in statements))
    result.skipped_ref = _txn_count(raw) - _txn_count(deduped)
    _split(result, merge_conversions(deduped), _cash_pool(existing, set(wise.account_map.values())))
    match_receivables(result, existing, cfg)
    _write_staging(result, out_dir, "wise")
    return result


def run_stripe(
    statements: list[Path],
    ledger_path: Path,
    out_dir: Path = DEFAULT_STAGING,
    cfg: config.Config | None = None,
) -> ImportResult:
    cfg = cfg or config.get()
    importer = stripe_importer(cfg)
    stripe = _require(cfg.import_stripe, "stripe", "account_id")
    for statement in statements:
        if not importer.identify(str(statement)):
            raise ValueError(
                f"{statement} is not a Stripe balance-transactions file for "
                f"{stripe.account_id} in {sorted(stripe.account_map)}"
            )

    existing, _ = ledger.load_entries(ledger_path)
    raw: data.Directives = []  # without ledger dedup, to count reference skips
    deduped: data.Directives = []
    for statement in statements:
        raw += importer.extract(str(statement), existing=[])
        deduped += importer.extract(str(statement), existing=existing)

    result = ImportResult(source=", ".join(str(s) for s in statements))
    result.skipped_ref = _txn_count(raw) - _txn_count(deduped)
    _split(result, deduped, _cash_pool(existing, set(stripe.account_map.values())))
    match_receivables(result, existing, cfg)
    result.fee_tax_periods = _fee_tax_periods(statements)
    _write_staging(result, out_dir, "stripe")
    return result


def _txn_count(entries: Sequence[data.Directive]) -> int:
    return sum(1 for e in entries if isinstance(e, data.Transaction))


def fetch_wise(
    interval_start: str,
    interval_end: str,
    out_dir: Path = DEFAULT_STAGING,
    cfg: config.Config | None = None,
) -> list[Path]:
    """Download one statement.json per currency balance into ``out_dir``."""
    wise = _require((cfg or config.get()).import_wise, "wise", "holder")
    holder = wise.holder
    if not holder:  # unreachable: _require checked, but narrows the Optional
        raise ValueError("[import.wise] needs 'holder' in quints.toml")
    _load_env()
    token = os.environ.get("QUINTS_WISE_API_TOKEN")
    if not token:
        raise ScaChallenge("QUINTS_WISE_API_TOKEN is not set (.env)")
    pem = None
    key_path = os.environ.get("QUINTS_WISE_PRIVATE_KEY")
    if key_path and Path(key_path).exists():
        pem = Path(key_path).read_bytes()

    client = WiseClient(token, private_key_pem=pem)
    profile = client.profile_id(holder)
    paths: list[Path] = []
    out_dir.mkdir(exist_ok=True)
    for balance in client.balances(profile):
        currency = balance["currency"]
        if not isinstance(currency, str) or currency not in wise.account_map:
            continue
        balance_id = balance["id"]
        if not isinstance(balance_id, int):
            raise ValueError(f"Wise balance for {currency} has a non-integer id: {balance_id!r}")
        statement = client.balance_statement(
            profile,
            balance_id,
            currency,
            f"{interval_start}T00:00:00.000Z",
            f"{interval_end}T23:59:59.999Z",
        )
        path = out_dir / f"wise-{currency.lower()}-{interval_start}-{interval_end}.json"
        path.write_text(json.dumps(statement, indent=1))
        paths.append(path)
    return paths


def fetch_stripe(
    interval_start: str,
    interval_end: str,
    out_dir: Path = DEFAULT_STAGING,
    cfg: config.Config | None = None,
) -> list[Path]:
    """Download balance transactions (+ balance snapshot) into ``out_dir``.

    Saved as one JSON wrapper the importer reads back — auditable and
    replayable offline, same convention as Wise. The balance snapshot (and
    with it the assertion) is only meaningful when the window extends to
    today, so it is included only then.
    """
    import datetime as dt

    stripe = _require((cfg or config.get()).import_stripe, "stripe", "account_id")
    _load_env()
    key = os.environ.get("QUINTS_STRIPE_API_KEY")
    if not key:
        raise StripeError("QUINTS_STRIPE_API_KEY is not set (.env)")

    client = StripeClient(key)
    account = client.account()
    if account.get("id") != stripe.account_id:
        raise StripeError(
            f"this key belongs to {account.get('id')}, expected {stripe.account_id} "
            "([import.stripe] account_id) — wrong Stripe account"
        )

    def _ts(day: str, end: bool) -> int:
        moment = dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc)
        if end:
            moment += dt.timedelta(days=1)
        return int(moment.timestamp()) - (1 if end else 0)

    wrapper: dict[str, object] = {
        "account": {"id": account.get("id")},
        "data": client.balance_transactions(_ts(interval_start, False), _ts(interval_end, True)),
    }
    today = TodayDate.today()
    if TodayDate.fromisoformat(interval_end) >= today:
        snapshot = client.balance()
        wrapper["balance"] = {
            "as_of": today.isoformat(),
            "available": snapshot.get("available") or [],
            "pending": snapshot.get("pending") or [],
        }

    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"stripe-{interval_start}-{interval_end}.json"
    path.write_text(json.dumps(wrapper, indent=1))
    return [path]


# Invoice numbers go into a filename verbatim (the convention keeps them
# readable); anything that could change the path is replaced.
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class InvoiceDoc:
    """One Stripe customer invoice PDF, filed into ``inbox/``."""

    name: str
    path: Path  # under inbox/, or wherever it turned out to be filed already
    number: str
    customer: str
    txn_id: str  # the balance transaction it documents
    matched_by: str  # how the two were paired: "id" or "amount+time"
    skipped: bool  # already in inbox/ or documents/, so not downloaded again


def _stripe_transactions(statements: Sequence[Path]) -> list[dict[str, object]]:
    """Balance transactions out of staged Stripe JSON (wrapper or bare array)."""
    transactions: list[dict[str, object]] = []
    for statement in statements:
        document = json.loads(statement.read_text())
        batch = document if isinstance(document, list) else document.get("data")
        if isinstance(batch, list):
            transactions.extend(t for t in batch if isinstance(t, dict))
    return transactions


def _fee_tax_periods(statements: Sequence[Path]) -> list[str]:
    """Months whose Stripe fee debit carries VAT (``fee_details`` type ``tax``).

    Stripe's own monthly tax invoice is the document for that VAT, and it has
    no API at all — Dashboard only, published by the 10th of the next month. So
    the import can't fetch it; it can only say which months need one pulled by
    hand before the quarterly VAT close.
    """
    import datetime as dt

    periods: set[str] = set()
    for txn in _stripe_transactions(statements):
        if txn.get("type") not in FEE_TYPES:
            continue
        details = txn.get("fee_details")
        if not isinstance(details, list):
            continue
        if not any(isinstance(d, dict) and d.get("type") == "tax" for d in details):
            continue
        created = txn.get("created")
        if isinstance(created, int):
            periods.add(dt.datetime.fromtimestamp(created, tz=dt.timezone.utc).strftime("%Y-%m"))
    return sorted(periods)


def _already_filed(root: Path, name: str) -> Path | None:
    """Where ``name`` is already filed, if it is — inbox/ or documents/.

    Documents move from ``inbox/`` to ``documents/`` once booked, so checking
    the inbox alone would re-download every invoice that has been filed.
    """
    candidate = root / "inbox" / name
    if candidate.exists():
        return candidate
    documents = root / "documents"
    if documents.is_dir():
        for path in documents.rglob(name):
            if path.is_file():
                return path
    return None


def fetch_stripe_invoices(
    statements: Sequence[Path],
    ledger_path: Path,
    cfg: config.Config | None = None,
) -> list[InvoiceDoc]:
    """File each charge's customer invoice PDF into ``inbox/``.

    The balance transactions come from the statements just imported, so the
    correlation runs against exactly what was booked, and invoices are listed
    only over the window those transactions span. A charge with no invoice is
    normal and silent (payouts, refunds and monthly fee debits have none); a
    charge that could belong to more than one invoice raises
    :class:`StripeError`, because filing a PDF against the wrong entry is worse
    than filing none.

    The key is checked against ``[import.stripe] account_id`` first: a key for
    another account would list another entity's invoices, and those correlate
    against these charges just as well.

    Names follow the ledger convention ``YYYY-MM-DD.payee.narrative.ext`` with
    the invoice number as the narrative — the same shape ``quints invoice``
    files its own PDFs under, so ``quints inbox`` and ``quints match`` read the
    hints. Anything already in ``inbox/`` or ``documents/`` is left alone, so
    re-runs download nothing.
    """
    import datetime as dt

    stripe = _require((cfg or config.get()).import_stripe, "stripe", "account_id")
    transactions = _stripe_transactions(statements)
    created = [
        t.get("created")
        for t in transactions
        if t.get("type") in CHARGE_TYPES and isinstance(t.get("created"), int)
    ]
    if not created:
        return []

    _load_env()
    key = os.environ.get("QUINTS_STRIPE_API_KEY")
    if not key:
        raise StripeError("QUINTS_STRIPE_API_KEY is not set (.env)")
    client = StripeClient(key)
    # The statements were checked against the configured account, but the key
    # in .env is a different object — a key for another account would list
    # another entity's invoices and correlate them against these charges.
    account = client.account()
    if account.get("id") != stripe.account_id:
        raise StripeError(
            f"this key belongs to {account.get('id')}, expected {stripe.account_id} "
            "([import.stripe] account_id) — wrong Stripe account"
        )
    window = [c for c in created if isinstance(c, int)]
    invoices = client.invoices(
        min(window) - CREATED_TOLERANCE_SECONDS,
        max(window) + CREATED_TOLERANCE_SECONDS,
    )

    matches, ambiguities = correlate_invoices(transactions, invoices)
    if ambiguities:
        raise StripeError(
            "cannot tell which invoice documents which charge — nothing filed:\n  "
            + "\n  ".join(ambiguities)
        )

    root = ledger_path.resolve().parent
    docs: list[InvoiceDoc] = []
    for match in matches:
        date = dt.datetime.fromtimestamp(match.created, tz=dt.timezone.utc).date()
        number = _FILENAME_SAFE.sub("-", match.number)
        name = f"{date.isoformat()}.{slugify(match.customer)}.{number}.pdf"
        filed = _already_filed(root, name)
        if filed is not None:
            docs.append(
                InvoiceDoc(
                    name, filed, number, match.customer, match.txn_id, match.matched_by, True
                )
            )
            continue
        payload = client.document(match.pdf_url)
        inbox_dir = root / "inbox"
        inbox_dir.mkdir(exist_ok=True)
        path = inbox_dir / name
        path.write_bytes(payload)
        docs.append(
            InvoiceDoc(name, path, number, match.customer, match.txn_id, match.matched_by, False)
        )
    docs.sort(key=lambda d: d.name)  # the name leads with the date, so: chronological
    return docs


def _load_env(path: Path = Path(".env")) -> None:
    """Minimal .env loader (no new dependency); never overrides real env."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
