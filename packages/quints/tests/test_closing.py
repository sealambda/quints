"""Tests for the year-end close: the readiness checklist and depreciation.

`_CLEAN` is a fiscal year 2026 that is genuinely finished — VAT settled and
paid, the FX revaluation and the depreciation booked, bank balances asserted
on 1 January, documents linked. Every checklist test breaks exactly one of
those and asserts the item that notices.
"""

from __future__ import annotations

import json
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from quints import closing, config
from quints.cli import app

runner = CliRunner()

_CHART = """
plugin "quints.plugins.kmu" ":CH:GmbH:"

2025-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
  kmu: "1020"
2025-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR
  kmu: "1020"
2025-01-01 open Assets:CH:GmbH:Receivable:Trade
  kmu: "1100"
2025-01-01 open Assets:CH:GmbH:FixedAssets:Equipment CHF
  kmu: "1520"
  depreciation: "declining"
  depreciation_rate: "40"
  depreciation_category: "it-equipment"
2025-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
  kmu: "2200"
2025-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF
  kmu: "2200"
2025-01-01 open Equity:CH:GmbH:Capital:Share CHF
  kmu: "2800"
2025-01-01 open Income:CH:GmbH:Consulting:External:Domestic
  kmu: "3400"
2025-01-01 open Expenses:CH:GmbH:Depreciation CHF
  kmu: "6800"
2025-01-01 open Expenses:CH:GmbH:FX:CurrencyLoss CHF
  kmu: "6900"
2025-01-01 open Income:CH:GmbH:FX:CurrencyGain CHF
  kmu: "6950"
2025-01-01 open Income:CH:GmbH:Rounding CHF
  kmu: "6950"

2026-06-01 price EUR 0.95 CHF
2026-12-31 price EUR 0.90 CHF

2026-01-05 * "Founder" "Share capital"
  Assets:CH:GmbH:Current:UBS:CHF          10000.00 CHF
  Equity:CH:GmbH:Capital:Share

2026-02-01 * "Dealer" "Laptop"
  document: "2026-02-01.dealer.laptop.pdf"
  Assets:CH:GmbH:FixedAssets:Equipment     5000.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF

2026-03-10 * "Acme AG" "Consulting"
  document: "2026-03-10.acme.invoice.pdf"
  Assets:CH:GmbH:Receivable:Trade          1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic  -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT          -81.00 CHF

2026-04-15 * "Acme AG" "Payment"
  Assets:CH:GmbH:Current:UBS:CHF           1081.00 CHF
  Assets:CH:GmbH:Receivable:Trade

2026-06-01 * "Globex Ltd" "Export consulting"
  document: "2026-06-01.globex.invoice.pdf"
  Assets:CH:GmbH:Current:Wise:EUR            200.00 EUR @ 0.95 CHF
  Income:CH:GmbH:Consulting:External:Domestic  -190.00 CHF
"""

_SETTLEMENT = """
2026-12-31 * "2026-Q4 VAT Settlement" ^VAT-2026-Q4
  due: 2027-03-01
  Liabilities:CH:GmbH:Tax:PayableVAT         -81.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT           81.00 CHF
"""

_PAYMENT = """
2026-12-31 * "ESTV" "VAT paid" ^VAT-2026-Q4
  Liabilities:CH:GmbH:Tax:PayableVAT          81.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF             -81.00 CHF
"""

_FX = """
2026-12-31 * "Wise" "Year-end FX revaluation EUR (Art. 960 OR)"
  Assets:CH:GmbH:Current:Wise:EUR           -200.00 EUR @@ 190.00 CHF
  Assets:CH:GmbH:Current:Wise:EUR            200.00 EUR @@ 180.00 CHF
  Expenses:CH:GmbH:FX:CurrencyLoss            10.00 CHF
"""

_DEPRECIATION = """
2026-12-31 * "Depreciation 2026 (Art. 960a OR)"
  depreciation_year: "2026"
  Expenses:CH:GmbH:Depreciation             2000.00 CHF
  Assets:CH:GmbH:FixedAssets:Equipment     -2000.00 CHF
"""

_ASSERTIONS = """
2027-01-01 balance Assets:CH:GmbH:Current:UBS:CHF     6000.00 CHF
2027-01-01 balance Assets:CH:GmbH:Current:Wise:EUR     200.00 EUR
"""

_CLEAN = _CHART + _SETTLEMENT + _PAYMENT + _FX + _DEPRECIATION + _ASSERTIONS

# VAT from Q4 2026 only, so one settlement covers the whole liable year.
_CFG = config.Config(vat_registered_since=Date(2026, 10, 1))


def _write(tmp_path: Path, text: str) -> Path:
    main = tmp_path / "main.bean"
    main.write_text(text)
    return main


def _check(tmp_path: Path, text: str = _CLEAN, cfg: config.Config | None = None) -> dict[str, str]:
    checklist = closing.check(_write(tmp_path, text), 2026, cfg or _CFG)
    return {item.id: item.status for item in checklist.items}


def _item(tmp_path: Path, text: str, item_id: str, cfg: config.Config | None = None):
    checklist = closing.check(_write(tmp_path, text), 2026, cfg or _CFG)
    return next(i for i in checklist.items if i.id == item_id)


# ── the checklist ────────────────────────────────────────────────────────────


def test_a_finished_year_passes_every_item(tmp_path: Path) -> None:
    checklist = closing.check(_write(tmp_path, _CLEAN), 2026, _CFG)
    failing = [(i.id, i.status, i.detail) for i in checklist.items if i.status != closing.PASS]
    assert not failing, failing
    assert checklist.ok and checklist.failed == 0 and checklist.warned == 0


def test_loader_errors_fail_the_ledger_item(tmp_path: Path) -> None:
    # A :CH:GmbH: account without a kmu: code — what `quints check` rejects.
    broken = _CLEAN + "\n2026-01-01 open Expenses:CH:GmbH:Unmapped CHF\n"
    item = _item(tmp_path, broken, "ledger")
    assert item.status == closing.FAIL
    assert item.data["errors"] == 1


def test_unsettled_vat_periods_fail(tmp_path: Path) -> None:
    # Registered for the whole year: Q1..Q3 were never settled.
    item = _item(tmp_path, _CLEAN, "vat", config.Config(vat_registered_since=Date(2026, 1, 1)))
    assert item.status == closing.FAIL
    assert item.data["unsettled"] == ["VAT-2026-Q1", "VAT-2026-Q2", "VAT-2026-Q3"]
    assert "quints vat settle -p 2026-Q1" in item.detail


def test_filed_but_unpaid_vat_only_warns(tmp_path: Path) -> None:
    filed = _CHART + _SETTLEMENT + _FX + _DEPRECIATION
    item = _item(tmp_path, filed, "vat")
    assert item.status == closing.WARN
    unpaid = item.data["unpaid"]
    assert isinstance(unpaid, list) and unpaid[0]["period"] == "VAT-2026-Q4"
    assert unpaid[0]["owed"] == Decimal("81.00")


def test_not_vat_registered_passes(tmp_path: Path) -> None:
    item = _item(tmp_path, _CLEAN, "vat", config.Config(vat_registered=False))
    assert item.status == closing.PASS and "Not VAT-registered" in item.detail


def test_registered_without_a_start_date_owes_every_quarter(tmp_path: Path) -> None:
    # No vat_registered_since: liable for as long as the books go back.
    item = _item(tmp_path, _CLEAN, "vat", config.Config())
    assert item.data["periods"] == [f"VAT-2026-Q{q}" for q in range(1, 5)]


def test_the_period_grid_follows_the_filing_period(tmp_path: Path) -> None:
    saldo = (config.SaldoRate(Decimal("0.062")),)
    half = config.Config(vat_method="saldo", saldo=saldo)
    assert closing.vat_periods(2026, half) == ["VAT-2026-H1", "VAT-2026-H2"]
    annual = config.Config(vat_period="year")
    assert closing.vat_periods(2026, annual) == ["VAT-2026"]
    # Registered in May: the first half-year is a (short) period too.
    late = config.replace(half, vat_registered_since=Date(2026, 5, 4))
    assert closing.vat_periods(2026, late) == ["VAT-2026-H1", "VAT-2026-H2"]
    autumn = config.replace(half, vat_registered_since=Date(2026, 9, 1))
    assert closing.vat_periods(2026, autumn) == ["VAT-2026-H2"]


def test_a_switch_changes_the_grid_from_its_year(tmp_path: Path) -> None:
    cfg = config.Config(
        vat_registered_since=Date(2023, 1, 1),
        vat_changes=(
            config.VatChange(
                Date(2026, 1, 1), method="saldo", saldo=(config.SaldoRate(Decimal("0.062")),)
            ),
        ),
    )
    assert closing.vat_periods(2025, cfg) == [f"VAT-2025-Q{q}" for q in range(1, 5)]
    assert closing.vat_periods(2026, cfg) == ["VAT-2026-H1", "VAT-2026-H2"]


def test_deregistration_ends_the_grid(tmp_path: Path) -> None:
    cfg = config.Config(vat_registered_until=Date(2026, 6, 30))
    assert closing.vat_periods(2026, cfg) == ["VAT-2026-Q1", "VAT-2026-Q2"]
    assert closing.vat_periods(2027, cfg) == []


def test_flagged_transactions_fail(tmp_path: Path) -> None:
    draft = _CLEAN + (
        '\n2026-08-01 ! "Someone" "Payment order"\n'
        "  Assets:CH:GmbH:Current:UBS:CHF          -250.00 CHF\n"
        "  Expenses:CH:GmbH:Depreciation\n"
    )
    item = _item(tmp_path, draft, "flagged")
    assert item.status == closing.FAIL and item.data["count"] == 1


def test_staging_and_inbox_warn_with_counts(tmp_path: Path) -> None:
    (tmp_path / "staging").mkdir()
    (tmp_path / "staging" / "ubs-2026.bean").write_text("; draft\n")
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "2026-11-02.acme.invoice.pdf").write_text("pdf")
    statuses = _check(tmp_path)
    assert statuses["staging"] == closing.WARN
    assert statuses["inbox"] == closing.WARN
    item = _item(tmp_path, _CLEAN, "inbox")
    assert item.data["count"] == 1 and item.data["unbooked"] == 1


def test_missing_document_link_warns_with_examples(tmp_path: Path) -> None:
    undocumented = _CLEAN.replace('  document: "2026-03-10.acme.invoice.pdf"\n', "")
    item = _item(tmp_path, undocumented, "documents")
    assert item.status == closing.WARN
    assert item.data["count"] == 1 and item.data["of"] == 2
    examples = item.data["examples"]
    assert isinstance(examples, list) and "Acme AG" in examples[0]


def test_generated_entries_need_no_document(tmp_path: Path) -> None:
    # The VAT settlement (^VAT-… link), the FX revaluation (only FX legs) and
    # the depreciation entry (its year marker) are quints' own output.
    item = _item(tmp_path, _CLEAN, "documents")
    assert item.status == closing.PASS and "All 2 " in item.detail


def test_balance_assertion_must_be_dated_after_the_year_end(tmp_path: Path) -> None:
    # Beancount asserts at the *start* of the date, so 12-31 does not cover
    # the year's last day — only 01-01 of the next year does.
    on_year_end = _CLEAN.replace("2027-01-01 balance", "2026-12-31 balance")
    item = _item(tmp_path, on_year_end, "assertions")
    assert item.status == closing.FAIL
    assert item.data["missing"] == [
        "Assets:CH:GmbH:Current:UBS:CHF",
        "Assets:CH:GmbH:Current:Wise:EUR",
    ]
    assert item.data["on_or_after"] == "2027-01-01"


def test_assertions_ignore_an_importer_account_the_ledger_never_opened(
    tmp_path: Path,
) -> None:
    # A [import.*] section can name an account that is not in the chart (yet);
    # demanding a balance assertion on one would never be satisfiable.
    cfg = config.Config(
        vat_registered_since=Date(2026, 10, 1),
        import_ubs=config.UbsImport(account="Assets:CH:GmbH:Current:Neon:CHF"),
    )
    item = _item(tmp_path, _CLEAN, "assertions", cfg)
    assert item.status == closing.PASS
    assert item.data["accounts"] == [
        "Assets:CH:GmbH:Current:UBS:CHF",
        "Assets:CH:GmbH:Current:Wise:EUR",
    ]


def test_recent_open_receivable_passes(tmp_path: Path) -> None:
    # Open at the year end but younger than receivable_review_days: no verdict
    # to make, so no warning.
    fresh = _CLEAN + (
        '\n2026-11-20 * "Acme AG" "Consulting" ^INV2026120\n'
        '  document: "2026-11-20.acme.invoice.pdf"\n'
        "  Assets:CH:GmbH:Receivable:Trade          500.00 CHF\n"
        "  Income:CH:GmbH:Consulting:External:Domestic\n"
    )
    item = _item(tmp_path, fresh, "receivables")
    assert item.status == closing.PASS
    assert item.data["open"] == 1 and item.data["aged"] == []
    assert "none older than 90 days" in item.detail


def test_unbooked_fx_revaluation_fails(tmp_path: Path) -> None:
    item = _item(tmp_path, _CLEAN.replace(_FX, ""), "fx")
    assert item.status == closing.FAIL
    assert item.data["total"] == Decimal("-10.00")
    assert "quints fx revalue --at 2026-12-31" in item.detail


def test_missing_rate_warns_instead_of_raising(tmp_path: Path) -> None:
    # fx.compute raises RateUnavailable — a checklist never traces back.
    no_rates = _CLEAN.replace("2026-06-01 price EUR 0.95 CHF\n", "").replace(
        "2026-12-31 price EUR 0.90 CHF\n", ""
    )
    checklist = closing.check(_write(tmp_path, no_rates), 2026, _CFG)
    fx_item = next(i for i in checklist.items if i.id == "fx")
    assert fx_item.status == closing.WARN and fx_item.data["currency"] == "EUR"
    prices_item = next(i for i in checklist.items if i.id == "prices")
    assert prices_item.status == closing.FAIL and prices_item.data["missing"] == ["EUR"]


def test_stale_year_end_rate_warns(tmp_path: Path) -> None:
    stale = _CLEAN.replace("2026-12-31 price EUR 0.90 CHF", "2026-11-30 price EUR 0.90 CHF")
    item = _item(tmp_path, stale, "prices")
    assert item.status == closing.WARN
    assert item.data["stale"] == [{"currency": "EUR", "latest": "2026-11-30"}]


def test_aged_receivable_warns(tmp_path: Path) -> None:
    open_invoice = _CLEAN + (
        '\n2026-05-01 * "Slow Payer" "Consulting" ^INV2026099\n'
        '  document: "2026-05-01.slow.invoice.pdf"\n'
        "  Assets:CH:GmbH:Receivable:Trade          500.00 CHF\n"
        "  Income:CH:GmbH:Consulting:External:Domestic\n"
    )
    item = _item(tmp_path, open_invoice, "receivables")
    assert item.status == closing.WARN
    aged = item.data["aged"]
    assert isinstance(aged, list) and aged[0]["number"] == "INV2026099"
    assert aged[0]["age_days"] == 244


def test_unbooked_depreciation_fails(tmp_path: Path) -> None:
    item = _item(tmp_path, _CLEAN.replace(_DEPRECIATION, ""), "depreciation")
    assert item.status == closing.FAIL
    assert "quints close depreciation --year 2026" in item.detail


# ── depreciation ─────────────────────────────────────────────────────────────


def _plan(tmp_path: Path, text: str, year: int = 2026, cfg: config.Config | None = None):
    return closing.compute_depreciation(_write(tmp_path, text), year, cfg or _CFG)


def test_declining_balance_on_the_years_purchase(tmp_path: Path) -> None:
    plan = _plan(tmp_path, _CLEAN.replace(_DEPRECIATION, ""))
    (asset,) = plan.assets
    assert asset.method == "declining" and asset.rate == Decimal("40")
    assert asset.cost == Decimal("5000.00")
    assert asset.base == Decimal("5000.00")  # `full` year on the acquisition
    assert asset.expected == Decimal("2000.00")
    assert asset.booked == Decimal("0")
    assert asset.delta == Decimal("2000.00")
    assert asset.book_after == Decimal("3000.00")
    assert asset.acquired == Date(2026, 2, 1)


def test_booked_depreciation_nets_to_zero(tmp_path: Path) -> None:
    plan = _plan(tmp_path, _CLEAN)
    (asset,) = plan.assets
    assert asset.expected == Decimal("2000.00") and asset.booked == Decimal("2000.00")
    assert asset.delta == Decimal("0") and plan.total == Decimal("0")
    assert closing.depreciation_text(plan) == ""


def test_declining_balance_continues_on_the_next_year(tmp_path: Path) -> None:
    plan = _plan(tmp_path, _CLEAN, year=2027)
    (asset,) = plan.assets
    assert asset.book_start == Decimal("3000.00")  # 5000 − 2000 booked in 2026
    assert asset.expected == Decimal("1200.00")  # 40% of 3000


def test_linear_over_a_useful_life(tmp_path: Path) -> None:
    linear = _CLEAN.replace(_DEPRECIATION, "").replace(
        '  depreciation: "declining"\n  depreciation_rate: "40"\n',
        '  depreciation: "linear"\n  useful_life_years: "3"\n',
    )
    (asset,) = _plan(tmp_path, linear).assets
    assert asset.method == "linear" and asset.useful_life_years == 3
    assert asset.expected == Decimal("1666.67")  # 5000 / 3, to the Rappen


def test_linear_rate_runs_off_cost_not_book_value(tmp_path: Path) -> None:
    linear = _CLEAN.replace(
        '  depreciation: "declining"\n  depreciation_rate: "40"\n',
        '  depreciation: "linear"\n  depreciation_rate: "20"\n',
    )
    (asset,) = _plan(tmp_path, linear, year=2027).assets
    assert asset.book_start == Decimal("3000.00")
    assert asset.expected == Decimal("1000.00")  # 20% of the 5000 cost


def test_residual_floors_the_write_down(tmp_path: Path) -> None:
    # 40% of a 1.20 book value would write it below the pro-memoria franc.
    tiny = (
        _CLEAN.replace(_DEPRECIATION, "")
        .replace(_ASSERTIONS, "")
        .replace('  depreciation_category: "it-equipment"\n', '  residual: "1"\n')
        .replace("5000.00 CHF", "1.20 CHF")
    )
    (asset,) = _plan(tmp_path, tiny).assets
    assert asset.book_before == Decimal("1.20")
    assert asset.expected == Decimal("0.20")  # not 0.48 — the residual wins
    assert asset.book_after == Decimal("1.00")


def test_a_rate_above_the_merkblatt_maximum_warns(tmp_path: Path) -> None:
    # Geschäftsmobiliar tops out at 25% of book value (Merkblatt A/1995).
    steep = _CLEAN.replace(_DEPRECIATION, "").replace(
        '  depreciation_category: "it-equipment"\n', '  depreciation_category: "furniture"\n'
    )
    plan = _plan(tmp_path, steep)
    (asset,) = plan.assets
    assert asset.max_rate == Decimal("25") and asset.exceeds_max
    assert any("exceeds the 25% Normalsatz" in w for w in plan.warnings)
    assert any("Merkblatt A/1995" in w for w in plan.warnings)


def test_linear_halves_the_merkblatt_maximum(tmp_path: Path) -> None:
    # Footnote 3: rates on the Anschaffungswert are halved — 40% → 20%.
    linear = _CLEAN.replace(_DEPRECIATION, "").replace(
        '  depreciation: "declining"\n  depreciation_rate: "40"\n',
        '  depreciation: "linear"\n  depreciation_rate: "25"\n',
    )
    plan = _plan(tmp_path, linear)
    (asset,) = plan.assets
    assert asset.max_rate == Decimal("20") and asset.exceeds_max
    assert plan.warnings and "20% Normalsatz" in plan.warnings[0]


def test_unknown_category_warns_without_a_ceiling(tmp_path: Path) -> None:
    odd = _CLEAN.replace(_DEPRECIATION, "").replace('"it-equipment"', '"spaceships"')
    plan = _plan(tmp_path, odd)
    assert plan.assets[0].max_rate is None
    assert any("unknown depreciation_category" in w for w in plan.warnings)


def test_months_prorata_in_the_year_of_acquisition(tmp_path: Path) -> None:
    # Bought 1 February: 11 of 12 months under `prorata = "months"`.
    cfg = config.Config(vat_registered_since=Date(2026, 10, 1), depreciation_prorata="months")
    (asset,) = _plan(tmp_path, _CLEAN.replace(_DEPRECIATION, ""), cfg=cfg).assets
    assert asset.base == Decimal("4583.33")  # 5000 × 11/12
    assert asset.expected == Decimal("1833.33")


def test_indirect_credits_the_wertberichtigung_account(tmp_path: Path) -> None:
    indirect = _CLEAN.replace(_DEPRECIATION, "").replace(
        '  depreciation_category: "it-equipment"\n',
        '  depreciation_contra: "Assets:CH:GmbH:FixedAssets:Valuation"\n',
    ) + ('\n2025-01-01 open Assets:CH:GmbH:FixedAssets:Valuation CHF\n  kmu: "1529"\n')
    plan = _plan(tmp_path, indirect)
    (asset,) = plan.assets
    assert asset.contra_account == "Assets:CH:GmbH:FixedAssets:Valuation"
    text = closing.depreciation_text(plan, _CFG)
    postings = [
        line
        for line in text.splitlines()
        if line.startswith("    ") and not line.strip().startswith(";")
    ]
    # The asset account keeps its cost; the charge lands on the contra account.
    assert any("FixedAssets:Valuation" in line for line in postings)
    assert not any("FixedAssets:Equipment" in line for line in postings)
    assert "2027-01-01 balance Assets:CH:GmbH:FixedAssets:Valuation" in text
    assert "-2000.00 CHF" in text.splitlines()[-1]  # accumulated, as a credit


def test_indirect_without_a_contra_account_warns(tmp_path: Path) -> None:
    cfg = config.Config(vat_registered_since=Date(2026, 10, 1), depreciation_method="indirect")
    plan = _plan(tmp_path, _CLEAN.replace(_DEPRECIATION, ""), cfg=cfg)
    assert any("depreciation_contra:" in w for w in plan.warnings)
    assert plan.assets[0].contra_account is None  # booked directly instead


def test_depreciation_text_is_paste_ready(tmp_path: Path) -> None:
    plan = _plan(tmp_path, _CLEAN.replace(_DEPRECIATION, ""))
    text = closing.depreciation_text(plan, _CFG)
    assert '2026-12-31 * "Depreciation 2026 (Art. 960a OR)"' in text
    assert 'depreciation_year: "2026"' in text
    assert "Expenses:CH:GmbH:Depreciation" in text
    assert "2000.00 CHF" in text and "-2000.00 CHF" in text
    # The assertion lands on 1 January, where beancount evaluates it.
    assert "2027-01-01 balance Assets:CH:GmbH:FixedAssets:Equipment" in text
    assert "3000.00 CHF" in text


def test_booking_the_printed_text_makes_the_year_pass(tmp_path: Path) -> None:
    # The loop the guide documents: print, paste, re-run, nothing left.
    without = _CLEAN.replace(_DEPRECIATION, "")
    plan = _plan(tmp_path, without)
    booked = without + "\n" + closing.depreciation_text(plan, _CFG) + "\n"
    assert closing.compute_depreciation(_write(tmp_path, booked), 2026, _CFG).total == Decimal("0")
    statuses = _check(tmp_path, booked)
    assert statuses["depreciation"] == closing.PASS
    assert statuses["ledger"] == closing.PASS  # the emitted assertion holds


def test_no_metadata_means_no_plan(tmp_path: Path) -> None:
    bare = _CLEAN.replace('  depreciation: "declining"\n', "").replace(
        '  depreciation_rate: "40"\n', ""
    )
    plan = _plan(tmp_path, bare)
    assert plan.assets == [] and plan.total == Decimal("0")
    assert closing.depreciation_text(plan, _CFG) == ""


def test_merkblatt_table_matches_the_estv_normalsaetze() -> None:
    # Spot-check the transcription against the printed Merkblatt A/1995.
    assert closing.MERKBLATT_A1995["it-equipment"][0] == Decimal("40")
    assert closing.MERKBLATT_A1995["furniture"][0] == Decimal("25")
    assert closing.MERKBLATT_A1995["vehicles"][0] == Decimal("40")
    assert closing.MERKBLATT_A1995["machines"][0] == Decimal("30")
    assert closing.MERKBLATT_A1995["tools"][0] == Decimal("45")
    assert closing.MERKBLATT_A1995["buildings-commercial"][0] == Decimal("4")
    assert closing.MERKBLATT_A1995["buildings-residential-with-land"][0] == Decimal("1.5")
    assert "estv.admin.ch" in closing.MERKBLATT_URL


# ── CLI ──────────────────────────────────────────────────────────────────────


def _toml(tmp_path: Path) -> list[str]:
    """`--config` for the CLI tests: registered from Q4, like `_CFG`."""
    path = tmp_path / "quints.toml"
    path.write_text("[entity]\nvat_registered_since = 2026-10-01\n")
    return ["--config", str(path)]


def test_cli_close_check_reports_and_gates(tmp_path: Path) -> None:
    main = _write(tmp_path, _CLEAN.replace(_FX, ""))
    cfg = _toml(tmp_path)
    result = runner.invoke(app, [*cfg, "close", "check", "--year", "2026", "-f", str(main)])
    assert result.exit_code == 0, result.output  # a checklist reports; it does not fail
    assert "Year-end close" in result.output and "FX revaluation booked" in result.output

    strict = runner.invoke(
        app, [*cfg, "close", "check", "--year", "2026", "--strict", "-f", str(main)]
    )
    assert strict.exit_code == 1

    clean = _write(tmp_path / "ok", _CLEAN) if (tmp_path / "ok").mkdir() is None else main
    strict_ok = runner.invoke(
        app, [*cfg, "close", "check", "--year", "2026", "--strict", "-f", str(clean)]
    )
    assert strict_ok.exit_code == 0, strict_ok.output


def test_cli_close_check_json(tmp_path: Path) -> None:
    main = _write(tmp_path, _CLEAN.replace(_DEPRECIATION, ""))
    result = runner.invoke(
        app, [*_toml(tmp_path), "close", "check", "--year", "2026", "--json", "-f", str(main)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["year"] == 2026 and payload["at"] == "2026-12-31"
    assert payload["ok"] is False and payload["failed"] == 1
    items = {i["id"]: i for i in payload["items"]}
    assert set(items) == {
        "ledger",
        "vat",
        "flagged",
        "staging",
        "inbox",
        "documents",
        "assertions",
        "fx",
        "prices",
        "depreciation",
        "receivables",
    }
    assert items["depreciation"]["status"] == "fail"
    assert items["depreciation"]["data"]["assets"][0]["delta"] == "2000.00"


def test_cli_close_depreciation_prints_the_entry(tmp_path: Path) -> None:
    main = _write(tmp_path, _CLEAN.replace(_DEPRECIATION, ""))
    result = runner.invoke(app, ["close", "depreciation", "--year", "2026", "-f", str(main)])
    assert result.exit_code == 0, result.output
    assert "Depreciation 2026 (Art. 960a OR)" in result.output
    assert "Merkblatt A/1995" in result.output


def test_cli_close_depreciation_json(tmp_path: Path) -> None:
    main = _write(tmp_path, _CLEAN.replace(_DEPRECIATION, ""))
    result = runner.invoke(
        app, ["close", "depreciation", "--year", "2026", "--json", "-f", str(main)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total"] == "2000.00"
    (asset,) = payload["assets"]
    assert asset["account"] == "Assets:CH:GmbH:FixedAssets:Equipment"
    assert asset["method"] == "declining" and asset["rate"] == "40"
    assert asset["expected"] == "2000.00" and asset["book_after"] == "3000.00"
    assert 'depreciation_year: "2026"' in payload["text"]
