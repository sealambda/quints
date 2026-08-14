// quints invoice template — data-driven (reads data.json), two modes.
//
// Everything issuer-specific arrives through `data.json`: no family name, hex
// colour or asset path is hard-coded here. The brand tokens are:
//   accent  — the one saturated colour: title, rules that lead, the amount due
//   ink     — body copy            subtle — labels and secondary text
//   rule    — hairlines            panel  — fill behind bounded blocks
// and three families: `font` (body), `font_display` (title), `font_mono` —
// every figure, IBAN and reference. `font_mono` defaults to the bundled Geist
// Mono, so amount columns align on the digit out of the box; an issuer may
// swap in any family (tabular figures are requested either way).
#let d = json("data.json")
#let L = d.labels
#let accent = rgb(d.brand.accent)
#let ink = rgb(d.brand.ink)
#let subtle = rgb(d.brand.subtle)
#let rule = rgb(d.brand.rule)
#let panel = rgb(d.brand.panel)
#let body = (d.brand.font, "Liberation Sans", "Arial")
#let display = (d.brand.font_display, d.brand.font, "Liberation Sans", "Arial")
#let mono = (d.brand.font_mono, d.brand.font, "Liberation Mono", "Courier New")
#let lines(a) = a.join(linebreak())
#let qrbill = d.payment.type == "qrbill"

// Display face. `stretch` is only passed when the issuer actually asks for a
// width other than normal: a font with no wdth axis fails to match against an
// explicit stretch and silently falls back to the body face.
#let disp(it, size: 10pt, fill: ink) = text(
  font: display, size: size, weight: d.brand.display_weight, fill: fill,
  ..(if d.brand.display_stretch != 100 { (stretch: d.brand.display_stretch * 1%) } else { (:) }),
)[#it]

// Small tracked capitals: every label in the document, at one size.
#let label(it) = text(size: 7pt, weight: "medium", tracking: 0.1em, fill: subtle)[#upper(it)]
// Figures. The mono family keeps columns on the digit; `number-width` asks a
// proportional fallback for its tabular figures, so alignment survives even
// when the issuer points `font_mono` at a non-monospace face.
#let fig(it, weight: "regular", fill: ink, size: 9.5pt) = text(
  font: mono, size: size, weight: weight, fill: fill, number-width: "tabular",
)[#it]

#set document(title: L.invoice + " " + d.invoice.number, author: d.issuer.name)
#set page(
  paper: "a4",
  margin: (x: 20mm, top: 16mm, bottom: if qrbill { 108mm } else { 24mm }),
)
#set text(font: body, size: 9.5pt, fill: ink)
#set par(justify: false, leading: 0.65em)

// ── Header: wordmark left, issuer identity right (Art. 26 Abs. 2 lit. a MWSTG) ─
#grid(columns: (1fr, auto), column-gutter: 10mm,
  align(left + top,
    if d.brand.logo != none {
      image(d.brand.logo, height: d.brand.logo_height * 1mm)
    } else {
      disp(d.issuer.name, size: 17pt)
    }
  ),
  align(right + top)[
    #set text(size: 7.5pt, fill: subtle)
    #set par(leading: 0.55em)
    #text(fill: ink, weight: "medium")[#d.issuer.name] \
    #lines(d.issuer.address) \
    #d.issuer.vat_id
    #if d.issuer.email != none [ \ #link("mailto:" + d.issuer.email)[#d.issuer.email] ]
    #if d.issuer.phone != none [ \ #d.issuer.phone ]
  ],
)
#v(4mm)
#line(length: 100%, stroke: 0.5pt + rule)

// ── Recipient (right, window-envelope position) ───────────────────────
#place(top + left, dx: 112mm, dy: 30mm, block(width: 78mm)[
  #set par(leading: 0.6em)
  #text(weight: "medium")[#d.customer.name] \
  #lines(d.customer.address)
  #if d.customer.country != d.issuer.country [ \ #d.customer.country ]
  #if d.customer.vat_id != none [ \ #d.customer.vat_id ]
])

// Clears the recipient block placed above; the QR-bill leaves only ~173mm of
// body height, so the rhythm below stays deliberately tight.
#v(17mm)

// ── Title + meta ──────────────────────────────────────────────────────
#disp(L.invoice, size: 25pt, fill: accent)
#v(4mm)
#grid(columns: (auto, auto), column-gutter: 7mm, row-gutter: 4pt,
  align(horizon, label(L.invoice_no)), fig(d.invoice.number, weight: "medium"),
  align(horizon, label(L.date)), fig(d.invoice.issue_date),
  ..(if d.invoice.supply != "" {
    (align(horizon, label(L.supply)), text(size: 9.5pt)[#d.invoice.supply])
  } else { () }),
)
#v(8mm)

// ── Line items ────────────────────────────────────────────────────────
#table(
  columns: (auto, 1fr, auto, auto, auto),
  inset: (x: 0pt, y: 7pt),
  column-gutter: 6mm,
  align: (x, y) => if x >= 2 { right } else { left },
  stroke: none,
  table.hline(stroke: 1pt + accent),
  // The currency lives in the column headers (and once more on the amount
  // due), never inside the amount cells: a repeated "CHF " prefix is what
  // keeps right-aligned columns from ever lining up on the digit.
  table.header(
    label(L.pos), label(L.description), label(L.qty),
    label(L.unit_price + " " + d.currency), label(L.line_total + " " + d.currency),
  ),
  table.hline(stroke: 0.5pt + rule),
  ..d.items.map(it => (
    fig(it.pos, fill: subtle, size: 9pt),
    [#it.description],
    fig[#it.quantity#if it.unit != "" [ #it.unit]],
    fig(it.unit_price),
    fig(it.total),
  )).flatten(),
  table.hline(stroke: 0.5pt + rule),
)

// ── Totals ────────────────────────────────────────────────────────────
// One grid, so every amount shares a single right-aligned column that ends
// exactly where the line-item total column does; the figures are bare (the
// currency is named in the table header and on the amount due).
#v(4mm)
#align(right, block(width: 76mm)[
  #let row(l, v) = (align(horizon, label(l)), fig(v))
  #grid(
    columns: (1fr, auto), column-gutter: 6mm, row-gutter: 7pt,
    ..row(L.subtotal, d.totals.subtotal),
    ..(if not d.totals.export {
      row(L.vat + " " + d.totals.vat_rate + "%", d.totals.vat_amount)
    } else { () }),
    ..(if d.totals.show_rounding { row(L.rounding, d.totals.rounding) } else { () }),
  )
  #v(6pt)
  // The amount due is the one thing a reader looks for: accent on a tinted
  // band. The fill bleeds outward (`outset`), no horizontal inset — so the
  // amount keeps the exact right edge of every figure above it.
  #block(fill: panel, inset: (y: 7pt), outset: (x: 8pt), width: 100%,
    grid(columns: (1fr, auto), column-gutter: 6mm,
      align(horizon, text(size: 8pt, weight: "medium", tracking: 0.1em, fill: accent)[
        #upper(L.grand_total)
      ]),
      [#text(size: 8pt, fill: accent, tracking: 0.05em)[#d.currency]
        #fig(d.totals.grand_total, weight: "medium", fill: accent, size: 12pt)],
    ),
  )
])

// ── Export legal notes ────────────────────────────────────────────────
#if d.totals.export {
  v(6mm)
  block(
    fill: panel, width: 100%,
    inset: (left: 9pt, right: 9pt, y: 8pt), stroke: (left: 1.5pt + accent),
  )[
    #set text(size: 8.5pt)
    #L.export_note
    #if d.reverse_charge [ \ #text(fill: subtle)[#L.reverse_charge] ]
  ]
}

// ── Payment terms + notes ─────────────────────────────────────────────
#if d.terms != none or d.notes.len() > 0 {
  v(5mm)
  set text(size: 8.5pt, fill: subtle)
  if d.terms != none [ #d.terms \ ]
  for n in d.notes [ #n \ ]
}

// ── Payment ───────────────────────────────────────────────────────────
#if qrbill {
  // Full-width Swiss QR-bill payment part, flush at the page bottom. Its
  // layout and typography are prescribed by the Implementation Guidelines —
  // it is rendered by qr.py and deliberately left unstyled here.
  place(bottom + left, dx: -20mm, dy: 108mm, image("qrbill.svg", width: 210mm))
} else {
  v(8mm)
  block(fill: panel, inset: (x: 10pt, y: 9pt), width: 100%)[
    #text(size: 8pt, weight: "medium", tracking: 0.1em, fill: accent)[#upper(L.payment_to)]
    #v(4pt)
    // Beneficiary, IBAN, BIC — the three fields a payer retypes into their
    // banking form, and the three a mismatch bounces the transfer on. The BIC
    // is mandatory on this branch (render.py refuses without one); the bank's
    // name sits next to it so the payer can sanity-check the code instead of
    // looking up their own.
    #grid(columns: (auto, 1fr), column-gutter: 7mm, row-gutter: 4pt,
      align(horizon, label(L.beneficiary)), text(size: 9.5pt)[#d.payment.beneficiary],
      align(horizon, label("IBAN")), fig(d.payment.iban),
      align(horizon, label("BIC/SWIFT")), fig(d.payment.bic, weight: "medium"),
      ..(if d.payment.bank_name != none {
        (align(horizon, label(L.bank)), text(size: 9.5pt)[#d.payment.bank_name])
      } else { () }),
      align(horizon, label(L.reference)), fig(d.payment.reference),
    )
  ]
}
