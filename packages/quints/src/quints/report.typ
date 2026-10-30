// quints statutory statements — data-driven (reads data.json).
// One page per statement: Bilanz, then Erfolgsrechnung. With `columns`, a
// second amount column carries the prior year (Art. 958d Abs. 2 OR).
#let d = json("data.json")
#let accent = rgb(d.issuer.accent)
#let muted = luma(105)
#let prior = d.columns.len() > 0

#set document(title: d.bilanz.title + " · " + d.issuer.name, author: d.issuer.name)
#set page(paper: "a4", margin: (x: 22mm, top: 20mm, bottom: 22mm))
#set text(font: (d.issuer.font, "Liberation Sans", "Arial"), size: 10pt, fill: luma(20))
#set par(justify: false, leading: 0.6em)

#let statement(s) = {
  // header
  text(size: 7.5pt, fill: muted)[#(
    d.issuer.name + " · " + d.issuer.address.join(", ")
    + if d.issuer.vat_id not in (none, "") { " · " + d.issuer.vat_id } else { "" }
  )]
  v(2mm)
  text(size: 17pt, weight: "bold", fill: accent)[#s.title]
  v(1mm)
  text(size: 9pt, fill: muted)[#s.subtitle]
  v(5mm)
  // lines
  table(
    columns: if prior { (11mm, 1fr, 30mm, 30mm) } else { (11mm, 1fr, 30mm) },
    inset: (x: 3pt, y: 4.2pt),
    align: (x, y) => if x >= 2 { right } else { left },
    stroke: none,
    ..if prior {
      ([], [], ..d.columns.map(c => text(size: 8pt, weight: "bold", fill: muted)[#c]))
    } else { () },
    ..for line in s.lines {
      let lbl = if line.indent > 0 {
        h(5mm) + text(size: 9pt, fill: muted)[#line.label]
      } else if line.bold {
        text(weight: "bold")[#line.label]
      } else {
        [#line.label]
      }
      let fmt(a) = if line.indent > 0 {
        text(size: 9pt, fill: muted)[#a]
      } else if line.bold {
        text(weight: "bold")[#a]
      } else {
        [#a]
      }
      (
        ..if line.rule { (table.hline(stroke: 0.7pt + accent),) } else { () },
        text(size: 8pt, fill: muted)[#line.code], lbl, fmt(line.amount),
        ..if prior { (text(fill: muted)[#fmt(line.prior)],) } else { () },
      )
    }
  )
  if "note" in s and s.note != "" {
    v(3mm)
    text(size: 8pt, fill: muted)[#s.note]
  }
}

// Loader errors and other problems in the books, once, before any figure.
#if d.violations.len() > 0 {
  block(width: 100%, inset: 3mm, stroke: 0.7pt + rgb("#b45309"))[
    #text(size: 9pt, weight: "bold", fill: rgb("#b45309"))[#d.violations_title]
    #for v in d.violations [
      #linebreak()#text(size: 8pt)[#if v.date != "" [#v.date · ]#v.message]
    ]
  ]
  v(4mm)
}

#statement(d.bilanz)
#pagebreak()
#statement(d.erfolg)

// Signed by the chair of the management body and whoever is responsible for
// the financial reporting (Art. 958 Abs. 3 OR); one person may be both.
#let S = d.signatures
#v(1fr)
#grid(columns: (1fr, 1fr, 1fr), column-gutter: 8mm,
  ..(S.place_date, S.chair, S.accounting).map(l => [
    #v(12mm)
    #line(length: 100%, stroke: 0.5pt + muted)
    #text(size: 8pt, fill: muted)[#l]
  ]),
)
#v(4mm)
#text(size: 8pt, fill: muted)[#S.no_notes]
