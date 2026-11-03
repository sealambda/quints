// quints statutory statements — data-driven (reads data.json).
// One page per statement: Bilanz, then Erfolgsrechnung. Each amount column is
// headed by its year; a second one carries the prior year (Art. 958d Abs. 2 OR).
//
// Same brand tokens and families as the invoice (see invoice/template.typ):
// accent leads (title, section heads, the rules above totals), mono carries
// account codes and amounts, small tracked capitals carry every label.
#let d = json("data.json")
#let accent = rgb(d.brand.accent)
#let ink = rgb(d.brand.ink)
#let subtle = rgb(d.brand.subtle)
#let rule = rgb(d.brand.rule)
#let body = (d.brand.font, "Liberation Sans", "Arial")
#let display = (d.brand.font_display, d.brand.font, "Liberation Sans", "Arial")
#let mono = (d.brand.font_mono, d.brand.font, "Liberation Mono", "Courier New")
#let prior = d.columns.len() > 1
#let margin-top = 16mm

#let disp(it, size: 10pt, fill: ink) = text(
  font: display, size: size, weight: d.brand.display_weight, fill: fill,
  ..(if d.brand.display_stretch != 100 { (stretch: d.brand.display_stretch * 1%) } else { (:) }),
)[#it]
#let label(it, fill: subtle) = text(size: 7pt, weight: "medium", tracking: 0.1em, fill: fill)[#upper(it)]
// Amounts: tabular, on the digit, with a true minus sign.
#let fig(a, weight: "regular", fill: ink, size: 9.5pt) = text(
  font: mono, size: size, weight: weight, fill: fill, number-width: "tabular",
)[#if a.starts-with("-") { "−" + a.slice(1) } else { a }]

#set document(title: d.document.title, ..(if d.issuer.name != "" { (author: d.issuer.name) } else { (:) }))
#set page(
  paper: "a4",
  margin: (x: 20mm, top: margin-top, bottom: 22mm),
  footer-descent: 30%,
  footer: context text(size: 6.5pt, fill: subtle, number-width: "tabular")[
    #d.document.title #h(1fr) #counter(page).display() / #counter(page).final().first()
  ],
)
#set text(font: body, size: 9.5pt, fill: ink, lang: d.lang, region: "CH")
#set par(justify: false, leading: 0.65em)
// A text box ends at the baseline: the gap below clears the title's descenders.
#show heading.where(level: 1): it => block(below: 4.5mm, disp(it.body, size: 25pt, fill: accent))

// ── Header: wordmark left, issuer identity right — as on the invoice ──
#let header = {
  grid(columns: (1fr, auto), column-gutter: 10mm,
    align(left + top,
      if d.brand.logo != none {
        image(d.brand.logo, height: d.brand.logo_height * 1mm, alt: d.issuer.name)
      } else if d.issuer.name != "" {
        disp(d.issuer.name, size: 17pt)
      }
    ),
    align(right + top)[
      #set text(size: 7.5pt, fill: subtle)
      #set par(leading: 0.55em)
      #if d.issuer.name != "" [#text(fill: ink, weight: "medium")[#d.issuer.name] \ ]
      #d.issuer.address.join(linebreak())
      #if d.issuer.vat_id not in (none, "") [ \ #d.issuer.vat_id ]
    ],
  )
  v(4mm)
  line(length: 100%, stroke: 0.5pt + rule)
  v(7mm)
}

// What a line is, from the flags report_pdf.py sets: a section head carries
// no figure in either year, a subtotal is bold, a total also takes a rule.
#let kind(l) = if l.rule { "total" } else if l.bold and l.amount == "" and l.prior == "" {
  "section"
} else if l.bold { "subtotal" } else if l.indent > 0 { "account" } else { "row" }

#let statement(s, before: none) = {
  header
  before
  heading(level: 1, s.title)
  text(size: 8.5pt, fill: subtle, number-width: "tabular")[#s.subtitle]
  v(8mm)
  let kinds = s.lines.map(kind)
  table(
    columns: (12mm, 1fr, ..d.columns.map(_ => 27mm)),
    column-gutter: (3mm, 6mm, 6mm),
    // Air above each section head and subtotal groups the lines beneath them.
    inset: (x, y) => (
      x: 0pt,
      top: if y == 0 { 6pt } else {
        let k = kinds.at(y - 1)
        if k == "section" and y > 1 { 15pt } else if k in ("subtotal", "total") { 6pt } else { 4pt }
      },
      bottom: if y == 0 { 6pt } else if kinds.at(y - 1) in ("subtotal", "total") { 6pt } else { 4pt },
    ),
    align: (x, y) => if x >= 2 { right + bottom } else { left + bottom },
    stroke: none,
    table.hline(stroke: 1pt + accent),
    table.header(
      label(d.head.code), label(d.head.item),
      ..d.columns.enumerate().map(((i, c)) => label(c, fill: if i == 0 { ink } else { subtle })),
    ),
    table.hline(stroke: 0.5pt + rule),
    ..for (line, k) in s.lines.zip(kinds) {
      let amount(a, i) = {
        let fill = if i > 0 or k == "account" { subtle } else { ink }
        let weight = if k in ("subtotal", "total") { "medium" } else { "regular" }
        fig(a, weight: weight, fill: fill, size: if k == "account" { 8.5pt } else { 9.5pt })
      }
      let lbl = if k == "section" {
        label(line.label, fill: accent)
      } else if k == "account" {
        pad(left: 4mm, text(size: 8.5pt, fill: subtle)[#line.label])
      } else if k in ("subtotal", "total") {
        text(weight: "medium")[#line.label]
      } else [#line.label]
      (
        ..if k == "total" { (table.hline(stroke: 1pt + accent),) }
          else if k == "subtotal" { (table.hline(stroke: 0.5pt + rule),) } else { () },
        fig(line.code, fill: subtle, size: 8.5pt),
        lbl,
        amount(line.amount, 0),
        ..if prior { (amount(line.prior, 1),) } else { () },
      )
    },
  )
  if "note" in s and s.note != "" {
    v(4mm)
    text(size: 8pt, fill: subtle)[#s.note]
  }
}

// Loader errors and other problems in the books, once, before any figure.
#let problems = if d.violations.len() > 0 {
  let warn = rgb("#a14a00")
  block(width: 100%, inset: (x: 4mm, y: 3.5mm), stroke: 0.7pt + warn, below: 6mm)[
    #text(size: 9pt, weight: "medium", fill: warn)[#d.violations_title]
    #set text(size: 8pt)
    #for v in d.violations [
      #linebreak()#if v.date != "" [#text(number-width: "tabular")[#v.date] · ]#v.message
    ]
  ]
}

#statement(d.bilanz, before: problems)
#pagebreak()
#statement(d.erfolg)

// Signed by the chair of the management body and whoever is responsible for
// the financial reporting (Art. 958 Abs. 3 OR); one person may be both.
#let S = d.signatures
#v(1fr)
#block(breakable: false)[
  #grid(columns: (1fr, 1fr, 1fr), column-gutter: 8mm,
    ..(S.place_date, S.chair, S.accounting).map(l => [
      #v(14mm)
      #line(length: 100%, stroke: 0.5pt + subtle)
      #v(1.5mm)
      #label(l)
    ]),
  )
  #v(6mm)
  #text(size: 8pt, fill: subtle)[#S.no_notes]
]
