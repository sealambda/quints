// quints statutory statements — data-driven (reads data.json).
// One page per statement: Bilanz, then Erfolgsrechnung.
#let d = json("data.json")
#let accent = rgb(d.issuer.accent)
#let muted = luma(105)

#set document(title: d.bilanz.title + " · " + d.issuer.name, author: d.issuer.name)
#set page(paper: "a4", margin: (x: 22mm, top: 20mm, bottom: 22mm))
#set text(font: (d.issuer.font, "Liberation Sans", "Arial"), size: 10pt, fill: luma(20))
#set par(justify: false, leading: 0.6em)

#let statement(s) = {
  // header
  text(size: 7.5pt, fill: muted)[#d.issuer.name · #d.issuer.address.join(", ") · #d.issuer.vat_id]
  v(2mm)
  text(size: 17pt, weight: "bold", fill: accent)[#s.title]
  v(1mm)
  text(size: 9pt, fill: muted)[#s.subtitle]
  v(5mm)
  // lines
  table(
    columns: (11mm, 1fr, 30mm),
    inset: (x: 3pt, y: 4.2pt),
    align: (x, y) => if x == 2 { right } else { left },
    stroke: none,
    ..for line in s.lines {
      let lbl = if line.indent > 0 {
        h(5mm) + text(size: 9pt, fill: muted)[#line.label]
      } else if line.bold {
        text(weight: "bold")[#line.label]
      } else {
        [#line.label]
      }
      let amt = if line.indent > 0 {
        text(size: 9pt, fill: muted)[#line.amount]
      } else if line.bold {
        text(weight: "bold")[#line.amount]
      } else {
        [#line.amount]
      }
      (
        ..if line.rule { (table.hline(stroke: 0.7pt + accent),) } else { () },
        text(size: 8pt, fill: muted)[#line.code], lbl, amt,
      )
    }
  )
  if "note" in s and s.note != "" {
    v(3mm)
    text(size: 8pt, fill: muted)[#s.note]
  }
}

#statement(d.bilanz)
#pagebreak()
#statement(d.erfolg)
