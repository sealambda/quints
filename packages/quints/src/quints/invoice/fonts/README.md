# Bundled fallback fonts

Liberation Sans, always added to the Typst font search path.

It carries two guarantees the invoice template would otherwise only get by
luck, from whatever the rendering machine happens to have installed:

- `Brand.font` defaults to Liberation Sans, and the template names it as the
  fallback behind every issuer-configured family.
- The Swiss QR-bill Implementation Guidelines permit only Arial, Frutiger,
  Helvetica and Liberation Sans in the payment part. Liberation Sans is the
  one of the four that can be licensed and shipped, and it is metric-
  compatible with Arial, which the `qrbill` library sizes its text boxes for.

Liberation Fonts 2.1.5, SIL Open Font License 1.1 — see `LICENSE`.
