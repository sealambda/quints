"""Statutory statements as one PDF artifact (Typst) — plan 3 step 4.

Bilanz + Erfolgsrechnung in the requested language, issuer identity from
``invoicing/issuer.yaml``, with the prior year's figures (Art. 958d Abs. 2 OR)
and lines for the signatures (Art. 958 Abs. 3 OR). The notes (Anhang, Art.
959c OR) are not part of it, and the PDF says so.

All structure lives here as flat display lines (indent/code/label/amount/
emphasis); the Typst template just lays them out. Same pipeline as the
invoice: context → ``data.json`` → ``typst.compile`` (PDF/A-2b).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import typst
import yaml

from . import ui
from .kmu import BilanzReport, ErfolgReport, RowLine, kmu_name, label

TEMPLATE = Path(__file__).parent / "report.typ"
DEFAULT_ISSUER = Path("invoicing/issuer.yaml")


class Line(TypedDict):
    """One flat display line of a statement (the Typst template's unit)."""

    code: str
    label: str
    amount: str
    indent: int
    bold: bool
    rule: bool
    prior: str  # the prior year's amount; "" without a comparative column


def _money(value: Decimal, lang: str) -> str:
    """Swiss apostrophe thousands for German (1'234.56), anglo otherwise."""
    text = ui.money(value)
    return text.replace(",", "'") if lang == "de" else text


def _line(
    label_: str,
    amount: str = "",
    *,
    code: str = "",
    indent: int = 0,
    bold: bool = False,
    rule: bool = False,
) -> Line:
    return {
        "code": code,
        "label": label_,
        "amount": amount,
        "indent": indent,
        "bold": bold,
        "rule": rule,
        "prior": "",
    }


def with_prior(current: list[Line], prior: list[Line]) -> list[Line]:
    """`current` with each line's prior-year amount filled in. A line only the
    prior year has (an account since closed) is kept where the statement's
    order puts it: a row before the next line both years share, a code under
    the row above it."""
    out = [line.copy() for line in current]
    match: list[int | None] = []
    at = -1
    for p in prior:
        key = (p["code"], p["label"])
        i = next(
            (j for j in range(at + 1, len(out)) if (out[j]["code"], out[j]["label"]) == key), None
        )
        match.append(i)
        if i is not None:
            out[i]["prior"] = p["amount"]
            at = i
    # Sort key per line: (current index it is anchored to, 0 before / 1 the
    # line itself / 2 after it, insertion order).
    keyed = [((j, 1, 0), line) for j, line in enumerate(out)]
    last = (-1, 2, 0)
    for n, (p, i) in enumerate(zip(prior, match, strict=True)):
        if i is not None:
            last = (i, 1, 0)
            continue
        if p["indent"] == 0:
            successor = next((m for m in match[n + 1 :] if m is not None), len(out))
            last = (successor, 0, n)
        else:
            last = (last[0], 2 if last[1] == 1 else last[1], n)
        only_prior = p.copy()
        only_prior["amount"], only_prior["prior"] = "", p["amount"]
        keyed.append((last, only_prior))
    return [line for _, line in sorted(keyed, key=lambda kl: kl[0])]


def _section(
    rows: list[RowLine], lang: str, *, sign: int = 1, form: str | None = None
) -> list[Line]:
    lines: list[Line] = []
    # A subtotal (`reserves`) lists its positions, each with its codes; the
    # positions are plain rows, so one level of flattening reaches every code.
    flat = [r for row in rows for r in (row, *row.parts)]
    for row in flat:
        lines.append(_line(label(row.key, lang, form), _money(sign * row.amount, lang)))
        if row.parts:
            continue
        for cl in row.codes:
            lines.append(
                _line(
                    kmu_name(cl.code, lang, form),
                    _money(sign * cl.amount, lang),
                    code=cl.code,
                    indent=1,
                )
            )
    return lines


def bilanz_lines(report: BilanzReport, lang: str) -> list[Line]:
    lines = [_line(label("assets", lang), bold=True)]
    for section in ("current_assets", "noncurrent_assets"):
        rows = getattr(report, section)
        if not rows:
            continue
        lines += _section(rows, lang, form=report.legal_form)
        lines.append(
            _line(
                label(section, lang),
                _money(sum((r.amount for r in rows), Decimal("0")), lang),
                bold=True,
            )
        )
    lines.append(
        _line(label("total_assets", lang), _money(report.total_assets, lang), bold=True, rule=True)
    )

    lines.append(_line(label("liabilities_equity", lang), bold=True))
    for section in ("short_term_liabilities", "long_term_liabilities", "equity"):
        rows = getattr(report, section)
        if not rows and section != "equity":
            continue
        lines += _section(rows, lang, form=report.legal_form)
        total = sum((r.amount for r in rows), Decimal("0"))
        if section == "equity":
            if report.retained_prior:
                lines.append(
                    _line(label("retained_prior", lang), _money(report.retained_prior, lang))
                )
                total += report.retained_prior
            lines.append(_line(label("result", lang), _money(report.result, lang)))
            total += report.result
        lines.append(_line(label(section, lang), _money(total, lang), bold=True))
    lines.append(
        _line(
            label("total_liabilities_equity", lang),
            _money(report.total_liabilities_equity, lang),
            bold=True,
            rule=True,
        )
    )
    return lines


def erfolg_lines(report: ErfolgReport, lang: str) -> list[Line]:
    lines = _section(report.revenue, lang)
    lines += _section(report.expenses, lang, sign=-1)
    lines.append(_line(label("ebit", lang), _money(report.ebit, lang), bold=True, rule=True))
    lines += _section(report.financial_expenses, lang, sign=-1)
    lines += _section(report.financial_income, lang)
    if report.other_results or report.direct_taxes:  # Art. 959b Abs. 2 Ziff. 8-10 OR
        lines.append(_line(label("ebt", lang), _money(report.ebt, lang), bold=True, rule=True))
        lines += _section(report.other_results, lang)
        lines.append(
            _line(
                label("result_before_taxes", lang),
                _money(report.result_before_taxes, lang),
                bold=True,
                rule=True,
            )
        )
        lines += _section(report.direct_taxes, lang, sign=-1)
    lines.append(_line(label("result", lang), _money(report.result, lang), bold=True, rule=True))
    return lines


def _load_issuer(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    return {
        "name": data.get("name", ""),
        "address": data.get("address", []),
        "vat_id": data.get("vat_id", ""),
        "accent": (data.get("brand") or {}).get("accent", "#123c3a"),
        "font": (data.get("brand") or {}).get("font", "Liberation Sans"),
    }


_SIGNATURES = {
    "en": {
        "place_date": "Place, date",
        "chair": "Chair of the management body",
        "accounting": "Responsible for financial reporting",
        "no_notes": "The notes to the accounts (Art. 959c CO) are not part of this document.",
    },
    "de": {
        "place_date": "Ort, Datum",
        "chair": "Vorsitz des obersten Leitungsorgans",
        "accounting": "Verantwortlich für die Rechnungslegung",
        "no_notes": "Der Anhang (Art. 959c OR) ist nicht Teil dieses Dokuments.",
    },
}

_PROBLEMS_TITLE = {
    "en": "Problems in the books — these statements may be incomplete",
    "de": "Prüfung — Probleme in der Buchhaltung, die Abschlüsse sind evtl. unvollständig",
}


def build_context(
    bilanz: BilanzReport,
    erfolg: ErfolgReport,
    lang: str,
    issuer_path: Path = DEFAULT_ISSUER,
    prior: tuple[BilanzReport, ErfolgReport] | None = None,
) -> dict[str, object]:
    """`prior` adds the comparative column; leave it out for a first year."""
    fx_note = ""
    if bilanz.converted:
        parts = ", ".join(f"{_money(v, lang)} {c}" for c, v in sorted(bilanz.converted.items()))
        fx_note = {
            "en": f"Non-CHF balances valued at the {bilanz.at} rate: {parts}.",
            "de": f"Fremdwährungsbestände zum Kurs per {bilanz.at} bewertet: {parts}.",
        }.get(lang, "")
    if bilanz.unrealized_fx:
        fx_note += f" {label('unrealized_fx', lang)}: {_money(bilanz.unrealized_fx, lang)}."
    # Both statements load the same books: list each problem once.
    problems: list[dict[str, str]] = []
    for v in bilanz.violations + erfolg.violations:
        item = {"date": v.date, "message": v.message}
        if item not in problems:
            problems.append(item)
    b_lines, e_lines = bilanz_lines(bilanz, lang), erfolg_lines(erfolg, lang)
    columns: list[str] = []
    if prior:
        b_lines = with_prior(b_lines, bilanz_lines(prior[0], lang))
        e_lines = with_prior(e_lines, erfolg_lines(prior[1], lang))
        columns = [bilanz.at[:4], prior[0].at[:4]]
    return {
        "lang": lang,
        "columns": columns,
        "signatures": _SIGNATURES.get(lang, _SIGNATURES["en"]),
        "issuer": _load_issuer(issuer_path),
        "violations": problems,
        "violations_title": _PROBLEMS_TITLE.get(lang, _PROBLEMS_TITLE["en"]),
        "bilanz": {
            "title": label("bilanz_title", lang),
            "subtitle": f"{label('as_at', lang)} {bilanz.at} · OR Art. 959a · CHF",
            "lines": b_lines,
            "note": fx_note,
        },
        "erfolg": {
            "title": label("erfolg_title", lang),
            "subtitle": f"{erfolg.date_from} – {erfolg.date_to} · OR Art. 959b · CHF",
            "lines": e_lines,
        },
    }


def render_pdf(
    bilanz: BilanzReport,
    erfolg: ErfolgReport,
    lang: str,
    out_path: Path,
    issuer_path: Path = DEFAULT_ISSUER,
    prior: tuple[BilanzReport, ErfolgReport] | None = None,
) -> Path:
    ctx = build_context(bilanz, erfolg, lang, issuer_path, prior)
    work = Path(tempfile.mkdtemp(prefix="quints-report-"))
    try:
        (work / "data.json").write_text(json.dumps(ctx, ensure_ascii=False, indent=2))
        shutil.copy(TEMPLATE, work / "report.typ")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Archival PDF/A-2b, never a silent plain-PDF fallback: the books' PDFs
        # must stay readable for the ten years they are kept (Art. 958f OR).
        typst.compile(
            str(work / "report.typ"), output=str(out_path), root=str(work), pdf_standards="a-2b"
        )
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)
