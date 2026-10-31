// quints invoice template — data-driven (reads data.json), two modes.
//
// Everything issuer-specific arrives through `data.json`: no family name, hex
// colour or asset path is hard-coded here. The brand tokens are:
//   accent  — the one saturated colour: title, rules that lead, the amount due
//   ink     — body copy            subtle — labels and secondary text
//   rule    — hairlines            panel  — fill behind bounded blocks
// and three families: `font` (body), `font_display` (title), `font_mono` —
// identifiers a payer matches on (invoice no., IBAN, reference) and amounts.
// Dates, periods and units stay in the body face, with tabular figures.
// `font_mono` defaults to the bundled Geist Mono; an issuer may swap in any
// family (tabular figures are requested either way).
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
#let margin-top = 16mm
// Panel tints bleed this far into the margin, so the text inside every panel
// stays on the text column with everything else on the page.
#let bleed = 8pt

// Display face. `stretch` is only passed when the issuer actually asks for a
// width other than normal: a font with no wdth axis fails to match against an
// explicit stretch and silently falls back to the body face.
#let disp(it, size: 10pt, fill: ink) = text(
  font: display, size: size, weight: d.brand.display_weight, fill: fill,
  ..(if d.brand.display_stretch != 100 { (stretch: d.brand.display_stretch * 1%) } else { (:) }),
)[#it]

// Small tracked capitals: every label in the document, at one size.
#let label(it, fill: subtle) = text(size: 7pt, weight: "medium", tracking: 0.1em, fill: fill)[#upper(it)]
// Identifiers and amounts. The mono family keeps columns on the digit;
// `number-width` asks a proportional fallback for its tabular figures, so
// alignment survives even when `font_mono` points at a non-monospace face.
#let fig(it, weight: "regular", fill: ink, size: 9.5pt) = text(
  font: mono, size: size, weight: weight, fill: fill, number-width: "tabular",
)[#it]
// Dates, periods: body face, tabular figures.
#let tab(it) = text(number-width: "tabular")[#it]
// Label/value pairs. One label width for every such grid, so values share a
// left edge across the page; bottom-aligned, so labels sit on the value's
// baseline (text boxes end at the baseline).
#let pairs(..rows) = grid(
  columns: (34mm, 1fr), column-gutter: 3mm, row-gutter: 5pt, align: bottom,
  ..rows.pos().map(r => (label(r.at(0)), r.at(1))).flatten(),
)

#set document(
  title: d.document.title, author: d.issuer.name,
  description: d.document.description, keywords: d.document.keywords,
)
// Credit line. A footer is a PDF/UA artifact, which may not carry a link:
// the address is printed instead (viewers auto-link it).
#let credit = if d.brand.credit {
  align(right, text(size: 6.5pt, fill: subtle)[#L.made_with · go.sealambda.com/quints])
}
#set page(
  paper: "a4",
  margin: (x: 20mm, top: margin-top, bottom: 24mm),
  footer-descent: 30%,
  // The QR-bill page prints its credit above the perforation instead (below).
  footer: context if not (qrbill and here().page() == counter(page).final().first()) { credit },
)
#set text(font: body, size: 9.5pt, fill: ink, lang: d.lang, region: d.region)
#set par(justify: false, leading: 0.65em)
#show heading.where(level: 1): it => disp(it.body, size: 25pt, fill: accent)
#show heading: set block(above: 0pt, below: 6mm)

// ── Header: wordmark left, issuer identity right (Art. 26 Abs. 2 lit. a MWSTG) ─
#grid(columns: (1fr, auto), column-gutter: 10mm,
  align(left + top,
    if d.brand.logo != none {
      image(d.brand.logo, height: d.brand.logo_height * 1mm, alt: d.issuer.name)
    } else {
      disp(d.issuer.name, size: 17pt)
    }
  ),
  align(right + top)[
    #set text(size: 7.5pt, fill: subtle)
    #set par(leading: 0.55em)
    #text(fill: ink, weight: "medium")[#d.issuer.name] \
    #lines(d.issuer.address)
    #if d.issuer.vat_id != none [ \ #d.issuer.vat_id ]
    #if d.issuer.email != none [ \ #link("mailto:" + d.issuer.email)[#d.issuer.email] ]
    #if d.issuer.phone != none [ \ #d.issuer.phone ]
  ],
)
#v(4mm)
// Rules run to the same edge as the panel tints: the text column plus `bleed`.
#pad(x: -bleed, line(length: 100%, stroke: 0.5pt + rule))

// ── Recipient (right, window-envelope position) ───────────────────────
// Postal lines only: what shows through the window. Width keeps it inside
// the right margin.
#let recipient = block(width: 58mm)[
  #set par(leading: 0.6em)
  #text(weight: "medium")[#d.customer.name] \
  #lines(d.customer.address)
  #if d.customer.country != d.issuer.country [ \ #d.customer.country_name ]
]
#place(top + left, dx: 112mm, dy: 30mm, recipient)

// ── Title + meta, beside the recipient ────────────────────────────────
#v(3mm)
#block(width: 104mm)[
  = #L.invoice
  #pairs(
    (L.invoice_no, fig(d.invoice.number, weight: "medium")),
    (L.date, tab(d.invoice.issue_date)),
    ..(if d.invoice.due_date != "" { ((L.due_date, tab(d.invoice.due_date)),) } else { () }),
    ..(if d.invoice.supply != "" { ((L.supply, tab(d.invoice.supply)),) } else { () }),
    // The customer's own references (PO number, Leitweg-ID, cost centre…).
    // They belong next to the invoice number: it is what the payer's
    // accounts-payable department matches the invoice on.
    ..d.references.map(r => (r.label, fig(r.value))),
    ..(if d.customer.vat_id != none { ((L.customer_vat, fig(d.customer.vat_id)),) } else { () }),
  )
]
// The window position is fixed, the recipient's height is not: clear
// whichever of the two columns ends lower.
#context {
  let bottom = margin-top + 30mm + measure(recipient).height
  v(calc.max(0pt, bottom - here().position().y) + 8mm)
}

// ── Line items ────────────────────────────────────────────────────────
// Widened by `bleed` so its rules meet the panel edges; the outer columns
// pad it back, so the text stays on the column.
#pad(x: -bleed, table(
  columns: (auto, 1fr, auto, auto, auto),
  inset: (x, y) => (left: if x == 0 { bleed } else { 0pt }, right: if x == 4 { bleed } else { 0pt }, y: 7pt),
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
    [#fig(it.quantity)#if it.unit != "" [ #it.unit]],
    fig(it.unit_price),
    fig(it.total),
  )).flatten(),
  table.hline(stroke: 0.5pt + rule),
))

// ── Totals ────────────────────────────────────────────────────────────
// One right-aligned column of bare figures ending where the line-item total
// column does (the currency is named in the table header and on the amount
// due). A subtotal alone would only repeat the amount due: the breakdown
// prints when something stands between the two.
#let vat-row = not d.totals.export and d.totals.vat_registered
#let breakdown = vat-row or d.totals.show_rounding or d.advances.len() > 0
#v(4mm)
// Wider when it credits advances: their labels name the pro forma and date.
// Unbreakable: the breakdown never leaves the amount due on the next page.
#align(right, block(width: if d.advances.len() > 0 { 100mm } else { 76mm }, breakable: false)[
  #let row(l, v) = (label(l), fig(v))
  #if breakdown {
    grid(
      columns: (1fr, auto), column-gutter: 6mm, row-gutter: 7pt, align: (left + bottom, right + bottom),
      ..row(L.subtotal, d.totals.subtotal),
      ..(if vat-row { row(L.vat + " " + d.totals.vat_rate + "%", d.totals.vat_amount) } else { () }),
      ..(if d.totals.show_rounding { row(L.rounding, d.totals.rounding) } else { () }),
      // Advances credited from pro formas: the invoice total — VAT on the
      // whole supply — first, then what was paid ahead, leaving the amount due.
      ..(if d.advances.len() > 0 {
        row(L.invoice_total, d.totals.grand_total)
        d.advances.map(a => row(a.label, a.amount)).flatten()
      } else { () }),
    )
    v(6pt)
  }
  // The amount due — and by when — is what a reader looks for: accent on a
  // tinted band whose tint bleeds past the column, so the amount itself stays
  // on the figure column above.
  #block(fill: panel, inset: (y: 7pt), outset: (x: bleed), width: 100%,
    grid(columns: (1fr, auto), column-gutter: 6mm, row-gutter: 5pt, align: (left + bottom, right + bottom),
      label(if d.advances.len() > 0 { L.amount_due } else { L.grand_total }, fill: accent),
      [#text(size: 8pt, fill: accent, tracking: 0.05em)[#d.currency]
        #fig(d.totals.amount_due, weight: "medium", fill: accent, size: 12pt)],
      ..(if d.invoice.due_date != "" {
        (label(L.due_date, fill: accent), text(size: 8.5pt, fill: accent)[#tab(d.invoice.due_date)])
      } else { () }),
    ),
  )
])

// ── Pro forma note: what this document is, and what it is not ─────────
#if d.proforma {
  v(6mm)
  block(fill: panel, width: 100%, inset: (y: 8pt), outset: (x: bleed))[#text(size: 8.5pt)[#L.proforma_note]]
}

// ── Legal notes, payment terms, notes ─────────────────────────────────
#if d.totals.export and (d.totals.vat_registered or d.reverse_charge) {
  v(6mm)
  set text(size: 8.5pt)
  if d.totals.vat_registered [ #L.export_note \ ]
  if d.reverse_charge [ #L.reverse_charge \ ]
}
#if d.terms != none or d.notes.len() > 0 {
  v(4mm)
  set text(size: 8.5pt, fill: subtle)
  if d.terms != none [ #d.terms \ ]
  for n in d.notes [ #n \ ]
}

// ── Payment ───────────────────────────────────────────────────────────
#if qrbill {
  // Full-width Swiss QR-bill payment part, flush at the page bottom. Its
  // layout and typography are prescribed by the Implementation Guidelines —
  // it is rendered by qr.py and deliberately left unstyled here. Only the
  // last page reserves its 105 mm: pushed to the foot, unbreakable, and down
  // into the 24 mm bottom margin. The credit sits in the 3 mm above the
  // perforation — nothing but the payment part may print below it.
  v(1fr)
  block(height: 108mm - 24mm, width: 100%, breakable: false, credit)
  place(bottom + left, dx: -20mm, dy: 24mm, image("qrbill.svg", width: 210mm, alt: d.payment.alt))
} else if d.payment.type == "sepa" {
  // At the foot of the page, like the QR-bill: payment is the last thing read.
  v(8mm)
  v(1fr)
  // Unbreakable: the code and the fields it encodes stay side by side.
  block(fill: panel, inset: (y: 9pt), outset: (x: bleed), width: 100%, breakable: false, grid(
    columns: (1fr, auto), column-gutter: 7mm,
    [
      #label(L.payment_to, fill: accent)
      #v(5pt)
      // Beneficiary, IBAN, BIC — the three fields a payer retypes into their
      // banking form, and the three a mismatch bounces the transfer on. The
      // IBAN is the one people mistype, so it carries the weight. The BIC is
      // mandatory on this branch (render.py refuses without one); the bank's
      // name sits next to it so the payer can sanity-check the code instead of
      // looking up their own.
      #pairs(
        (L.beneficiary, d.payment.beneficiary),
        ("IBAN", fig(d.payment.iban, weight: "medium")),
        ("BIC/SWIFT", fig(d.payment.bic)),
        ..(if d.payment.bank_name != none { ((L.bank, d.payment.bank_name),) } else { () }),
        (L.reference, fig(d.payment.reference)),
        (L.amount, [#text(size: 8pt, tracking: 0.05em)[#d.currency] #fig(d.totals.amount_due, weight: "medium")]),
      )
    ],
    // EUR only: the EPC QR code pre-fills this same transfer in the payer's
    // banking app. Febelfin asks for at least 2 × 2 cm.
    if d.payment.at("epc", default: none) != none {
      align(center + horizon, stack(spacing: 3pt,
        image("epc.svg", width: 26mm, alt: d.payment.alt),
        text(size: 6.5pt, fill: subtle)[#L.scan_to_pay],
      ))
    },
  ))
}
