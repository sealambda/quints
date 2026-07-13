// quints invoice template — data-driven (reads data.json), two modes.
#let d = json("data.json")
#let L = d.labels
#let accent = rgb(d.brand.accent)
#let display = (d.brand.font_display, d.brand.font, "Liberation Sans", "Arial")
#let dstretch = d.brand.display_stretch * 1%
#let muted = luma(105)
#let lines(a) = a.join(linebreak())
#let qrbill = d.payment.type == "qrbill"

#set document(title: L.invoice + " " + d.invoice.number, author: d.issuer.name)
#set page(
  paper: "a4",
  margin: (x: 20mm, top: 18mm, bottom: if qrbill { 108mm } else { 24mm }),
)
#set text(font: (d.brand.font, "Liberation Sans", "Arial"), size: 10pt, fill: luma(20))
#set par(justify: false, leading: 0.62em)

// ── Header: wordmark left, issuer identity right (Art. 26 Abs. 2 lit. a MWSTG) ─
#grid(columns: (1fr, auto),
  align(left + top,
    if d.brand.logo != none {
      image(d.brand.logo, height: 12mm)
    } else {
      text(font: display, stretch: dstretch, size: 15pt, weight: "bold", fill: accent)[#d.issuer.name]
    }
  ),
  align(right + top)[
    #set text(size: 8pt, fill: muted)
    #set par(leading: 0.5em)
    #text(weight: "semibold", fill: luma(60))[#d.issuer.name] \
    #lines(d.issuer.address) \
    #d.issuer.vat_id
    #if d.issuer.email != none [ \ #link("mailto:" + d.issuer.email)[#d.issuer.email] ]
    #if d.issuer.phone != none [ \ #d.issuer.phone ]
  ],
)

// ── Recipient (right, window-envelope position) ───────────────────────
#place(top + left, dx: 112mm, dy: 34mm, block(width: 78mm)[
  #set text(size: 10pt)
  #strong(d.customer.name) \
  #lines(d.customer.address)
  #if d.customer.country != d.issuer.country [ \ #d.customer.country ]
  #if d.customer.vat_id != none [ \ #d.customer.vat_id ]
])

#v(38mm)

// ── Title + meta ──────────────────────────────────────────────────────
#text(font: display, stretch: dstretch, size: 20pt, weight: "semibold", fill: accent)[#L.invoice]
#v(3mm)
#grid(columns: (auto, auto), column-gutter: 8mm, row-gutter: 3pt,
  text(fill: muted)[#L.invoice_no], text(weight: "bold")[#d.invoice.number],
  text(fill: muted)[#L.date], [#d.invoice.issue_date],
  ..(if d.invoice.supply != "" { (text(fill: muted)[#L.supply], [#d.invoice.supply]) } else { () }),
)
#v(7mm)

// ── Line items ────────────────────────────────────────────────────────
#table(
  columns: (auto, 1fr, auto, auto, auto),
  inset: (x: 4pt, y: 6.5pt),
  align: (x, y) => if x >= 2 { right } else { left },
  stroke: none,
  table.hline(stroke: 0.7pt + accent),
  table.header(
    text(size: 9pt, fill: accent, weight: "bold")[#L.pos],
    text(size: 9pt, fill: accent, weight: "bold")[#L.description],
    text(size: 9pt, fill: accent, weight: "bold")[#L.qty],
    text(size: 9pt, fill: accent, weight: "bold")[#L.unit_price],
    text(size: 9pt, fill: accent, weight: "bold")[#L.line_total],
  ),
  table.hline(stroke: 0.7pt + accent),
  ..d.items.map(it => (
    [#it.pos],
    [#it.description],
    [#it.quantity#if it.unit != "" [ #it.unit]],
    [#it.unit_price],
    [#it.total],
  )).flatten(),
  table.hline(stroke: 0.4pt + luma(210)),
)

// ── Totals ────────────────────────────────────────────────────────────
#v(3mm)
#align(right, block(width: 72mm)[
  #let row(l, v, strong: false) = grid(
    columns: (1fr, auto), column-gutter: 6mm,
    if strong { text(weight: "bold")[#l] } else { text(fill: muted)[#l] },
    if strong { text(weight: "bold")[#v] } else [#v],
  )
  #row(L.subtotal, [#d.currency #d.totals.subtotal])
  #if not d.totals.export {
    v(2.2pt)
    row(L.vat + " " + d.totals.vat_rate + "%", [#d.currency #d.totals.vat_amount])
  }
  #if d.totals.show_rounding {
    v(2.2pt)
    row(L.rounding, [#d.currency #d.totals.rounding])
  }
  #v(4pt)
  #line(length: 100%, stroke: 0.5pt + luma(180))
  #v(4pt)
  #row(L.grand_total, text(fill: accent)[#d.currency #d.totals.grand_total], strong: true)
])

// ── Export legal notes ────────────────────────────────────────────────
#if d.totals.export {
  v(6mm)
  block(fill: luma(246), inset: 8pt, radius: 3pt, width: 100%)[
    #text(size: 9pt)[#L.export_note]
    #if d.reverse_charge [ \ #text(size: 9pt, fill: muted)[#L.reverse_charge] ]
  ]
}

// ── Payment terms + notes ─────────────────────────────────────────────
#if d.terms != none or d.notes.len() > 0 {
  v(6mm)
  set text(size: 9pt, fill: muted)
  if d.terms != none [ #d.terms \ ]
  for n in d.notes [ #n \ ]
}

// ── Payment ───────────────────────────────────────────────────────────
#if qrbill {
  // Full-width Swiss QR-bill payment part, flush at the page bottom.
  place(bottom + left, dx: -20mm, dy: 108mm, image("qrbill.svg", width: 210mm))
} else {
  v(8mm)
  block(stroke: 0.6pt + accent, inset: 10pt, radius: 3pt, width: 100%)[
    #text(fill: accent, weight: "bold")[#L.payment_to] \
    #v(2pt)
    #grid(columns: (auto, 1fr), column-gutter: 6mm, row-gutter: 3pt,
      text(fill: muted)[IBAN], [#d.payment.iban],
      ..(if d.payment.bic != none { (text(fill: muted)[BIC], [#d.payment.bic]) } else { () }),
      text(fill: muted)[#L.reference], [#d.payment.reference],
    )
  ]
}
