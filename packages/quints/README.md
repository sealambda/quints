# quints

Plain-text accounting for Swiss micro-companies (GmbH, AG, or Einzelfirma),
on top of [beancount](https://github.com/beancount/beancount) and
[Fava](https://github.com/beancount/fava).

![quints init scaffolds sample books, quints check validates them, and quints vat report prints the Form-310 VAT return](https://raw.githubusercontent.com/sealambda/quints/main/docs/assets/quickstart.gif)

**Docs: [sealambda.github.io/quints](https://sealambda.github.io/quints/)** —
every command shown there is executed by the test suite.

## Install

```bash
uv tool install quints        # or: pipx install quints  /  pip install quints
quints init my-books --samples --yes
cd my-books
quints vat report -p 2026-Q3
```

Needs Python 3.11+. Typst, for the PDFs, comes bundled.

## What it does

- **VAT (MWST)**: the registration threshold, the return Ziffer by Ziffer
  under the effective or the Saldosteuersatz method, settlement entries,
  what is filed but unpaid, and Bezugsteuer (Art. 45 ff. MWSTG).
  Registration, a method switch and deregistration are dated, and every
  period is computed under the rules in force for it.
- **Invoicing**: Swiss QR-bill and export invoices as PDF/A with embedded
  Factur-X data, in German, French, Italian, English or Spanish. SCOR
  references by default, QRR on request. Credit notes, discounts,
  pass-through items, recurring invoices and payment reminders. Each invoice
  is cross-checked against its booking in the ledger.
- **Open items**: receivables and payables aged by due date, in their own
  currency and consolidated.
- **Statement importers**: UBS (MT940), Yapeal, Wise and Stripe draft into a
  `staging/` area, never straight into your books. Re-imports are
  idempotent, and payments are matched to open invoices by reference or
  amount.
- **Statutory statements**: Bilanz and Erfolgsrechnung (OR Art. 959a/959b)
  by KMU Kontenrahmen code, in English or German, in the terminal or as PDF.
- **Year-end**: a closing checklist, the depreciation entry and the FX
  revaluation (Art. 960 ff. OR), at official BAZG/EZV rates via
  [beanprice-bazg](https://pypi.org/project/beanprice-bazg/).
- **Fava extension**: a review panel inside the UI you already run.

quints is deterministic and every reporting command takes `--json`, so an AI
coding agent can drive it. It never calls a model itself.

Everything entity-specific (accounts, VAT registration, importer rules) lives
in a `quints.toml` next to your ledger. VAT rates are law, not configuration,
and ship date-ranged in code.

## License

GPL-2.0-only, the license beancount itself uses. quints is not tax or legal
advice and comes without warranty. You, or your Treuhänder, stay responsible
for what you file and sign.
