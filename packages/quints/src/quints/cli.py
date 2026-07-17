"""`quints` — Swiss VAT & accounting CLI for plain-text (beancount) books."""

from __future__ import annotations

from dataclasses import replace
from datetime import date as Date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import typer

from . import (
    config as config_mod,
)
from . import (
    fx as fx_mod,
)
from . import (
    importing as importing_mod,
)
from . import (
    inbox as inbox_mod,
)
from . import (
    init as init_mod,
)
from . import (
    kmu as kmu_mod,
)
from . import (
    ledger,
    ui,
)
from . import (
    match as match_mod,
)
from . import (
    mwst as mwst_mod,
)
from . import (
    prices as prices_mod,
)
from . import (
    receivables as recv_mod,
)
from . import (
    settlement as settle_mod,
)
from . import (
    vat as vat_mod,
)

app = typer.Typer(
    no_args_is_help=True,
    add_completion=True,
    help="quints — Swiss VAT & accounting for plain-text books (MWST, statements, imports).",
)


@app.callback()
def _main(
    config: Path | None = typer.Option(
        None, "--config", help="quints.toml path (default: ./quints.toml, else built-in defaults)."
    ),
):
    config_mod.set_path(config)


prices_app = typer.Typer(no_args_is_help=True, help="Price database (BAZG daily CHF rates).")
app.add_typer(prices_app, name="prices")
report_app = typer.Typer(
    no_args_is_help=True,
    help="Statutory statements grouped by the Swiss KMU chart of accounts (OR Art. 959a/959b).",
)
app.add_typer(report_app, name="report")
import_app = typer.Typer(
    no_args_is_help=True,
    help="Draft transactions from bank/PSP statements into staging/ (never books/).",
)
app.add_typer(import_app, name="import")
fx_app = typer.Typer(no_args_is_help=True, help="FX helpers (year-end revaluation).")
app.add_typer(fx_app, name="fx")


def _lang_option() -> str:
    return typer.Option(
        None, "--lang", "-l", help="Report language: en or de (default from quints.toml)."
    )


def _emit(report, render, lang: str | None, as_json: bool) -> None:
    lang = lang or config_mod.get().report_language
    if as_json:
        import dataclasses
        import json

        typer.echo(json.dumps(dataclasses.asdict(report), indent=2, default=str))
    else:
        render(report, lang=lang)


def _file_option() -> Path:
    return typer.Option(ledger.DEFAULT_LEDGER, "--file", "-f", help="Ledger file.")


def _parse_date(text: str) -> Date:
    try:
        return Date.fromisoformat(text)
    except ValueError:
        typer.secho(f"ERROR: invalid date {text!r} (use YYYY-MM-DD)", fg="red", err=True)
        raise typer.Exit(1) from None


def _json_out(payload) -> None:
    """Emit a machine-readable payload (Decimals/dates as strings)."""
    import json

    typer.echo(json.dumps(payload, indent=2, default=str))


def _require_ledger(file: Path) -> None:
    if not file.exists():
        typer.secho(f"ERROR: ledger not found: {file}", fg="red", err=True)
        raise typer.Exit(1)


@app.command()
def vat(
    amount: str = typer.Argument(
        ..., help="VAT amount in the invoice currency (or net price with --net)."
    ),
    currency: str = typer.Argument(..., help="Invoice currency, e.g. USD or EUR."),
    date: str = typer.Argument(
        ..., metavar="YYYY-MM-DD", help="Invoice date (picks the BAZG rate)."
    ),
    net: bool = typer.Option(
        False, "--net", help="Treat amount as the net price; VAT = 8.1% first."
    ),
    bezugsteuer: bool = typer.Option(
        False,
        "--bezugsteuer",
        "-b",
        help="Reverse charge (Art. 45 MWSTG): amount is the net foreign invoice; "
        "emit the InputVAT + Bezugsteuer posting pair (implies --net).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Convert a foreign-currency VAT amount to a CHF InputVAT posting."""
    on = _parse_date(date)
    net = net or bezugsteuer
    try:
        amt = Decimal(amount)
    except InvalidOperation:
        typer.secho(f"ERROR: invalid amount {amount!r}", fg="red", err=True)
        raise typer.Exit(1) from None
    _require_ledger(file)

    price_map, errors = ledger.build_price_map(file)
    if errors:
        typer.secho(f"WARNING: {len(errors)} loader error(s) in {file.name}", fg="yellow", err=True)
    try:
        posting = vat_mod.convert(amt, currency, on, price_map, net=net)
    except vat_mod.RateUnavailable as e:
        typer.secho(
            f"ERROR: no {e.ccy}→CHF rate on or before {e.on} in {file.name}.\n"
            f"       Fetch it:  uv run quints prices sync",
            fg="red",
            err=True,
        )
        raise typer.Exit(1) from None
    text = posting.render_bezugsteuer() if bezugsteuer else posting.render()
    if as_json:
        import dataclasses

        _json_out({**dataclasses.asdict(posting), "bezugsteuer": bezugsteuer, "posting_text": text})
        return
    typer.echo(text)


@app.command()
def mwst(
    quarter: str | None = typer.Option(
        None, "--quarter", "-q", help="e.g. 2026-Q2 (instead of --from/--to)."
    ),
    from_: str | None = typer.Option(None, "--from", help="Period start YYYY-MM-DD."),
    to: str | None = typer.Option(None, "--to", help="Period end YYYY-MM-DD."),
    settle: bool = typer.Option(
        False, "--settle", help="Also print the settlement transaction to paste (period close)."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Machine-readable output (not with --settle)."
    ),
    file: Path = _file_option(),
):
    """Swiss MWST (VAT) report for a reporting period."""
    if as_json and settle:
        typer.secho("ERROR: --json and --settle are mutually exclusive.", fg="red", err=True)
        raise typer.Exit(1)
    if quarter:
        try:
            date_from, date_to = mwst_mod.quarter_range(quarter)
        except ValueError as e:
            typer.secho(f"ERROR: {e}", fg="red", err=True)
            raise typer.Exit(1) from None
        label = quarter.upper().replace(" ", "")
    elif from_ and to:
        date_from = _parse_date(from_).isoformat()
        date_to = _parse_date(to).isoformat()
        label = None
    else:
        typer.secho("ERROR: provide --quarter, or both --from and --to.", fg="red", err=True)
        raise typer.Exit(1)
    _require_ledger(file)
    report = mwst_mod.compute(file, date_from, date_to)
    if as_json:
        import dataclasses
        import json

        typer.echo(json.dumps(dataclasses.asdict(report), indent=2, default=str))
        return
    mwst_mod.render(report)
    if settle:
        settle_mod.render_settlement(settle_mod.build_settlement(file, report, label))


@app.command()
def status(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Outstanding VAT owed to the ESTV (filed but unpaid), with due dates."""
    _require_ledger(file)
    liabilities, unlinked, total, today = settle_mod.outstanding(file)
    if as_json:
        import dataclasses
        import json

        typer.echo(
            json.dumps(
                {
                    "today": str(today),
                    "liabilities": [dataclasses.asdict(liab) for liab in liabilities],
                    "unlinked_owed": str(unlinked),
                    "total_owed": str(total),
                },
                indent=2,
                default=str,
            )
        )
        return
    settle_mod.render_status(liabilities, unlinked, total, today)


@app.command()
def receivables(
    at: str | None = typer.Option(
        None, "--at", metavar="YYYY-MM-DD", help="Aging as of this date (default: today)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Open invoices (aging), grouped by invoice id against Receivable:Trade."""
    _require_ledger(file)
    open_invoices, ref = recv_mod.compute(file, _parse_date(at) if at else None)
    if as_json:
        import dataclasses
        import json

        typer.echo(
            json.dumps(
                {"at": str(ref), "open": [dataclasses.asdict(o) for o in open_invoices]},
                indent=2,
                default=str,
            )
        )
        return
    recv_mod.render(open_invoices, ref)


@app.command()
def inbox(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Inventory inbox/ documents: filename hints, duplicates, already-linked."""
    _require_ledger(file)
    docs = inbox_mod.compute(file)
    if as_json:
        import dataclasses

        _json_out({"inbox": [dataclasses.asdict(d) for d in docs]})
        return
    inbox_mod.render(docs)


@app.command()
def match(
    staging: Path | None = typer.Option(
        None, "--staging", help="Staging directory (default: ./staging)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Match staging drafts and inbox documents to invoices and bookings (scored)."""
    _require_ledger(file)
    results = match_mod.compute(file, staging_dir=staging)
    if as_json:
        import dataclasses

        _json_out({"matches": [dataclasses.asdict(m) for m in results]})
        return
    match_mod.render(results)


@prices_app.command("sync")
def prices_sync(
    out: Path = typer.Option(
        ledger.DEFAULT_PRICES, "--out", help="Price file (default: prices.bean)."
    ),
    from_: str | None = typer.Option(
        None,
        "--from",
        help="Repair: re-scan from this date and fill ANY missing days (heals interior gaps).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
):
    """Fetch new BAZG daily CHF rates into the price file (full precision, gap-aware).

    Without --from: extend each currency forward to today (fast, daily use).
    With --from DATE: re-scan the whole range and fill any missing days.
    """
    repair = _parse_date(from_) if from_ else None
    result = prices_mod.sync(out, repair_from=repair)
    if as_json:
        _json_out(
            {
                "file": str(out),
                "wrote": result.wrote,
                "added": result.added,
                "per_currency": {
                    ccy: {"added": added, "had_through": last}
                    for ccy, (added, last) in result.per_currency.items()
                },
            }
        )
        return
    for ccy, (added, last) in result.per_currency.items():
        where = f"had through {last}" if last else "was empty"
        typer.echo(f"{ccy}: +{added} rate(s) ({where}).")
    if result.wrote:
        typer.secho(f"Wrote {result.added} new price(s) to {out.name} (sorted).", fg="green")
    else:
        typer.echo(f"{out.name} already current.")


@report_app.command()
def bilanz(
    at: str = typer.Option(..., "--at", metavar="YYYY-MM-DD", help="Report date."),
    lang: str = _lang_option(),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Balance sheet (Bilanz, OR Art. 959a) grouped by KMU codes."""
    _parse_date(at)
    _require_ledger(file)
    _emit(kmu_mod.compute_bilanz(file, at), kmu_mod.render_bilanz, lang, as_json)


@report_app.command()
def erfolg(
    from_: str | None = typer.Option(None, "--from", metavar="YYYY-MM-DD"),
    to: str | None = typer.Option(None, "--to", metavar="YYYY-MM-DD"),
    year: int | None = typer.Option(None, "--year", help="Shortcut for a calendar year."),
    lang: str = _lang_option(),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Income statement (Erfolgsrechnung, OR Art. 959b) grouped by KMU codes."""
    date_from, date_to = _period(from_, to, year)
    _require_ledger(file)
    _emit(kmu_mod.compute_erfolg(file, date_from, date_to), kmu_mod.render_erfolg, lang, as_json)


@report_app.command()
def konten(
    from_: str | None = typer.Option(None, "--from", metavar="YYYY-MM-DD"),
    to: str | None = typer.Option(None, "--to", metavar="YYYY-MM-DD"),
    year: int | None = typer.Option(None, "--year", help="Shortcut for a calendar year."),
    lang: str = _lang_option(),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Per-KMU-code transaction listings (Kontoblätter) — auditor detail."""
    date_from, date_to = _period(from_, to, year)
    _require_ledger(file)
    _emit(kmu_mod.compute_konten(file, date_from, date_to), kmu_mod.render_konten, lang, as_json)


@report_app.command()
def statements(
    year: int = typer.Option(..., "--year", help="Fiscal year."),
    at: str | None = typer.Option(
        None, "--at", metavar="YYYY-MM-DD", help="Balance-sheet date (default: <year>-12-31)."
    ),
    lang: str = _lang_option(),
    out: Path | None = typer.Option(None, "--out", "-o", help="Output PDF path."),
    file: Path = _file_option(),
):
    """Bilanz + Erfolgsrechnung as one PDF for the Treuhänder/auditor."""
    from . import report_pdf

    _require_ledger(file)
    balance_date = at or f"{year}-12-31"
    _parse_date(balance_date)
    lang = lang or config_mod.get().report_language
    bilanz_report = kmu_mod.compute_bilanz(file, balance_date)
    erfolg_report = kmu_mod.compute_erfolg(file, f"{year}-01-01", f"{year}-12-31")
    out = out or Path(f"statements-{year}-{lang}.pdf")
    path = report_pdf.render_pdf(bilanz_report, erfolg_report, lang, out)
    typer.secho(f"Wrote {path}", fg="green")


@fx_app.command("revalue")
def fx_revalue(
    at: str = typer.Option(
        ..., "--at", metavar="YYYY-MM-DD", help="Revaluation date (usually 12-31)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Print the year-end FX revaluation transaction(s) to paste (Art. 960 OR)."""
    _parse_date(at)
    _require_ledger(file)
    try:
        revaluations = fx_mod.compute(file, at)
    except fx_mod.RateUnavailable as e:
        typer.secho(
            f"ERROR: {e}.\n       Fetch rates:  uv run quints prices sync", fg="red", err=True
        )
        raise typer.Exit(1) from None
    if as_json:
        import dataclasses

        _json_out(
            {
                "at": at,
                "revaluations": [{**dataclasses.asdict(r), "delta": r.delta} for r in revaluations],
            }
        )
        return
    fx_mod.render(revaluations, at)


@import_app.command("ubs")
def import_ubs(
    statement: Path = typer.Argument(..., help="UBS MT940 statement file."),
    out: Path = typer.Option(importing_mod.DEFAULT_STAGING, "--out", help="Staging directory."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Draft the UBS CHF account's activity from an MT940 statement."""
    _require_ledger(file)
    if not statement.exists():
        typer.secho(f"ERROR: statement not found: {statement}", fg="red", err=True)
        raise typer.Exit(1)
    try:
        result = importing_mod.run_ubs(statement, file, out)
    except ValueError as e:
        typer.secho(f"ERROR: {e}", fg="red", err=True)
        raise typer.Exit(1) from None

    _report_import(result, as_json)


@import_app.command("wise")
def import_wise(
    statements: list[Path] | None = typer.Argument(None, help="Wise statement.json file(s)."),
    fetch: bool = typer.Option(False, "--fetch", help="Fetch statements from the Wise API first."),
    from_: str | None = typer.Option(None, "--from", metavar="YYYY-MM-DD"),
    to: str | None = typer.Option(None, "--to", metavar="YYYY-MM-DD"),
    out: Path = typer.Option(importing_mod.DEFAULT_STAGING, "--out", help="Staging directory."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Draft the Wise balances' activity (all currencies, conversions merged)."""
    _require_ledger(file)
    if fetch:
        if not (from_ and to):
            typer.secho("ERROR: --fetch needs --from and --to.", fg="red", err=True)
            raise typer.Exit(1)
        _parse_date(from_)
        _parse_date(to)
        try:
            statements = importing_mod.fetch_wise(from_, to, out)
        except importing_mod.WiseError as e:
            typer.secho(f"ERROR: {e}", fg="red", err=True)
            if isinstance(e, importing_mod.ScaChallenge):
                typer.secho(
                    "Statements are SCA-protected: upload .wise/public.pem in Wise "
                    "(Settings → API tokens → Manage public keys) and set "
                    "QUINTS_WISE_PRIVATE_KEY in .env.",
                    fg="yellow",
                    err=True,
                )
            raise typer.Exit(1) from None
        for path in statements:
            typer.echo(f"fetched {path}")
    if not statements:
        typer.secho("ERROR: pass statement.json file(s) or --fetch.", fg="red", err=True)
        raise typer.Exit(1)
    try:
        result = importing_mod.run_wise(list(statements), file, out)
    except ValueError as e:
        typer.secho(f"ERROR: {e}", fg="red", err=True)
        raise typer.Exit(1) from None
    _report_import(result, as_json)


@import_app.command("stripe")
def import_stripe(
    statements: list[Path] | None = typer.Argument(
        None, help="Stripe balance-transactions JSON file(s)."
    ),
    fetch: bool = typer.Option(
        False, "--fetch", help="Fetch balance transactions from the Stripe API first."
    ),
    from_: str | None = typer.Option(None, "--from", metavar="YYYY-MM-DD"),
    to: str | None = typer.Option(None, "--to", metavar="YYYY-MM-DD"),
    out: Path = typer.Option(importing_mod.DEFAULT_STAGING, "--out", help="Staging directory."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Draft the Stripe balance's activity (charges, monthly fees, payouts)."""
    _require_ledger(file)
    if fetch:
        if not (from_ and to):
            typer.secho("ERROR: --fetch needs --from and --to.", fg="red", err=True)
            raise typer.Exit(1)
        _parse_date(from_)
        _parse_date(to)
        try:
            statements = importing_mod.fetch_stripe(from_, to, out)
        except importing_mod.StripeError as e:
            typer.secho(f"ERROR: {e}", fg="red", err=True)
            typer.secho(
                "Fetching needs QUINTS_STRIPE_API_KEY in .env — a restricted key "
                "(Balance transaction sources: Read + Charges: Read) for the "
                "account configured as [import.stripe] account_id.",
                fg="yellow",
                err=True,
            )
            raise typer.Exit(1) from None
        for path in statements:
            typer.echo(f"fetched {path}")
    if not statements:
        typer.secho("ERROR: pass balance-transactions JSON file(s) or --fetch.", fg="red", err=True)
        raise typer.Exit(1)
    try:
        result = importing_mod.run_stripe(list(statements), file, out)
    except ValueError as e:
        typer.secho(f"ERROR: {e}", fg="red", err=True)
        raise typer.Exit(1) from None
    _report_import(result, as_json)


def _report_import(result, as_json: bool = False) -> None:
    if as_json:

        def txn(t):
            u = t.postings[0].units
            return {
                "date": str(t.date),
                "flag": t.flag,
                "payee": t.payee,
                "narration": t.narration,
                "amount": u.number,
                "currency": u.currency,
            }

        _json_out(
            {
                "source": result.source,
                "staging_file": str(result.out_path) if result.out_path else None,
                "skipped_ref": result.skipped_ref,
                "drafts": [txn(t) for t in result.drafts],
                "legacy_matches": [{**txn(t), "booked": str(d)} for t, d in result.legacy_matches],
                "receivable_matches": [
                    {**txn(t), "invoice": n} for n, t in result.receivable_matches
                ],
                "balances": [
                    {
                        "date": str(b.date),
                        "account": b.account,
                        "amount": b.amount.number,
                        "currency": b.amount.currency,
                    }
                    for b in result.balances
                ],
            }
        )
        return
    if result.skipped_ref:
        typer.echo(f"{result.skipped_ref} entr(ies) already imported (reference match) — skipped.")
    if result.legacy_matches:
        typer.echo(
            f"{len(result.legacy_matches)} entr(ies) matched already-booked postings "
            f"(amount within ±{importing_mod.LEGACY_WINDOW_DAYS}d):"
        )
        for draft, booked in result.legacy_matches:
            units = draft.postings[0].units
            typer.echo(f"  {draft.date}  {units}  {draft.payee}  → booked {booked}")
    if result.receivable_matches:
        typer.secho(
            f"{len(result.receivable_matches)} payment(s) matched open invoices "
            f"(receivable clearing drafted):",
            fg="green",
        )
        for number, draft in result.receivable_matches:
            typer.echo(f"  {draft.date}  {draft.postings[0].units}  ^{number}")
    if result.drafts:
        typer.secho(f"{len(result.drafts)} draft(s) → {result.out_path}", fg="green")
        for draft in result.drafts:
            typer.echo(f"  {draft.flag} {draft.date}  {draft.postings[0].units}  {draft.payee}")
    else:
        typer.echo("No new drafts.")
    for balance in result.balances:
        typer.echo(f"Closing balance assertion: {balance.date} {balance.amount} (in staging file).")


def _period(from_: str | None, to: str | None, year: int | None) -> tuple[str, str]:
    if year is not None:
        return f"{year}-01-01", f"{year}-12-31"
    if from_ and to:
        return _parse_date(from_).isoformat(), _parse_date(to).isoformat()
    typer.secho("ERROR: provide --year, or both --from and --to.", fg="red", err=True)
    raise typer.Exit(1)


@app.command()
def check(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    file: Path = _file_option(),
):
    """Validate the ledger (bean-check equivalent)."""
    _require_ledger(file)
    _entries, errors = ledger.load_entries(file)
    if as_json:
        _json_out(
            {
                "ok": not errors,
                "errors": [
                    {
                        "file": (e.source or {}).get("filename"),
                        "line": (e.source or {}).get("lineno"),
                        "message": e.message,
                    }
                    for e in errors
                ],
            }
        )
        raise typer.Exit(1 if errors else 0)
    if errors:
        from beancount.parser import printer

        printer.print_errors(errors)
        typer.secho(f"{len(errors)} error(s).", fg="red", err=True)
        raise typer.Exit(1)
    typer.secho("OK — no errors.", fg="green")


@app.command()
def invoice(
    data: Path = typer.Argument(..., help="Invoice file (.yaml/.toml/.json)."),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Output PDF (default: <number>.pdf)."
    ),
    issuer: Path = typer.Option(
        Path("invoicing/issuer.yaml"), "--issuer", help="Issuer config (.yaml/.toml/.json)."
    ),
    customers: Path = typer.Option(
        Path("invoicing/customers.yaml"),
        "--customers",
        help="Customer registry (.yaml/.toml/.json).",
    ),
    file: Path = _file_option(),
    verify: bool = typer.Option(
        True, "--verify/--no-verify", help="Cross-check total against the ledger."
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
):
    """Render a Swiss QR-bill invoice PDF (domestic or export)."""
    import dataclasses

    from .invoice import draft as dr
    from .invoice import model as m
    from .invoice import render as r
    from .invoice import verify as v

    for p, what in [(data, "invoice file"), (issuer, "issuer config")]:
        if not p.exists():
            typer.secho(f"ERROR: {what} not found: {p}", fg="red", err=True)
            raise typer.Exit(1)

    registry = m.load_customers(customers) if customers.exists() else None
    inv = m.load_invoice(data, registry)
    iss = m.load_issuer(issuer)
    out = out or Path(f"{inv.number}.pdf")
    path, totals, payload = r.render(inv, iss, out)

    qr_ok = None
    if payload:
        lines = payload.splitlines()
        qr_ok = lines[:1] == ["SPC"] and lines[-1] == "EPD"
    if not as_json:
        ui.console.print(
            f"[ok]Wrote[/] {path}  ·  {inv.kind}  ·  {inv.currency} {m.money(totals.grand_total)}"
        )
        if payload:
            ui.console.print(
                f"[muted]QR-bill payload: {'SPC…EPD ✓' if qr_ok else 'CHECK!'} "
                f"({len(payload.splitlines())} lines, ref {inv.reference or 'auto-QRR'})[/]"
            )

    cc = None
    ledger_draft = None
    if verify and file.exists():
        cc = v.cross_check(file, inv, totals)
        if not cc.found:
            ledger_draft = dr.build_draft(inv, totals)
        if not as_json:
            if not cc.found:
                ui.console.print(
                    f"[warn]No ledger txn for {inv.number}[/] — paste this draft "
                    f"into books/{inv.issue_date.year}.bean:\n"
                )
                print(ledger_draft)
                print()
            elif cc.ok:
                ui.console.print(
                    f"[ok]Ledger match[/] ({cc.date}): {inv.currency} {m.money(cc.ledger_total)}"
                )
                if not cc.date_ok:
                    ui.console.print(
                        f"[warn]booking date {cc.date} ≠ invoice date {inv.issue_date}[/]"
                    )
            else:
                ui.console.print(
                    f"[err]Ledger CONFLICT[/] ({cc.date}): {inv.number} is already booked "
                    f"at {m.money(cc.ledger_total)} but the invoice says "
                    f"{m.money(cc.invoice_total)} {inv.currency} — fix one side before issuing."
                )
    elif verify and not as_json:
        ui.console.print("[warn]ledger not found — cross-check skipped.[/]")

    if as_json:
        _json_out(
            {
                "number": inv.number,
                "kind": inv.kind,
                "currency": inv.currency,
                "issue_date": inv.issue_date,
                "customer": inv.customer.name,
                "pdf": str(path),
                "totals": totals.model_dump(),
                "qr_payload_ok": qr_ok,
                "cross_check": dataclasses.asdict(cc) if cc else None,
                "ledger_draft": ledger_draft,
            }
        )


@app.command()
def schema(
    out: Path = typer.Option(
        Path("invoicing/schema"), "--out", "-o", help="Directory for the generated JSON Schemas."
    ),
):
    """Write JSON Schemas for the invoice, issuer, and customers files.

    Point an editor at them (yaml-language-server modeline) for completion
    and validation; any future UI can consume the same contract."""
    import json as _json

    from .invoice import model as m

    out.mkdir(parents=True, exist_ok=True)
    for name, mdl in [
        ("invoice", m.Invoice),
        ("issuer", m.Issuer),
        ("customers", m.CustomerRegistry),
    ]:
        path = out / f"{name}.schema.json"
        path.write_text(_json.dumps(mdl.model_json_schema(), indent=2) + "\n")
        ui.console.print(f"[ok]Wrote[/] {path}")


@app.command()
def init(
    directory: Path = typer.Argument(
        Path("."), help="Target project directory (created if missing)."
    ),
    name: str | None = typer.Option(None, "--name", help="Entity name, e.g. 'Acme GmbH'."),
    legal_form: str | None = typer.Option(
        None,
        "--legal-form",
        help="Legal form: gmbh, ag, or einzelfirma (sole proprietorship / freelancer).",
    ),
    lang: str | None = typer.Option(None, "--lang", "-l", help="Report language: en or de."),
    importers: str | None = typer.Option(
        None, "--importers", help="Comma-separated: ubs, wise, stripe (default: none)."
    ),
    samples: bool = typer.Option(
        False, "--samples", help="Include a demo quarter of transactions."
    ),
    answers_file: Path | None = typer.Option(
        None, "--answers", help="TOML answer-file for non-interactive scaffolding."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip prompts; accept defaults."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
):
    """Scaffold a new quints project (chart of accounts, per-year books,
    quints.toml, pyproject.toml, AGENTS.md).

    Deterministic: the same answers always produce the same files. Run
    interactively, or feed a TOML answer-file with --answers for CI/repeatable
    setups."""
    if answers_file is not None:
        if not answers_file.exists():
            typer.secho(f"ERROR: answer-file not found: {answers_file}", fg="red", err=True)
            raise typer.Exit(1)
        answers = init_mod.load_answers(answers_file)
    else:
        answers = init_mod.Answers()

    interactive = answers_file is None and not yes
    if legal_form is not None:
        answers = replace(answers, legal_form=legal_form.strip().lower())
    elif interactive:
        answers = replace(
            answers,
            legal_form=typer.prompt("Legal form (gmbh/ag/einzelfirma)", default=answers.legal_form)
            .strip()
            .lower(),
        )
    # An answer-file's entity name is authoritative; otherwise suggest one
    # that matches the chosen legal form instead of the GmbH default.
    example_names = {"gmbh": "Example GmbH", "ag": "Example AG", "einzelfirma": "Jane Doe"}
    if answers_file is None and answers.entity_name == init_mod.Answers.entity_name:
        answers = replace(
            answers, entity_name=example_names.get(answers.legal_form, answers.entity_name)
        )
    if name is not None:
        answers = replace(answers, entity_name=name)
    elif interactive:
        answers = replace(
            answers, entity_name=typer.prompt("Entity name", default=answers.entity_name)
        )
    if lang is not None:
        answers = replace(answers, report_language=lang)
    elif interactive:
        answers = replace(
            answers,
            report_language=typer.prompt(
                "Report language (en/de)", default=answers.report_language
            ),
        )
    if importers is not None:
        answers = replace(
            answers, importers=tuple(i.strip() for i in importers.split(",") if i.strip())
        )
    if samples:
        answers = replace(answers, include_samples=True)

    try:
        files = init_mod.plan(answers)
    except init_mod.InitError as e:
        typer.secho(f"ERROR: {e}", fg="red", err=True)
        raise typer.Exit(1) from None
    result = init_mod.write(directory, files, force=force)

    if as_json:
        _json_out(
            {
                "directory": str(directory),
                "entity": answers.entity_name,
                "written": [str(p) for p in result.written],
                "skipped": [str(p) for p in result.skipped],
            }
        )
        return
    for path in result.written:
        ui.console.print(f"[ok]created[/] {path}")
    for path in result.skipped:
        ui.console.print(f"[warn]exists, skipped[/] {path} (use --force to overwrite)")
    if result.written and not result.skipped:
        ui.console.print(
            f"\nScaffolded [b]{answers.entity_name}[/] in {directory}. "
            "Next: [b]uv sync[/], then [b]quints check[/] and [b]quints mwst -q 2026-Q3[/]."
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
