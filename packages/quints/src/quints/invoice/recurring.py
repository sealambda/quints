"""The next invoice of a recurring one: a copy of its file with the next
number and a new supply period. Not a scheduler — the caller (a person, an
agent, cron) decides when."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .model import Invoice, load_mapping
from .verify import number_series


def _next_number(number: str, issued: int, on: date, taken: list[str]) -> str:
    """The number after the highest one `taken` in the series `number` is in.
    A series that carries the year (`INV2026014`) starts over in a new one."""
    key = number_series(number, issued)
    if key is None:
        raise ValueError(f"invoice number {number!r} does not end in a counter to continue")
    prefix = key[0]
    width = len(number) - len(prefix)
    prefix = re.sub(rf"{issued}(\D*)$", rf"{on.year}\1", prefix)
    counters = [
        int(n[len(prefix) :]) for n in taken if n.startswith(prefix) and n[len(prefix) :].isdigit()
    ]
    return f"{prefix}{max(counters, default=0) + 1:0{width}d}"


def _set(text: str, key: str, value: str) -> str:
    """Replace the top-level `key:` line; the rest of the file, comments
    included, stays as written."""
    new, count = re.subn(rf"^{key}:[^\n]*$", f"{key}: {value}", text, flags=re.M)
    if count != 1:
        raise ValueError(f"expected one top-level `{key}:` line to replace, found {count}")
    return new


def copy_invoice(source: Path, period: str, on: date) -> tuple[Path, Invoice]:
    """Write the next invoice after `source` for `period`, dated `on`, beside
    it. Returns the new file and the invoice it holds."""
    if source.suffix.lower() not in (".yaml", ".yml"):
        raise ValueError(f"{source} is not YAML — only a YAML invoice file can be copied")
    old = load_mapping(source)
    number, issued = str(old.get("number", "")), old.get("issue_date")
    if not isinstance(issued, date):
        raise ValueError(f"{source} has no `issue_date` to continue from")
    taken = [
        str(m["number"])
        for path in source.parent.glob("*.y*ml")
        if "number" in (m := load_mapping(path))
    ]
    new_number = _next_number(number, issued.year, on, taken)
    text = source.read_text()
    for key, value in [("number", new_number), ("issue_date", on.isoformat()), ("supply", period)]:
        text = _set(text, key, value)
    # A copy starts clean: it is neither a correction nor a credit note.
    text = re.sub(r"^(corrected|credit_note_for):[^\n]*\n", "", text, flags=re.M)

    was = str(old.get("supply"))
    stem = source.stem.replace(was, period) if was in source.stem else f"{source.stem}-{new_number}"
    dest = source.with_name(stem + source.suffix)
    if dest.exists():
        raise ValueError(f"{dest} already exists — nothing written")
    inv = Invoice.model_validate({**load_mapping_text(text), "number": new_number})
    dest.write_text(text)
    return dest, inv


def load_mapping_text(text: str) -> dict[str, object]:
    import yaml

    return yaml.safe_load(text) or {}
