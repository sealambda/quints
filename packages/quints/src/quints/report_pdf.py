"""Statutory statements as one PDF artifact (Typst) — plan 3 step 4.

Bilanz + Erfolgsrechnung in the requested language, issuer identity from
``invoicing/issuer.yaml`` — the single document the Treuhänder/auditor files.

All structure lives here as flat display lines (indent/code/label/amount/
emphasis); the Typst template just lays them out. Same pipeline as the
invoice: context → ``data.json`` → ``typst.compile`` (PDF/A-2b when
supported).
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
    }


def _section(
    rows: list[RowLine], lang: str, *, sign: int = 1, form: str | None = None
) -> list[Line]:
    lines: list[Line] = []
    for row in rows:
        lines.append(_line(label(row.key, lang, form), _money(sign * row.amount, lang)))
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


def build_context(
    bilanz: BilanzReport, erfolg: ErfolgReport, lang: str, issuer_path: Path = DEFAULT_ISSUER
) -> dict[str, object]:
    fx_note = ""
    if bilanz.converted:
        parts = ", ".join(f"{_money(v, lang)} {c}" for c, v in sorted(bilanz.converted.items()))
        fx_note = {
            "en": f"Non-CHF balances valued at the {bilanz.at} rate: {parts}.",
            "de": f"Fremdwährungsbestände zum Kurs per {bilanz.at} bewertet: {parts}.",
        }.get(lang, "")
    return {
        "lang": lang,
        "issuer": _load_issuer(issuer_path),
        "bilanz": {
            "title": label("bilanz_title", lang),
            "subtitle": f"{label('as_at', lang)} {bilanz.at} · OR Art. 959a · CHF",
            "lines": bilanz_lines(bilanz, lang),
            "note": fx_note,
        },
        "erfolg": {
            "title": label("erfolg_title", lang),
            "subtitle": f"{erfolg.date_from} – {erfolg.date_to} · OR Art. 959b · CHF",
            "lines": erfolg_lines(erfolg, lang),
        },
    }


def render_pdf(
    bilanz: BilanzReport,
    erfolg: ErfolgReport,
    lang: str,
    out_path: Path,
    issuer_path: Path = DEFAULT_ISSUER,
) -> Path:
    ctx = build_context(bilanz, erfolg, lang, issuer_path)
    work = Path(tempfile.mkdtemp(prefix="quints-report-"))
    try:
        (work / "data.json").write_text(json.dumps(ctx, ensure_ascii=False, indent=2))
        shutil.copy(TEMPLATE, work / "report.typ")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            typst.compile(
                str(work / "report.typ"), output=str(out_path), root=str(work), pdf_standards="a-2b"
            )
        except (TypeError, ValueError):
            typst.compile(str(work / "report.typ"), output=str(out_path), root=str(work))
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)
