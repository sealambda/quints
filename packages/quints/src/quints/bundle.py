"""Every statutory file for one fiscal year, in one call: `quints report bundle`.

Not a gate: `quints check` is. The bundle renders what the books say, and
the files list the problems the reports find, as each report does.
"""

from __future__ import annotations

import dataclasses
import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from . import config, kmu, ledger, mwst, report_pdf, ui

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


@dataclass
class Manifest:
    """What the bundle wrote, relative to its directory."""

    date_from: str
    date_to: str
    files: list[str] = field(default_factory=list)


def write(
    ledger_path: Path,
    date_from: str,
    date_to: str,
    langs: list[str],
    out: Path,
    cfg: config.Config | None = None,
    *,
    calendar_year: bool,
) -> Manifest:
    """The statements PDF and the Bilanz/Erfolg/Konten text per language, the
    Bilanz/Erfolg JSON, and the MWST JSON of every VAT period in the range.

    A calendar year gets the prior year's column and files named by the year
    (``bilanz-2026.json``); any other range is named ``<from>_<to>``.
    """
    cfg = cfg or config.get()
    loaded = ledger.load(ledger_path)
    stem = date_to[:4] if calendar_year else f"{date_from}_{date_to}"
    bilanz, erfolg, prior = report_pdf.statements(
        ledger_path, date_from, date_to, cfg, prior=calendar_year, loaded=loaded
    )
    konten = kmu.compute_konten(ledger_path, date_from, date_to, cfg, loaded=loaded)
    out.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(date_from, date_to)

    def put(name: str, content: str) -> None:
        ledger.atomic_write(out / name, content)
        manifest.files.append(name)

    for lang in langs:
        name = f"statements-{stem}.{lang}.pdf"
        report_pdf.render_pdf(bilanz, erfolg, lang, out / name, prior=prior)
        manifest.files.append(name)
        for kind, report, render in (
            ("bilanz", bilanz, kmu.render_bilanz),
            ("erfolg", erfolg, kmu.render_erfolg),
            ("konten", konten, kmu.render_konten),
        ):
            put(f"{kind}-{stem}.{lang}.txt", _text(render, report, lang))
    put(f"bilanz-{stem}.json", _json(bilanz))
    put(f"erfolg-{stem}.json", _json(erfolg))
    d0, d1 = Date.fromisoformat(date_from), Date.fromisoformat(date_to)
    for year in range(d0.year, d1.year + 1):
        for link in mwst.vat_periods(year, cfg):
            p0, p1 = mwst.period_range(link.removeprefix("VAT-"))
            if p0 <= date_to and p1 >= date_from:
                report = mwst.compute(ledger_path, p0, p1, cfg, loaded=loaded)
                put(f"mwst-{link.removeprefix('VAT-')}.json", _json(report))
    return manifest


def _text(render: Callable[..., object], report: DataclassInstance, lang: str) -> str:
    """A report as plain text, as wide as its tables need: no terminal, no colour."""
    buffer = io.StringIO()
    console = Console(file=buffer, width=120, no_color=True, theme=ui.THEME, highlight=False)
    render(report, lang=lang, console=console)
    return buffer.getvalue()


def _json(report: DataclassInstance) -> str:
    return json.dumps(dataclasses.asdict(report), indent=2, default=str) + "\n"
