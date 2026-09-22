"""Tests for the MWST report computation (Form 310 Ziffern).

The Ziffern, labels and arithmetic are pinned to ESTV form MWST-4470
("Abrechnung nach der effektiven Methode", gültig ab 01.01.2024).
"""

from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from quints import mwst

_LEDGER = """
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
2024-01-01 open Income:CH:GmbH:Consulting:External:Export
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Assets:CH:GmbH:Tax:InputVAT CHF
2024-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
2024-01-01 open Liabilities:CH:GmbH:Tax:Bezugsteuer CHF
2024-01-01 open Expenses:CH:GmbH:IT:Hosting
2024-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR

2026-07-01 price EUR 0.93 CHF

2026-07-02 * "Client" "Consulting"
  Assets:CH:GmbH:Receivable:Trade                1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF

2026-07-03 * "Abroad Client" "Export consulting"
  Assets:CH:GmbH:Receivable:Trade                 200.00 EUR
  Income:CH:GmbH:Consulting:External:Export      -200.00 EUR

2026-07-04 * "Foreign SaaS" "reverse charge"
  Expenses:CH:GmbH:IT:Hosting                     100.00 EUR
  Assets:CH:GmbH:Tax:InputVAT                       7.53 CHF @@ 8.10 EUR
  Liabilities:CH:GmbH:Tax:Bezugsteuer              -7.53 CHF @@ 8.10 EUR
  Assets:CH:GmbH:Current:Wise:EUR                -100.00 EUR
"""

# A chart with kmu: codes, so the Kontenrahmen-driven rules (235, 400/405,
# "income that is not Entgelt") are exercised the way a real project has them.
_CHART = """
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
  kmu: "1020"
2026-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR
  kmu: "1020"
2026-01-01 open Assets:CH:GmbH:Receivable:Trade
  kmu: "1100"
2026-01-01 open Assets:CH:GmbH:Tax:InputVAT CHF
  kmu: "1170"
2026-01-01 open Assets:CH:GmbH:Equipment:Office CHF
  kmu: "1510"
2026-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
  kmu: "2200"
2026-01-01 open Liabilities:CH:GmbH:Tax:Bezugsteuer CHF
  kmu: "2200"
2026-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF
  kmu: "2200"
2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
  kmu: "3400"
2026-01-01 open Income:CH:GmbH:Consulting:External:Export EUR
  kmu: "3400"
2026-01-01 open Income:CH:GmbH:Trade:ExportGoods CHF
  kmu: "3200"
2026-01-01 open Income:CH:GmbH:Books:Reduced CHF
  kmu: "3200"
2026-01-01 open Income:CH:GmbH:Rooms:Lodging CHF
  kmu: "3400"
2026-01-01 open Income:CH:GmbH:Training:Exempt CHF
  kmu: "3400"
2026-01-01 open Income:CH:GmbH:Erloesminderungen CHF
  kmu: "3800"
2026-01-01 open Income:CH:GmbH:FX:CurrencyGain CHF
  kmu: "6950"
2026-01-01 open Expenses:CH:GmbH:Material CHF
  kmu: "4400"
2026-01-01 open Expenses:CH:GmbH:IT:Hosting
  kmu: "6570"

2026-07-01 price EUR 0.93 CHF
"""

# One quarter touching every section-I line, both rate vintages, 400 vs 405,
# and the Nicht-Entgelte of section III.
_WIDE = (
    _CHART
    + """
2026-07-02 * "Acme AG" "Consulting"
  Assets:CH:GmbH:Receivable:Trade                1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF

2026-07-05 * "Buchhandlung" "Books — reduced rate"
  Assets:CH:GmbH:Current:UBS:CHF                  513.00 CHF
  Income:CH:GmbH:Books:Reduced                   -500.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -13.00 CHF

2026-07-08 * "Gast" "Übernachtung"
  Assets:CH:GmbH:Current:UBS:CHF                  207.60 CHF
  Income:CH:GmbH:Rooms:Lodging                   -200.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                -7.60 CHF

2026-07-10 * "Globex Ltd" "Export consulting"
  Assets:CH:GmbH:Receivable:Trade                 200.00 EUR
  Income:CH:GmbH:Consulting:External:Export      -200.00 EUR

2026-07-12 * "Overseas Ltd" "Machine shipped abroad"
  Assets:CH:GmbH:Current:UBS:CHF                  300.00 CHF
  Income:CH:GmbH:Trade:ExportGoods               -300.00 CHF

2026-07-15 * "Klinik" "Ausgenommene Leistung (Art. 21)"
  Assets:CH:GmbH:Current:UBS:CHF                  400.00 CHF
  Income:CH:GmbH:Training:Exempt                 -400.00 CHF

2026-07-20 * "Acme AG" "Skonto"
  Income:CH:GmbH:Erloesminderungen                 20.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                 1.62 CHF
  Assets:CH:GmbH:Receivable:Trade                 -21.62 CHF

2026-08-12 * "Foreign SaaS" "Cloud hosting (reverse charge)"
  Expenses:CH:GmbH:IT:Hosting                     100.00 EUR
  Assets:CH:GmbH:Tax:InputVAT                       7.53 CHF @@ 8.10 EUR
  Liabilities:CH:GmbH:Tax:Bezugsteuer              -7.53 CHF @@ 8.10 EUR
  Assets:CH:GmbH:Current:Wise:EUR                -100.00 EUR

2026-08-15 * "Lieferant" "Material"
  Expenses:CH:GmbH:Material                       200.00 CHF
  Assets:CH:GmbH:Tax:InputVAT                      16.20 CHF
  Assets:CH:GmbH:Current:UBS:CHF                 -216.20 CHF

2026-08-20 * "Möbel AG" "Desk"
  Assets:CH:GmbH:Equipment:Office                1000.00 CHF
  Assets:CH:GmbH:Tax:InputVAT                      81.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF                -1081.00 CHF

2026-09-01 * "Bank" "Realised FX gain"
  Assets:CH:GmbH:Current:UBS:CHF                   10.00 CHF
  Income:CH:GmbH:FX:CurrencyGain                  -10.00 CHF

2026-09-05 * "Kanton" "Subvention"
  mwst: "subvention"
  Assets:CH:GmbH:Current:UBS:CHF                 5000.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -5000.00 CHF

2026-09-10 * "Altkunde" "2023 supply, invoiced now"
  mwst: "old_rate"
  Assets:CH:GmbH:Receivable:Trade                1077.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -77.00 CHF
"""
)


def _compute(tmp_path: Path, text: str = _LEDGER) -> mwst.MwstReport:
    led = tmp_path / "m.bean"
    led.write_text(text)
    return mwst.compute(led, "2026-07-01", "2026-09-30")


def _rows(report: mwst.MwstReport) -> dict[str, tuple[Decimal, Decimal]]:
    return {r.ziffer: (r.net, r.tax) for r in report.rate_rows}


# ── the narrow fixture: what the report did before, still does ────────────────


def test_bezugsteuer_files_under_383_from_2024(tmp_path: Path) -> None:
    # 382 is the *bis 31.12.2023* column of Form 310; a 2026 reverse charge
    # belongs in 383. (quints filed it under 382 until this was fixed.)
    r = _compute(tmp_path)
    assert r.z383_tax == Decimal("7.53")
    assert r.z383_net == Decimal("92.96")  # 7.53 / 0.081, rappen-rounded
    assert r.z382_tax == r.z382_net == Decimal("0")
    assert r.bezugsteuer_tax == Decimal("7.53")
    assert len(r.bezugsteuer_lines) == 1
    line = r.bezugsteuer_lines[0]
    assert (line.original, line.currency, line.ziffer) == (Decimal("8.10"), "EUR", "383")


def test_totals_include_bezugsteuer(tmp_path: Path) -> None:
    r = _compute(tmp_path)
    assert r.z303_tax == Decimal("81.00")
    assert r.z399 == Decimal("88.53")  # 81.00 output + 7.53 Bezugsteuer
    # The Bezugsteuer deduction's counter-leg is IT hosting (no kmu code in
    # this fixture) → Ziffer 405, "übriger Betriebsaufwand".
    assert (r.z400, r.z405) == (Decimal("0"), Decimal("7.53"))
    assert r.z479 == Decimal("7.53")  # the deduction side
    assert r.z500 == Decimal("81.00")  # Bezugsteuer is cash-neutral
    assert r.z510 == Decimal("0")
    # Bezugsteuer purchases are not turnover: 200 = domestic + export only.
    assert r.z299 == Decimal("1000.00")
    assert r.z200 == Decimal("1000.00") + r.z221
    assert r.violations == []


def test_settlement_debit_is_not_an_accrual(tmp_path: Path) -> None:
    r = _compute(
        tmp_path,
        _LEDGER
        + """
2026-09-30 * "2026-Q3 VAT Settlement" ^VAT-2026-Q3
  Liabilities:CH:GmbH:Tax:PayableVAT              -81.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                81.00 CHF
  Liabilities:CH:GmbH:Tax:Bezugsteuer               7.53 CHF
  Assets:CH:GmbH:Tax:InputVAT                      -7.53 CHF

2024-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF
""",
    )
    assert r.z383_tax == Decimal("7.53")  # unchanged by the settlement debit
    assert r.z399 == Decimal("88.53")
    assert r.z303_tax == Decimal("81.00")


# ── section I: every deduction line ───────────────────────────────────────────


def test_section_one_deductions(tmp_path: Path) -> None:
    r = _compute(tmp_path, _WIDE)
    assert r.z220 == Decimal("300.00")  # export of goods (Art. 23)
    assert r.z221 == Decimal("186.00")  # 200 EUR @ 0.93 — place of supply abroad
    assert r.z230 == Decimal("400.00")  # ausgenommen (Art. 21), no option
    assert r.z235 == Decimal("20.00")  # Skonto, from the kmu 3800 account
    assert r.z225 == r.z280 == r.z205 == Decimal("0")
    # 200 carries the gross turnover; the Skonto is deducted via 235, and the
    # subvention and the FX gain are not Entgelt at all.
    assert r.z200 == Decimal("3586.00")
    assert r.z289 == Decimal("906.00")
    assert r.z299 == Decimal("2680.00")


def test_ziffer_299_equals_the_rate_rows(tmp_path: Path) -> None:
    r = _compute(tmp_path, _WIDE)
    assert sum((row.net for row in r.rate_rows), Decimal("0")) == r.z299
    assert r.violations == []


def test_rate_rows_per_class_and_vintage(tmp_path: Path) -> None:
    r = _compute(tmp_path, _WIDE)
    rows = _rows(r)
    # The Skonto reverses its sale: 1000 − 20 net, 81.00 − 1.62 tax.
    assert rows["303"] == (Decimal("980.00"), Decimal("79.38"))
    assert rows["313"] == (Decimal("500.00"), Decimal("13.00"))  # 2.6 %
    assert rows["343"] == (Decimal("200.00"), Decimal("7.60"))  # 3.8 %
    # A 2023 supply invoiced in 2026 keeps the old rate and the old Ziffer.
    assert rows["302"] == (Decimal("1000.00"), Decimal("77.00"))  # 7.7 %
    assert "312" not in rows and "342" not in rows  # unused old-rate rows hidden
    assert r.z303_net == Decimal("980.00") and r.z302_tax == Decimal("77.00")
    assert r.z313_tax == Decimal("13.00") and r.z343_tax == Decimal("7.60")
    assert r.z399 == Decimal("184.51")


def test_input_vat_split_400_vs_405(tmp_path: Path) -> None:
    r = _compute(tmp_path, _WIDE)
    # kmu 4400 (Aufwand für bezogene Dienstleistungen) → 400; the desk (1510)
    # and the hosting (6570) are Investitionen / übriger Betriebsaufwand → 405.
    assert r.z400 == Decimal("16.20")
    assert r.z405 == Decimal("88.53")  # 81.00 desk + 7.53 Bezugsteuer deduction
    assert r.z479 == Decimal("104.73")
    assert {line.ziffer for line in r.vat_lines} == {"400", "405"}
    assert r.z500 == Decimal("79.78")  # 184.51 − 104.73


def test_flows_outside_ziffer_200(tmp_path: Path) -> None:
    r = _compute(tmp_path, _WIDE)
    assert r.z900 == Decimal("5000.00")  # tagged mwst: "subvention"
    assert r.z910 == Decimal("0")
    # The realised FX gain is booked to income but is not Entgelt: kmu 6950 is
    # outside Kontenklasse 3, so it never reaches Ziffer 200.
    assert Decimal("10.00") not in [line.chf for line in r.domestic]


# ── metadata overrides ────────────────────────────────────────────────────────

_OVERRIDE = (
    _CHART
    + """
2026-07-02 * "Mixed" "Reduced rate on a standard account"
  mwst: "reduced"
  Assets:CH:GmbH:Current:UBS:CHF                  102.60 CHF
  Income:CH:GmbH:Consulting:External:Domestic    -100.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                -2.60 CHF

2026-07-03 * "Optiert" "Art. 22 option"
  Assets:CH:GmbH:Current:UBS:CHF                  216.20 CHF
  Income:CH:GmbH:Training:Exempt                 -200.00 CHF
    mwst: "optioned"
  Liabilities:CH:GmbH:Tax:OutputVAT               -16.20 CHF

2026-07-04 * "Kunde" "Diverses"
  mwst: "diverses"
  Assets:CH:GmbH:Current:UBS:CHF                   50.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic     -50.00 CHF
"""
)


def test_metadata_overrides_the_account(tmp_path: Path) -> None:
    r = _compute(tmp_path, _OVERRIDE)
    rows = _rows(r)
    # mwst: "reduced" on a plain consulting account moves it to Ziffer 313.
    assert rows["313"] == (Decimal("100.00"), Decimal("2.60"))
    # An opted supply (Art. 22) is taxable and only *memoed* in 205 — it is
    # not deducted in 230, so it stays in the standard rate row.
    assert r.z205 == Decimal("200.00")
    assert r.z230 == Decimal("0")
    assert rows["303"] == (Decimal("200.00"), Decimal("16.20"))
    assert r.z280 == Decimal("50.00")
    assert r.z299 == Decimal("300.00")
    assert r.violations == []


def test_unknown_token_is_reported_not_ignored(tmp_path: Path) -> None:
    r = _compute(
        tmp_path,
        _CHART
        + """
2026-07-02 * "Kunde" "Typo in the tag"
  mwst: "exempted"
  Assets:CH:GmbH:Current:UBS:CHF                  108.10 CHF
  Income:CH:GmbH:Consulting:External:Domestic    -100.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                -8.10 CHF
""",
    )
    # The typo does not silently become "exempt": the booking stays taxable
    # and the token is reported.
    (v,) = r.violations
    assert v.message.startswith('unknown mwst: token "exempted"')
    assert r.z299 == Decimal("100.00") and r.z230 == Decimal("0")


# ── the rate-consistency check ────────────────────────────────────────────────


def test_wrong_output_vat_is_listed(tmp_path: Path) -> None:
    r = _compute(
        tmp_path,
        _CHART
        + """
2026-07-02 * "Acme AG" "VAT booked at the old rate by mistake"
  Assets:CH:GmbH:Receivable:Trade                1077.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -77.00 CHF
""",
    )
    (v,) = r.violations
    assert v.expected == Decimal("81.00") and v.posted == Decimal("77.00")
    assert "8.1 %" in v.message
    # The booking is still reported — under the rate row it claims to be in.
    assert r.z303_tax == Decimal("77.00")


def test_rappen_rounding_is_not_a_violation(tmp_path: Path) -> None:
    r = _compute(
        tmp_path,
        _CHART
        + """
2026-07-02 * "Acme AG" "Per-line rounding"
  Assets:CH:GmbH:Receivable:Trade                 108.11 CHF
  Income:CH:GmbH:Consulting:External:Domestic    -100.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                -8.11 CHF
""",
    )
    assert r.violations == []  # 8.10 expected, 8.11 posted — within tolerance


# ── the credit period (Ziffer 510) ────────────────────────────────────────────


def test_credit_period_reports_510(tmp_path: Path) -> None:
    r = _compute(
        tmp_path,
        _CHART
        + """
2026-07-02 * "Acme AG" "Small sale"
  Assets:CH:GmbH:Receivable:Trade                 108.10 CHF
  Income:CH:GmbH:Consulting:External:Domestic    -100.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                -8.10 CHF

2026-08-20 * "Möbel AG" "Desk"
  Assets:CH:GmbH:Equipment:Office                1000.00 CHF
  Assets:CH:GmbH:Tax:InputVAT                      81.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF                -1081.00 CHF
""",
    )
    assert r.z399 == Decimal("8.10") and r.z479 == Decimal("81.00")
    assert r.z500 == Decimal("-72.90")  # the signed ledger figure
    assert r.z510 == Decimal("72.90")  # what the form's Ziffer 510 carries


# ── the input-VAT corrections (410 / 415 / 420) ───────────────────────────────


def test_input_vat_corrections(tmp_path: Path) -> None:
    r = _compute(
        tmp_path,
        _CHART
        + """
2026-07-02 * "Einlage" "Einlageentsteuerung (Art. 32)"
  mwst: "einlageentsteuerung"
  Assets:CH:GmbH:Tax:InputVAT                     100.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic    -100.00 CHF
    mwst: "diverses"

2026-07-03 * "Eigenverbrauch" "Vorsteuerkorrektur (Art. 31)"
  Assets:CH:GmbH:Tax:InputVAT                     -30.00 CHF
    mwst: "vorsteuerkorrektur"
  Expenses:CH:GmbH:Material                        30.00 CHF

2026-07-04 * "Subvention" "Vorsteuerkürzung (Art. 33 Abs. 2)"
  Assets:CH:GmbH:Tax:InputVAT                     -20.00 CHF
    mwst: "vorsteuerkuerzung"
  Expenses:CH:GmbH:Material                        20.00 CHF
""",
    )
    assert (r.z410, r.z415, r.z420) == (Decimal("100.00"), Decimal("30.00"), Decimal("20.00"))
    assert r.z479 == Decimal("50.00")  # 0 + 0 + 100 − 30 − 20
    assert r.z500 == Decimal("-50.00") and r.z510 == Decimal("50.00")


# ── what counts as a settlement ───────────────────────────────────────────────


def test_only_payable_vat_marks_a_settlement(tmp_path: Path) -> None:
    # A sale is a sale even if its link happens to start with "VAT-"; only a
    # PayableVAT posting means "this is the quarterly flush".
    r = _compute(
        tmp_path,
        _CHART
        + """
2026-07-02 * "VAT-Services AG" "Consulting" ^VAT-INVOICE-9
  Assets:CH:GmbH:Current:UBS:CHF                  108.10 CHF
  Income:CH:GmbH:Consulting:External:Domestic    -100.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                -8.10 CHF
""",
    )
    assert r.z299 == Decimal("100.00") and r.z303_tax == Decimal("8.10")
    assert r.violations == []


# ── `vat convert --bezugsteuer` round-trips into the report ───────────────────


def test_bezugsteuer_block_round_trips_at_the_reduced_rate(tmp_path: Path) -> None:
    """What `quints vat convert` prints must report at the rate it used."""
    from beancount.core import prices as bc_prices

    from quints import ledger as ledger_mod
    from quints import vat as vat_mod

    led = tmp_path / "m.bean"
    led.write_text(_CHART)
    entries, _errors = ledger_mod.load_entries(led)
    posting = vat_mod.convert(
        Decimal("100"),
        "EUR",
        Date(2026, 7, 2),
        bc_prices.build_price_map(entries),
        net=True,
        rate_class="reduced",
    )
    block = '2026-07-02 * "Foreign SaaS" "Reduced-rate service"\n'
    block += posting.render_bezugsteuer() + "\n"
    block += "    Assets:CH:GmbH:Current:Wise:EUR              -100.00 EUR\n"
    block += "    Expenses:CH:GmbH:IT:Hosting                   100.00 EUR\n"
    led.write_text(_CHART + "\n" + block)
    _entries, errors = ledger_mod.load_entries(led)
    assert not errors, errors

    r = mwst.compute(led, "2026-07-01", "2026-09-30")
    assert r.z383_tax == Decimal("2.42")  # 2.6 % of 100 EUR at 0.93
    assert r.z383_net == Decimal("93.08")  # 2.42 / 0.026 — not the 8.1 % net
    assert r.z405 == Decimal("2.42")  # counter-leg kmu 6570 → übriger Betriebsaufwand


# ── quarter parsing ───────────────────────────────────────────────────────────


def test_quarter_range() -> None:
    assert mwst.quarter_range("2026-Q2") == ("2026-04-01", "2026-06-30")
    assert mwst.quarter_range("2026q4") == ("2026-10-01", "2026-12-31")
