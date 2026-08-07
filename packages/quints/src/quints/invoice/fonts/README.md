# Bundled fonts

Always added to the Typst font search path (searched recursively), so the
default invoice renders identically on every machine — no system font ever
decides how an invoice looks unless the issuer opts into one by name.

Four families, one per directory, all static cuts (variable fonts are
deliberately avoided: a variable font shadows same-named statics and collapses
every weight to its default instance):

- `geist/` — Geist, the default `Brand.font` (body copy).
- `geist-mono/` — Geist Mono, the default `Brand.font_mono`: every figure,
  IBAN and reference, so amount columns align on the digit.
- `newsreader/` — Newsreader, the default `Brand.font_display` (title).
- `liberation/` — Liberation Sans, the last-resort fallback behind every
  issuer-configured family, and the one face the Swiss QR-bill payment part
  may use that we can ship: the Implementation Guidelines permit only Arial,
  Frutiger, Helvetica and Liberation Sans, and Liberation Sans is metric-
  compatible with Arial, which the `qrbill` library sizes its text boxes for.

Licenses: Geist / Geist Mono (Vercel) and Newsreader (Production Type) under
the SIL Open Font License 1.1 — `OFL.txt` in each directory. Liberation
Fonts 2.1.5, SIL Open Font License 1.1 — `liberation/LICENSE`.
