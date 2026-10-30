"""Pro formas and the advances they collect, end to end.

A pro forma asks for a payment before the supply; the payment is a customer
advance, declared in the VAT period it arrives (Art. 40 Abs. 1 Bst. c MWSTG;
MWST-Info 15 Ziff. 1.3, Ziffern 200/221), and the invoice that follows credits
it — so that period declares only the remainder. The tests paste quints' own
drafts into a ledger, the way a user does, and read both returns back.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core import data, prices
from beancount.core.convert import get_weight
from test_invoice import ISSUER

from quints import config, fx, ledger, mwst
from quints.invoice import draft, qr, render, verify
from quints.invoice.model import Advance, Invoice, LineItem, Party, SupplyPeriod, compute

_CHART = """
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2026-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR
2026-01-01 open Assets:CH:GmbH:Receivable:Trade
2026-01-01 open Liabilities:CH:GmbH:Advances:Domestic
2026-01-01 open Liabilities:CH:GmbH:Advances:Export
2026-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic
2026-01-01 open Income:CH:GmbH:Consulting:External:Export
2026-01-01 open Income:CH:GmbH:Rounding
2026-11-01 price EUR 0.93 CHF
2027-01-01 price EUR 0.94 CHF
"""

_SUPPLY = SupplyPeriod(start=date(2027, 1, 1), end=date(2027, 3, 31))


def _item(description: str, price: int) -> LineItem:
    return LineItem(description=description, quantity=Decimal(1), unit_price=Decimal(price))


def _base(kind: str, currency: str, customer: Party, **overrides: object) -> Invoice:
    fields: dict[str, object] = {
        "number": "X1",
        "kind": kind,
        "currency": currency,
        "issue_date": date(2026, 11, 2),
        "supply": _SUPPLY,
        "customer": customer,
        "items": [_item("Project", 1)],
        **overrides,
    }
    return Invoice.model_validate(fields)


def _pair(base: Invoice, number: str, bank: str, tmp_path: Path) -> tuple[Invoice, Invoice, str]:
    """A 50 % pro forma paid in 2026, its 2027 invoice, and both bookings —
    the invoice drafted from the receipt in the books, the way the CLI does."""
    ccy = base.currency
    pf = base.model_copy(
        update={
            "number": "PF2026001",
            "proforma": True,
            "issue_date": date(2026, 11, 2),
            "supply": _SUPPLY,
            "items": [_item("Advance 50 %", 10000)],
        }
    )
    pf_totals = compute(pf)
    receipt = draft.build_draft(pf, pf_totals, config.Config()).replace(
        "2026-11-02 *", "2026-11-20 *"
    )
    paid = f"    {bank}  {pf_totals.amount_due:.2f} {ccy}"
    receipt = "\n".join(paid if "TODO the bank leg" in ln else ln for ln in receipt.splitlines())
    # A foreign VAT leg, converted at the day received as the guide says (0.93).
    receipt = re.sub(
        r"-(\S+) EUR  ; TODO in CHF.*",
        lambda m: f"-{Decimal(m[1]) * Decimal('0.93'):.2f} CHF @@ {m[1]} EUR",
        receipt,
    )

    final = base.model_copy(
        update={
            "number": number,
            "issue_date": date(2027, 1, 29),
            "supply": _SUPPLY,
            "items": [_item("Project", 20000)],
            "advances": [
                Advance(
                    proforma="PF2026001", received=date(2026, 11, 20), amount=pf_totals.amount_due
                )
            ],
        }
    )
    paid_only = _ledger(tmp_path, _CHART + "\n" + receipt + "\n")
    received = verify.cross_check(paid_only, final, compute(final)).received
    rate = None if ccy == "CHF" else Decimal("0.94")  # the invoice date's
    booked = draft.build_draft(final, compute(final), config.Config(), rate, received)
    return pf, final, _CHART + "\n" + receipt + "\n\n" + booked + "\n"


_ACME = Party(name="ACME AG", address=["Bahnhofstrasse 1", "8000 Zürich"])


def _domestic_pair(tmp_path: Path, currency: str = "CHF") -> tuple[Invoice, Invoice, str]:
    bank = f"Assets:CH:GmbH:Current:{'UBS:CHF' if currency == 'CHF' else 'Wise:EUR'}"
    return _pair(_base("domestic", currency, _ACME), "ACME202701", bank, tmp_path)


def _export_pair(tmp_path: Path, **overrides: object) -> tuple[Invoice, Invoice, str]:
    nordsoft = Party(
        name="nordsoft",
        address=["Tornimäe tn 1", "15551 Tallinn"],
        country="EE",
        vat_id="EE102566484",
    )
    base = _base("export", "EUR", nordsoft, **overrides)
    return _pair(base, "KEI202701", "Assets:CH:GmbH:Current:Wise:EUR", tmp_path)


def _ledger(tmp_path: Path, text: str) -> Path:
    led = tmp_path / "m.bean"
    led.write_text(text)
    return led


def test_domestic_advance_is_declared_on_receipt_and_only_the_rest_on_the_invoice(
    tmp_path: Path,
) -> None:
    _, _, text = _domestic_pair(tmp_path)
    led = _ledger(tmp_path, text)
    q4 = mwst.compute(led, "2026-10-01", "2026-12-31")
    q1 = mwst.compute(led, "2027-01-01", "2027-03-31")
    for r in (q4, q1):
        assert not r.violations, r.violations
        assert (r.z200, r.z303_net, r.z303_tax) == (Decimal("10000.00"), 10000, Decimal("810.00"))


@pytest.mark.parametrize("reverse_charge", [True, False])  # EU B2B, and outside the EU
def test_export_advance_is_declared_in_221_on_receipt(tmp_path: Path, reverse_charge: bool) -> None:
    _, _, text = _export_pair(tmp_path, reverse_charge=reverse_charge)
    led = _ledger(tmp_path, text)
    q4 = mwst.compute(led, "2026-10-01", "2026-12-31")
    q1 = mwst.compute(led, "2027-01-01", "2027-03-31")
    for r in (q4, q1):
        assert not r.violations, r.violations
        assert r.z299 == 0 and r.z399 == 0
    # Each period at its own rate: the advance at receipt, the rest when invoiced.
    assert (q4.z200, q4.z221) == (Decimal("9300.00"), Decimal("9300.00"))
    assert (q1.z200, q1.z221) == (Decimal("9400.00"), Decimal("9400.00"))


def test_final_invoice_credits_the_advance_and_asks_for_the_rest(tmp_path: Path) -> None:
    _, final, _ = _domestic_pair(tmp_path)
    t = compute(final)
    assert (t.grand_total, t.advances, t.amount_due) == (21620, 10810, Decimal("10810.00"))
    text = draft.build_draft(final, t, config.Config())  # no books: split as credited
    assert 'proforma: "PF2026001"' in text and "^PF2026001" not in text
    assert "Liabilities:CH:GmbH:Tax:OutputVAT                 -810.00 CHF" in text


def test_cross_check_reads_the_receipt_and_the_release(tmp_path: Path) -> None:
    pf, final, text = _domestic_pair(tmp_path)
    led = _ledger(tmp_path, text)
    paid = verify.cross_check(led, pf, compute(pf))
    assert paid.found and paid.ok and paid.date == "2026-11-20" and paid.date_ok
    booked = verify.cross_check(led, final, compute(final))
    assert booked.ok and not booked.advance_problems

    wrong = final.model_copy(
        update={
            "advances": [
                Advance(proforma="PF2026001", received=date(2026, 11, 20), amount=Decimal(9000))
            ]
        }
    )
    cc = verify.cross_check(led, wrong, compute(wrong))
    assert cc.advance_problems == [
        "advance PF2026001 is booked at 10810.00, the invoice credits 9000.00"
    ]


def test_unpaid_pro_forma_prints_the_receipt_legs(tmp_path: Path) -> None:
    pf, _, _ = _domestic_pair(tmp_path)
    assert not verify.cross_check(_ledger(tmp_path, _CHART), pf, compute(pf)).found
    text = draft.build_draft(pf, compute(pf), config.Config())
    assert "Liabilities:CH:GmbH:Advances:Domestic     -10000.00 CHF" in text
    assert 'proforma: "PF2026001"' in text and "TODO the bank leg: 10810.00 CHF" in text


def test_export_advance_is_not_revalued_at_year_end(tmp_path: Path) -> None:
    _, _, text = _export_pair(tmp_path)
    receipt_only = text.split("\n2027-01-29")[0] + "\n2026-12-31 price EUR 0.90 CHF\n"
    revalued = {r.account for r in fx.compute(_ledger(tmp_path, receipt_only), "2026-12-31")}
    assert revalued == {"Assets:CH:GmbH:Current:Wise:EUR"}


def test_pro_forma_is_no_invoice_to_the_payers_software(tmp_path: Path) -> None:
    pf, _, _ = _domestic_pair(tmp_path)
    t = compute(pf)
    payload = qr.payload(qr.build_bill(pf, ISSUER, ISSUER.account("CHF"), t))
    assert "//S1/" not in payload and "10810.00" in payload
    ctx = render.build_context(pf, ISSUER, t, {"type": "qrbill"})
    assert ctx["labels"]["invoice"] == "Proforma-Rechnung" and ctx["proforma"]
    path, _, _ = render.render(pf, ISSUER, tmp_path / "pf.pdf")
    assert path.read_bytes().startswith(b"%PDF")


def test_final_invoice_shows_the_advance_and_pays_the_rest(tmp_path: Path) -> None:
    _, final, _ = _domestic_pair(tmp_path)
    t = compute(final)
    ctx = render.build_context(final, ISSUER, t, {"type": "qrbill"})
    # The VAT the pro forma showed is credited back by name, not shown twice.
    assert ctx["advances"] == [
        {"label": "Anzahlung PF2026001 vom 20.11.2026, inkl. MWST 810.00", "amount": "-10'810.00"}
    ]
    _, export, _ = _export_pair(tmp_path)
    export_ctx = render.build_context(export, ISSUER, compute(export), {"type": "sepa"})
    assert "MWST" not in export_ctx["advances"][0]["label"]
    assert ctx["totals"]["amount_due"] == "10'810.00"
    payload = qr.payload(qr.build_bill(final, ISSUER, ISSUER.account("CHF"), t))
    assert "10810.00" in payload.splitlines()  # the QR-bill asks for the rest only
    render.render(final, ISSUER, tmp_path / "final.pdf")


def test_advances_are_refused_where_they_make_no_sense(tmp_path: Path) -> None:
    _, final, _ = _domestic_pair(tmp_path)
    too_much = final.model_copy(
        update={
            "advances": [
                Advance(proforma="PF1", received=date(2026, 11, 20), amount=Decimal(30000))
            ]
        }
    )
    with pytest.raises(ValueError, match="exceed its total"):
        compute(too_much)
    with pytest.raises(ValueError, match="cannot credit advances"):
        Invoice.model_validate(
            {**final.model_dump(), "proforma": True, "customer": final.resolved_customer}
        )


def test_cli_pro_forma_to_invoice_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The steps a user types for an EU customer who pays half in 2026 and
    # wants the invoice in 2027: render the pro forma, book its payment from
    # the printed legs, render the invoice that credits it, file both returns.
    import json

    from typer.testing import CliRunner

    from quints.cli import app

    runner = CliRunner()
    proj = tmp_path / "books"
    args = ["init", str(proj), "--name", "Jane Doe", "--legal-form", "einzelfirma"]
    res = runner.invoke(app, [*args, "--lang", "en", "--vat-method", "effective", "--samples"])
    assert res.exit_code == 0, res.output
    monkeypatch.chdir(proj)
    with Path("prices.bean").open("a") as f:
        f.write("2026-11-01 price EUR 0.93 CHF\n2026-12-31 price EUR 0.90 CHF\n")
        f.write("2027-01-01 price EUR 0.94 CHF\n")
    head = (
        "kind: export\ncurrency: EUR\ncustomer: globex\n"
        "supply: {from: 2027-01-01, to: 2027-03-31}\n"
    )
    Path("invoicing/globex-pf.yaml").write_text(
        f"number: PF2026001\nproforma: true\nissue_date: 2026-11-02\n{head}"
        "items:\n  - {description: Advance 50 %, quantity: 1, unit_price: 10000.00}\n"
    )
    res = runner.invoke(app, ["invoice", "invoicing/globex-pf.yaml"])
    assert res.exit_code == 0, res.output
    assert "No payment booked for pro forma PF2026001" in res.output, res.output
    assert Path("documents/Liabilities/CH/Einzelfirma/Advances/Export").is_dir()
    legs = res.output.split("books/<year>.bean:\n")[1].strip()
    bank = "    Assets:CH:Einzelfirma:Current:Wise:EUR  10000.00 EUR"
    receipt = "\n".join(bank if "TODO the bank leg" in ln else ln for ln in legs.splitlines())
    with Path("books/2026.bean").open("a") as f:
        f.write("\n" + receipt.replace("2026-11-02 *", "2026-11-20 *") + "\n")
    res = runner.invoke(app, ["invoice", "invoicing/globex-pf.yaml"])
    assert "Advance received (2026-11-20): EUR 10'000.00" in res.output, res.output

    # Year end: the advance is settled by the work, not in money — neither
    # revalued nor valued at the year-end rate, but kept at the CHF received.
    res = runner.invoke(app, ["fx", "revalue", "--at", "2026-12-31", "--json"])
    advance = "Liabilities:CH:Einzelfirma:Advances:Export"
    assert advance not in res.output, res.output
    res = runner.invoke(app, ["report", "bilanz", "--at", "2026-12-31", "--json"])
    rows = json.loads(res.output)["short_term_liabilities"]
    codes = {c["code"]: c["amount"] for r in rows for c in r["codes"]}
    assert codes["2030"] == "9300.00", rows

    Path("invoicing/globex-2027-01.yaml").write_text(
        f"number: INV2027001\nissue_date: 2027-01-29\n{head}"
        "items:\n  - {description: Project, quantity: 1, unit_price: 20000.00}\n"
        "advances:\n  - {proforma: PF2026001, received: 2026-11-20, amount: 10000.00}\n"
    )
    res = runner.invoke(app, ["invoice", "invoicing/globex-2027-01.yaml", "--json"])
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["totals"]["amount_due"] == "10000.00" and not d["cross_check"]["advance_problems"]
    Path("books/2027.bean").write_text(d["ledger_draft"] + "\n")

    assert runner.invoke(app, ["check"]).exit_code == 0
    for period, z221 in (("2026-Q4", "9300.00"), ("2027-Q1", "9400.00")):
        res = runner.invoke(app, ["vat", "report", "-q", period, "--json"])
        assert res.exit_code == 0, res.output
        r = json.loads(res.output)
        assert (r["z200"], r["z221"], r["violations"]) == (z221, z221, []), r

    # Released at the CHF it came in at: nothing is left on 2030 once invoiced.
    res = runner.invoke(app, ["report", "bilanz", "--at", "2027-12-31", "--json"])
    rows = json.loads(res.output)["short_term_liabilities"]
    codes = {c["code"]: c["amount"] for r in rows for c in r["codes"]}
    assert Decimal(codes.get("2030", "0")) == 0, rows


def test_saldo_method_taxes_the_gross_advance_on_receipt(tmp_path: Path) -> None:
    # The SSS form declares Entgelte VAT included (MWST-Info 12): the advance's
    # gross in Q4, the invoice's gross less it in Q1 — CHF 10'810 each.
    cfg = config.validate(
        config.Config(vat_method="saldo", saldo=(config.SaldoRate(Decimal("0.062")),))
    )
    _, _, text = _domestic_pair(tmp_path)
    led = _ledger(tmp_path, text)
    for d0, d1 in (("2026-10-01", "2026-12-31"), ("2027-01-01", "2027-03-31")):
        r = mwst.compute(led, d0, d1, cfg)
        assert not r.violations, r.violations
        assert (r.z200, r.z323_net) == (Decimal("10810.00"), Decimal("10810.00"))


@pytest.mark.parametrize(
    ("marker", "vat", "field"),
    [(":Reduced", "26.00", "z313_net"), (":Exempt", None, "z230")],
)
def test_advance_is_declared_like_the_income_it_becomes(
    tmp_path: Path, marker: str, vat: str | None, field: str
) -> None:
    # The income account's marker says reduced or exempt; the advance, held on
    # a liability until invoiced, is declared in that same Ziffer.
    cfg = config.validate(
        config.Config(income_domestic=f"Income:CH:GmbH:Consulting:External:Domestic{marker}")
    )
    gross = Decimal("1026.00") if vat else Decimal("1000.00")
    tax = f"    Liabilities:CH:GmbH:Tax:OutputVAT  -{vat} CHF\n" if vat else ""
    receipt = (
        '2026-11-20 * "ACME AG" "Advance on pro forma PF2026001"\n'
        '    proforma: "PF2026001"\n'
        f"    Assets:CH:GmbH:Current:UBS:CHF  {gross} CHF\n"
        "    Liabilities:CH:GmbH:Advances:Domestic  -1000.00 CHF\n" + tax
    )
    r = mwst.compute(_ledger(tmp_path, _CHART + receipt), "2026-10-01", "2026-12-31", cfg)
    assert not r.violations, r.violations
    assert getattr(r, field) == Decimal("1000.00")
    if vat:
        assert (r.z303_net, r.z313_tax) == (0, Decimal(vat))


def test_foreign_advance_leaves_at_the_chf_it_came_in_at(tmp_path: Path) -> None:
    # Received at 0.93, invoiced at 0.94: the release and the income it becomes
    # carry the receipt's CHF, so the advances account clears in CHF too.
    _, _, text = _export_pair(tmp_path)
    advance = "Liabilities:CH:GmbH:Advances:Export"
    assert "10000.00 EUR @@ 9300.00 CHF" in text and "-10000.00 EUR @@ 9300.00 CHF" in text
    entries, problems = ledger.load(_ledger(tmp_path, text))
    assert not problems, problems
    price_map = prices.build_price_map(entries)
    legs = [
        (p, e.date)
        for e in entries
        if isinstance(e, data.Transaction)
        for p in e.postings
        if p.account == advance
    ]
    # kmu's book value: each leg's weight at its own date.
    book = sum((ledger.to_chf(get_weight(p), on, price_map) for p, on in legs), Decimal("0"))
    assert book == 0 and sum(p.units.number or 0 for p, _ in legs if p.units) == 0


def test_foreign_domestic_advance_declares_each_share_at_its_own_rate(tmp_path: Path) -> None:
    # VAT and FX together: EUR 10'810 received at 0.93 in Q4, the invoice's
    # other EUR 10'810 at 0.94 in Q1 — and the advance clears in CHF and EUR.
    _, _, text = _domestic_pair(tmp_path, "EUR")
    led = _ledger(tmp_path, text)
    q4 = mwst.compute(led, "2026-10-01", "2026-12-31")
    q1 = mwst.compute(led, "2027-01-01", "2027-03-31")
    for r in (q4, q1):
        assert not r.violations, r.violations
    assert (q4.z303_net, q4.z303_tax) == (Decimal("9300.00"), Decimal("753.30"))
    assert (q1.z303_net, q1.z303_tax) == (Decimal("9400.00"), Decimal("761.40"))
    entries, problems = ledger.load(led)
    assert not problems, problems
    price_map = prices.build_price_map(entries)
    legs = [
        (p, e.date)
        for e in entries
        if isinstance(e, data.Transaction)
        for p in e.postings
        if p.account == "Liabilities:CH:GmbH:Advances:Domestic"
    ]
    book = sum((ledger.to_chf(get_weight(p), on, price_map) for p, on in legs), Decimal("0"))
    assert book == 0 and sum(p.units.number or 0 for p, _ in legs if p.units) == 0


def test_foreign_pro_forma_leaves_its_vat_to_the_day_received(tmp_path: Path) -> None:
    # The VAT on an advance is owed at the rate of the day it arrives (Art. 45
    # MWSTV); the pro forma's own date fixes nothing.
    pf = _base("domestic", "EUR", _ACME, proforma=True, items=[_item("Advance", 10000)])
    text = draft.build_draft(pf, compute(pf), config.Config())
    assert "-810.00 EUR  ; TODO in CHF at the day received: quints vat convert" in text
    assert " CHF" not in text.split("OutputVAT")[1].split(";")[0]


def _receipt(amount: str, on: str = "2026-11-20", account: str = "Domestic", vat: str = "") -> str:
    net = Decimal(amount) - Decimal(vat or 0)
    tax = f"    Liabilities:CH:GmbH:Tax:OutputVAT  -{vat} CHF\n" if vat else ""
    return (
        f'{on} * "ACME AG" "Advance on pro forma PF2026001"\n'
        '    proforma: "PF2026001"\n'
        f"    Assets:CH:GmbH:Current:UBS:CHF  {amount} CHF\n"
        f"    Liabilities:CH:GmbH:Advances:{account}  -{net:.2f} CHF\n" + tax + "\n"
    )


def test_pro_forma_paid_in_part_is_received_not_conflicting(tmp_path: Path) -> None:
    pf, _, _ = _domestic_pair(tmp_path)
    led = _ledger(tmp_path, _CHART + _receipt("5000.00", vat="374.65"))
    cc = verify.cross_check(led, pf, compute(pf))
    assert cc.ok and cc.ledger_total == Decimal("5000.00")
    over = _ledger(tmp_path, _CHART + _receipt("20000.00", vat="1498.61"))
    assert not verify.cross_check(over, pf, compute(pf)).ok


def test_instalments_of_one_pro_forma_are_credited_together(tmp_path: Path) -> None:
    _, final, _ = _domestic_pair(tmp_path)
    halves = [
        Advance(proforma="PF2026001", received=date(2026, 11, d), amount=Decimal("5405.00"))
        for d in (20, 27)
    ]
    final = final.model_copy(update={"advances": halves})
    led = _ledger(
        tmp_path,
        _CHART
        + _receipt("5405.00", vat="405.00")
        + _receipt("5405.00", "2026-11-27", vat="405.00"),
    )
    cc = verify.cross_check(led, final, compute(final))
    assert not cc.advance_problems
    text = draft.build_draft(final, compute(final), config.Config(), received=cc.received)
    assert text.count("Advances:Domestic") == 1 and "10000.00 CHF" in text


def test_receipt_the_invoice_cannot_release_is_reported(tmp_path: Path) -> None:
    _, final, _ = _domestic_pair(tmp_path)
    # Booked as an export advance (no VAT) — the domestic invoice would
    # release VAT never declared, from an account that never held it.
    led = _ledger(tmp_path, _CHART + _receipt("10810.00", account="Export"))
    problems = verify.cross_check(led, final, compute(final)).advance_problems
    assert problems == [
        "advance PF2026001 is booked on Liabilities:CH:GmbH:Advances:Export, "
        "a domestic invoice releases Liabilities:CH:GmbH:Advances:Domestic",
        "advance PF2026001 declared 0.00 VAT on receipt, 810.00 at the invoice's 8.1%",
    ]


def test_invoice_the_advances_cover_asks_for_nothing(tmp_path: Path) -> None:
    _, final, _ = _domestic_pair(tmp_path)
    paid = Advance(proforma="PF2026001", received=date(2026, 11, 20), amount=Decimal("21620"))
    final = final.model_copy(update={"advances": [paid]})
    t = compute(final)
    ctx = render.build_context(final, ISSUER, t, {"type": "none"})
    assert t.amount_due == 0 and ctx["terms"] is None
    path, _, payload = render.render(final, ISSUER, tmp_path / "covered.pdf")
    assert payload is None and path.read_bytes().startswith(b"%PDF")


def test_gross_row_is_the_invoice_total_not_a_second_amount_due(tmp_path: Path) -> None:
    _, final, _ = _domestic_pair(tmp_path)
    english = final.model_copy(update={"locale": "en_CH"})
    ctx = render.build_context(english, ISSUER, compute(english), {"type": "qrbill"})
    assert ctx["labels"]["invoice_total"] == "Invoice total"
