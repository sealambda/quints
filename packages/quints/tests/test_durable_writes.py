"""Writes that can't lose data: atomic, never clobbering, never guessing.

Each case used to lose something without a word: a second same-day import
overwrote the first one's drafts (staging/ is git-ignored), a price-file
rewrite dropped lines it didn't parse, a truncated PDF counted as filed,
`quints init` committed whatever else was in the directory.
"""

import json
import os
import subprocess
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from beanprice_bazg.bazg import SourcePrice
from quints import config, importing, init, ledger, prices
from quints.cli import app

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"

# ── atomic_write ───────────────────────────────────────────────────────────────


def test_atomic_write_replaces_whole_or_not_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "prices.bean"
    ledger.atomic_write(target, "old\n")
    assert target.read_text() == "old\n"

    def boom(_fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="disk full"):
        ledger.atomic_write(target, "new\n")
    assert target.read_text() == "old\n"  # untouched
    assert [p.name for p in tmp_path.iterdir()] == ["prices.bean"]  # no temp left


def test_atomic_write_can_refuse_to_clobber(tmp_path: Path) -> None:
    target = tmp_path / "draft.bean"
    ledger.atomic_write(target, b"first", overwrite=False)
    with pytest.raises(FileExistsError):
        ledger.atomic_write(target, b"second", overwrite=False)
    assert target.read_bytes() == b"first"
    assert [p.name for p in tmp_path.iterdir()] == ["draft.bean"]


# ── staging never overwrites ───────────────────────────────────────────────────

_UBS = config.Config(import_ubs=config.UbsImport(iban="CH9300762011623852957"))
_LEDGER = "2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF\n"


def test_second_same_day_import_keeps_the_first_drafts(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text(_LEDGER)
    staging = tmp_path / "staging"
    first = importing.run_ubs(FIXTURES / "transactions.mt940", led, staging, _UBS)
    second = importing.run_ubs(FIXTURES / "transactions.mt940", led, staging, _UBS)
    assert first.out_path and second.out_path and first.out_path != second.out_path
    assert first.out_path.read_text() == second.out_path.read_text()  # nothing lost
    assert first.already_staged == []
    assert len(second.already_staged) == len(second.drafts)  # each ref is staged already


def test_cli_import_warns_when_drafts_are_already_staged(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text(_LEDGER)
    toml = tmp_path / "quints.toml"
    toml.write_text('[import.ubs]\niban = "CH9300762011623852957"\n')
    argv = ["--config", str(toml), "import", "ubs", str(FIXTURES / "transactions.mt940")]
    argv += ["--out", str(tmp_path / "staging"), "-f", str(led)]
    assert runner.invoke(app, argv).exit_code == 0
    res = runner.invoke(app, argv)
    assert res.exit_code == 0, res.output
    assert "already in staging" in res.output
    assert len(list((tmp_path / "staging").glob("*.bean"))) == 2


# ── the price file keeps what it can't parse, or refuses ───────────────────────


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


class Weekdays:
    """Series source: weekdays up to ``newest`` only (later days not out yet)."""

    def __init__(self, newest: date) -> None:
        self.newest = newest

    def get_prices_series(self, ccy: str, begin: datetime, end: datetime) -> list[SourcePrice]:
        out: list[SourcePrice] = []
        d = begin.date()
        while d <= min(end.date(), self.newest):
            if d.isoweekday() <= 5:
                out.append(SourcePrice(Decimal("0.9"), _utc(d), "CHF"))
            d += timedelta(days=1)
        return out


def _sync(out: Path, today: date, source: object, **kw: object) -> prices.SyncResult:
    return prices.sync(
        out,
        today=today,
        currencies=("EUR",),
        backfill_start=today - timedelta(days=10),
        source=source,  # type: ignore[arg-type] -- a test double
        **kw,  # type: ignore[arg-type] -- test passthrough
    )


def test_unusual_but_valid_price_lines_survive_a_rewrite(tmp_path: Path) -> None:
    today = date(2026, 7, 10)
    out = tmp_path / "prices.bean"
    out.write_text(
        "; header\n\n"
        "2026-07-01 price VBTLX.US  123.45 USD\n"
        "2026-07-01 price EUR 0.95 CHF  ; manual override, bank rate\n"
    )
    _sync(out, today, Weekdays(today))
    text = out.read_text()
    assert "2026-07-01 price VBTLX.US 123.45 USD" in text
    assert "2026-07-01 price EUR 0.95 CHF  ; manual override, bank rate" in text


def test_a_line_the_price_file_cannot_hold_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    original = "2026-07-01 price EUR 0.95 CHF\n2026-07-02 commodity EUR\n"
    out.write_text(original)
    with pytest.raises(ValueError, match=r"prices\.bean:2"):
        _sync(out, date(2026, 7, 10), Weekdays(date(2026, 7, 10)))
    assert out.read_text() == original


def test_days_after_the_newest_rate_are_not_verified(tmp_path: Path) -> None:
    """The source has nothing newer than 3 days ago (not published yet): those
    days and today must be fetched again next time, not marked checked."""
    today = date(2026, 7, 10)  # a Friday
    out = tmp_path / "prices.bean"
    _sync(out, today, Weekdays(today - timedelta(days=3)))
    (verified,) = [ln for ln in out.read_text().splitlines() if "verified" in ln]
    assert verified.endswith(f"..{today - timedelta(days=3)}")


def test_a_mistyped_old_date_does_not_backfill_a_century(tmp_path: Path) -> None:
    """Books are kept ten years (Art. 958f OR): a 1924 typo can't make the sync
    fetch a hundred years; an explicit --from that old is refused."""
    today = date(2026, 7, 10)
    out = tmp_path / "prices.bean"
    out.write_text("1924-07-01 price EUR 0.95 CHF\n")
    calls: list[tuple[date, date]] = []

    class Recording(Weekdays):
        def get_prices_series(self, ccy: str, begin: datetime, end: datetime) -> list[SourcePrice]:
            calls.append((begin.date(), end.date()))
            return super().get_prices_series(ccy, begin, end)

    _sync(out, today, Recording(today))
    assert min(lo for lo, _ in calls) >= date(2016, 1, 1)
    with pytest.raises(ValueError, match="958f"):
        _sync(out, today, Recording(today), repair_from=date(1924, 1, 1))


# ── Stripe invoice PDFs: complete or not filed ─────────────────────────────────


def test_pdf_completeness() -> None:
    assert importing.is_complete_pdf(b"%PDF-1.7\n...\n%%EOF\n")
    assert not importing.is_complete_pdf(b"%PDF-1.7\n... cut off")
    assert not importing.is_complete_pdf(b"<html>error</html>")


def test_truncated_inbox_pdf_is_not_filed(tmp_path: Path) -> None:
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "a.pdf").write_bytes(b"%PDF-1.7 cut off")
    assert importing.already_filed(tmp_path, "a.pdf") is None
    (tmp_path / "inbox" / "a.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")
    assert importing.already_filed(tmp_path, "a.pdf") == tmp_path / "inbox" / "a.pdf"


# ── init: commit only the scaffold, bounded, honest ────────────────────────────


def _git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "quints test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.invalid")


def test_init_commits_only_the_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git_env(monkeypatch)
    proj = tmp_path / "books"
    proj.mkdir()
    (proj / "salaries.xlsx").write_bytes(b"private, not the scaffold's")
    res = runner.invoke(app, ["init", str(proj), "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["git"]["committed"] is True
    tracked = subprocess.run(  # noqa: S603 — fixed git argv in a test
        ["git", "-C", str(proj), "ls-files"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "salaries.xlsx" not in tracked and "main.bean" in tracked


def _fake_run(fail: str | None = None, hang: str | None = None) -> Callable[..., object]:
    real = subprocess.run

    def run(argv: list[str], *args: object, **kwargs: object) -> object:
        verb = next((a for a in argv[1:] if not a.startswith("-") and "/" not in a), "")
        assert kwargs.get("timeout"), f"git {verb} without a timeout"
        if verb == hang:
            raise subprocess.TimeoutExpired(argv, 1)
        if verb == fail:
            return subprocess.CompletedProcess(argv, 128, "", f"fatal: {verb} broke")
        return real(argv, *args, **kwargs)  # type: ignore[call-overload] -- passthrough

    return run


def test_failed_git_init_is_not_reported_initialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(fail="init"))
    result = init.init_git(tmp_path, [])
    assert (result.initialized, result.committed) == (False, False)
    assert "init broke" in result.detail


def test_hanging_git_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git_env(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _fake_run(hang="commit"))
    (tmp_path / "main.bean").write_text("")
    result = init.init_git(tmp_path, [tmp_path / "main.bean"])
    assert (result.initialized, result.committed) == (True, False)
    assert "timed out" in result.detail


def test_cli_prices_sync_refuses_a_file_it_would_damage(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    original = "2026-07-01 price EUR 0.95 CHF\n2026-07-02 commodity EUR\n"
    out.write_text(original)
    argv = ["prices", "sync", "--source", "beanprice_bazg", "--out", str(out)]
    res = runner.invoke(app, argv)
    assert res.exit_code == 1 and "prices.bean:2" in res.output
    assert out.read_text() == original
