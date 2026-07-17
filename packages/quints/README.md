# quints

Swiss VAT & accounting toolkit for plain-text ([beancount](https://github.com/beancount/beancount)) books.

![quints init scaffolds sample books, quints check validates them, and quints mwst prints the Form-310 VAT return](https://raw.githubusercontent.com/sealambda/quints/main/docs/assets/quickstart.gif)

Everything a Swiss micro-company (GmbH, AG, or Einzelfirma) needs on top of beancount + Fava:

- **MWST**: quarterly VAT report mapped to the ESTV form Ziffern, settlement
  transactions, VAT status, and Bezugsteuer (reverse-charge, Art. 45 ff. MWSTG)
  posting helpers — effective method.
- **Official FX rates**: BAZG/EZV daily rates via
  [beanprice-bazg](../beanprice-bazg), plus a year-end revaluation helper
  (Art. 960 OR).
- **QR-bill invoicing**: Swiss QR-bill (QRR) and SEPA (SCOR/RF) invoices as
  PDF/A via Typst, drafted straight into the ledger.
- **Statement importers**: UBS (MT940), Wise, and Stripe statements draft into
  a `staging/` area — idempotent re-imports, conversion merging, fee splitting,
  open-invoice matching by QR reference — never directly into your books.
- **KMU statements**: statutory Bilanz/Erfolgsrechnung (OR Art. 959a/959b) by
  KMU Kontenrahmen code, English or German, terminal or PDF.
- **Fava extension**: review panel inside the UI you already run.

Everything entity-specific (accounts, VAT registration, importer rules) lives
in a `quints.toml` next to your ledger — see the example in the docs. VAT
rates are law, not configuration, and ship date-ranged in code.

## Install

```bash
uv add quints            # or: pip install quints
uv run quints --help
```

## License

GPL-2.0-only (it links against beancount, which is GPL-2.0-only).
