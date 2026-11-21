"""Everything that would make a statement or a VAT return wrong: the gate.

`quints check` and the `ledger` item of `quints close check` render this. It
runs the loader, the issued-invoice checks, the statutory statements and the
open VAT returns over one load. quints has no fiscal year, so each check is
period-free or scoped by the data:

- codes in no statutory row, and postings no rate can value: all history;
- the statements' identity: one window, 1 January to the latest transaction;
- VAT: every period not settled yet. A filed period is corrected only by a
  counter-entry in a later one, so its old problems would fail every run.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from beancount.core import convert as bc_convert
from beancount.core import data
from beancount.core import prices as bc_prices

from . import config, diagnostics, kmu, ledger, mwst
from .invoice import verify


def audit(
    ledger_path: Path, cfg: config.Config, *, loaded: ledger.Loaded | None = None
) -> list[ledger.Violation]:
    """Every problem the gate knows, located and deduplicated, loader errors first."""
    entries, problems = loaded or ledger.load(ledger_path)
    loaded = (entries, problems)
    price_map = bc_prices.build_price_map(entries)
    unvalued = _unvalued(entries, cfg.entity_marker, price_map)
    found = [
        *problems,
        *kmu.unmapped_accounts(entries, cfg.entity_marker),
        *unvalued,
        *verify.audit(entries, ledger_path.parent, cfg),
    ]
    last = max((e.date for e in entries if isinstance(e, data.Transaction)), default=None)
    if last is None:
        return _dedupe(found)

    # The identity holds for any window, so one catches an account only one
    # statement sees. Its unmapped codes and dated rates are reported above
    # over all history; the cross-check and the report-date rates are new.
    reports = kmu.compute_bilanz(
        ledger_path, str(last), cfg, date_from=f"{last.year}-01-01", loaded=loaded
    ).violations
    for link in mwst.unsettled_periods(entries, cfg, last):
        d0, d1 = mwst.period_range(link.removeprefix("VAT-"))
        reports += mwst.compute(ledger_path, d0, d1, cfg, loaded=loaded).violations
    places = {(v.file, v.line) for v in unvalued}
    found += [
        v
        for v in reports
        if v.code != "kmu-unmapped"
        and not (v.code == "rate-missing" and v.file and (v.file, v.line) in places)
    ]
    return _dedupe(found)


def _unvalued(
    entries: data.Directives, marker: str, price_map: bc_prices.PriceMap
) -> list[ledger.Violation]:
    """Entity postings no rate values on their own date. Other entities are not ours."""
    out: list[ledger.Violation] = []
    for e in entries:
        if not isinstance(e, data.Transaction):
            continue
        for p in e.postings:
            if marker not in p.account or p.units is None:
                continue
            try:
                ledger.to_chf(bc_convert.get_weight(p), e.date, price_map)
            except ledger.RateUnavailable as exc:
                out.append(
                    ledger.violation_at(
                        e,
                        f"{exc} — {p.account} {p.units} has no CHF value; run `quints prices sync`",
                        code="rate-missing",
                    )
                )
    return out


def _dedupe(found: list[ledger.Violation]) -> list[ledger.Violation]:
    """Each report lists the loader's errors again: keep the first of each."""
    seen: set[tuple[str, str | None, int | None, str]] = set()
    out: list[ledger.Violation] = []
    for v in found:
        key = (v.code, v.file, v.line, v.message)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def new_since(
    found: list[ledger.Violation], base: list[ledger.Violation]
) -> list[ledger.Violation]:
    """``found`` without what ``base`` already had: the problems a change brought.

    Matched by content, not by file or line, so an entry that moved is not new.

    shortcut: a message that quotes an absolute path (a missing include)
    differs between the two checkouts and counts as new.
    """
    had = Counter((v.code, v.date, v.payee, v.narration, v.message) for v in base)
    out: list[ledger.Violation] = []
    for v in found:
        key = (v.code, v.date, v.payee, v.narration, v.message)
        if had[key] > 0:
            had[key] -= 1
        else:
            out.append(v)
    return out


def apply_fixes(ledger_path: Path, found: list[ledger.Violation]) -> int:
    """Insert each ``fix`` below its line; returns how many were applied."""
    by_file: dict[str, dict[int, str]] = {}
    for v in found:
        if v.fix and v.file and v.line:
            by_file.setdefault(v.file, {})[v.line] = v.fix
    for file, fixes in by_file.items():
        path = diagnostics.absolute(file, ledger_path.parent)
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for line in sorted(fixes, reverse=True):  # bottom-up: earlier lines keep their number
            lines.insert(line, fixes[line] + "\n")
        ledger.atomic_write(path, "".join(lines))
    return sum(len(fixes) for fixes in by_file.values())
