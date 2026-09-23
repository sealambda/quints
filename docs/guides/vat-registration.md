# Register, switch method, deregister

A company's VAT situation is not fixed. It usually starts below the
registration threshold, registers when it crosses it (or earlier, by choice),
may switch between the effective and the Saldosteuersatz method, may move to
annual filing, and eventually deregisters. You record each change in
`quints.toml` once. quints then computes every period under the rules that
applied to it, and refuses a change the law doesn't allow.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma, GmbH or AG. The rules are the same for all
      three.
    - **VAT status:** every stage. This page is the lifecycle, and each section
      opens with the stage it covers.
    - **Not covered:** the Pauschalsteuersatz method, which is for public
      bodies and associations only,[^pauschal] and group taxation.

## 1. Check whether you must register

```bash
quints vat liability --at 2026-12-31
```

It totals each year's turnover the way the threshold counts it,[^threshold]
converts a partial first year to a full one,[^annualise] and says what
follows. Leave out `--at` to assess as of today; the running year is then
marked *on course* rather than decided.

What counts:

- **Worldwide turnover.** Exports and supplies abroad count, not only
  domestic sales.
- **Net of VAT.**
- **Supplies exempt under Art. 21 MWSTG don't count** (Ziffer 230, the
  `:Exempt` accounts), and neither do subsidies or donations.

quints classifies each booking the same way `quints vat report` does.

The threshold is **CHF 100'000 a year**. Once a business year reaches it, you
are liable from the start of the next year and must register with the ESTV
within 30 days.[^start] `vat liability` prints both dates.

!!! warning "The part quints cannot compute"
    A **new** business that expects to pass CHF 100'000 within its first 12
    months is liable from its first day, not from the next year.[^new] If you
    can't tell yet, reassess after three months at the latest. That is an
    expectation, not a booking, so the decision is yours. Record what you
    decided (step 2).

Registering below the threshold is allowed. The waiver of the exemption
holds for at least one tax period.[^waiver] It can pay off when you have large
input VAT to recover, such as a start-up investing before its first sale.

## 2. Record the registration

Register with the ESTV online.[^register] It issues the VAT number
(`CHE-… MWST`). Put the date liability starts into `quints.toml`:

```toml
[entity]
vat_registered_since = 2026-04-01
vat_method = "effective"      # or "saldo" — see step 3
```

Before that date, a business that is not registered:

- **Books everything gross.** No InputVAT, OutputVAT or Bezugsteuer
  postings.
- **Invoices without VAT.** No VAT row and no VAT number: an invoice that
  shows VAT makes it owed.[^art27] `quints invoice` knows the issue date and
  leaves both out.
- **Owes Bezugsteuer only above CHF 10'000.** That limit is for services
  bought from abroad in a calendar year; above it, the business must register
  for them.[^bezug]

A project scaffolded before registering says so:

```bash
quints init jane-books --name "Jane Doe" --legal-form einzelfirma --vat-registered-since no --samples --yes
cd jane-books
quints vat liability --at 2026-12-31
quints invoice invoicing/acme-2026-07.yaml
cd ..
```

Its `quints.toml` carries `vat_registered = false`. When you register,
replace that line with the two above.

**The first return** runs from the registration date to the end of that
period. `quints vat report` clamps the period to it and prints a notice.
Under the effective method, you may recover input VAT on goods and assets
still on hand that you bought for taxable use: the *Einlageentsteuerung*,
Ziffer 410.[^einlage] Book it against InputVAT tagged
`mwst: "einlageentsteuerung"`. Services already consumed (consulting,
advertising) don't qualify.

## 3. Choose the method

| | Effective method | Saldosteuersatz |
|---|---|---|
| You pay | output VAT − input VAT | gross turnover × the rate the ESTV granted |
| Input VAT | deducted | never deducted: the rate already includes it |
| Filing | quarterly | half-yearly |
| Allowed up to | no limit | CHF 5.024 m turnover and CHF 108'000 tax a year[^sss-limits] |
| Bookkeeping | every purchase split into net + InputVAT | purchases booked gross |

Your invoices are the same either way: customers pay the statutory 8.1 % /
2.6 % / 3.8 %.

The Saldosteuersatz has to be requested within 60 days of receiving the VAT
number. If you don't, you stay on the effective method for three whole tax
periods.[^sss-apply] [File your VAT return](vat.md) shows both methods step
by step.

## 4. Switch method or filing period

A switch always starts on **1 January**, the start of a tax period, and has
to be notified to the ESTV within 60 days of that date. The minimum stays
are:[^switch]

- **Effective method:** at least three years before a switch to the
  Saldosteuersatz.
- **Saldosteuersatz:** at least one tax period before going back.

Record the switch as a dated entry. The fields at the top of `quints.toml`
stay the *first* phase:

```toml
[entity]
vat_registered_since = 2023-01-01
vat_method = "effective"

[[vat.change]]
from = 2027-01-01
method = "saldo"
saldo = [{ rate = 6.2 }]
```

Each `[[vat.change]]` inherits what it doesn't name. A change of method
resets the filing period to that method's default and drops the old
Saldosteuersätze. quints rejects a `from` that isn't a 1 January, a change
listed out of order, and a switch before the minimum stay has passed, and it
names the earliest legal date.

Every return is then computed under the phase in force for its period, and a
period that spans a switch is refused. The switch itself needs one
correction, in a specific return:

- **Effective → Saldosteuersatz:** in the *last effective* return, repay the
  input VAT deducted on goods and assets still on hand, at their current
  value. That is Ziffer 415, tagged `mwst: "vorsteuerkorrektur"`.[^to-sss]
- **Saldosteuersatz → effective:** in the *first effective* return, deduct
  the input VAT on goods and assets still on hand. That is Ziffer 410, tagged
  `mwst: "einlageentsteuerung"`.[^to-eff]

`quints vat report` prints the matching notice on both returns. The amounts
are valuations of what you still own, so you work them out. quints tells you
where they go.

**Annual filing** is a change of period, not of method. It is available on
request up to CHF 5.005 m turnover,[^annual] and you pay instalments during
the year:

```toml
[[vat.change]]
from = 2027-01-01
period = "year"
```

## 5. Deregister

Liability ends when the business stops trading. It can also end at the end
of a tax period in which turnover stayed below the threshold, provided you
don't expect to reach it in the next one.[^end] `quints vat liability` tells
a registered business when it may deregister, and by when the ESTV must be
told:

- **Below the threshold:** within 60 days of the tax period's end. Staying
  registered counts as waiving the exemption.
- **When you stop trading:** within 30 days of stopping.

Record the last day of liability:

```toml
[entity]
vat_registered_until = 2030-12-31
```

**The final return** runs to that date and is due within 60 days.[^final]
Under the effective method, goods and assets still on hand on which you
deducted input VAT are *Eigenverbrauch*: book the correction as Ziffer 415,
tagged `mwst: "vorsteuerkorrektur"`.[^eigen] Under the Saldosteuersatz there
is no such correction; the turnover up to the end is taxed at the granted
rates.[^sss-end] From the day after, invoices carry no VAT again.

## What quints doesn't do here

- **Register, switch or deregister with the ESTV.** That happens online at
  the ESTV. quints records the result.
- **Value what you still own.** The Einlageentsteuerung and Eigenverbrauch
  amounts depend on the current value of goods and assets (the purchase
  value less a fifth for each year of use, for movables). quints names the
  correction, its Ziffer and its tag, and the amount is yours to book.
- **Decide on expectations.** "Will we pass CHF 100'000 in our first year?"
  and "will we stay below it next year?" are judgements. quints computes the
  past and says which question to ask.
- **A business year that is not the calendar year.** quints assesses
  calendar years, like the books (one file per year).
- **Know when trading really started.** `vat liability` counts from the
  first booking. For books migrated from elsewhere, that is the opening
  balance, not the start of the business, so read a converted first year
  with that in mind.
- **Change the accounting basis** (vereinbart ↔ vereinnahmt). quints books
  on agreed Entgelte only.

[^register]: [ESTV, MWST-Pflicht abklären und MWST anmelden](https://www.estv.admin.ch/de/mwst-anmelden).
[^pauschal]: Art. 37 Abs. 5 MWSTG and Art. 97 MWSTV: "Gemeinwesen und verwandte Einrichtungen, namentlich private Spitäler und Schulen oder konzessionierte Transportunternehmungen, sowie Vereine und Stiftungen". [MWSTG, SR 641.20](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_37); [MWSTV, SR 641.201](https://www.fedlex.admin.ch/eli/cc/2009/828/de#art_97).
[^threshold]: Art. 10 Abs. 2 Bst. a MWSTG: exempt is who earns "innerhalb eines Jahres im In- und Ausland weniger als 100 000 Franken Umsatz aus Leistungen …, die nicht nach Artikel 21 Absatz 2 von der Steuer ausgenommen sind"; Abs. 2bis: "Der Umsatz berechnet sich nach den vereinbarten Entgelten ohne die Steuer." [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_10). What counts: [MWST-Info 02 *Steuerpflicht*, Ziff. 2.1.1](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/cipherDisplay.xhtml?publicationId=1010164&componentId=1010279) (Stand 01.01.2025).
[^annualise]: Art. 9 Abs. 3 MWSTV: "Wurde die … Tätigkeit nicht während eines ganzen Jahres ausgeübt, so ist der Umsatz auf ein volles Jahr umzurechnen." [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/828/de#art_9). The ESTV converts by months: 1 March to 31 December with CHF 85'000 is CHF 102'000 for a full year — [MWST-Info 02, Ziff. 5.3](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/cipherDisplay.xhtml?publicationId=1010164&componentId=1010319). quints reproduces that example in its tests.
[^start]: Art. 9 Abs. 3 MWSTV: the exemption ends "nach Ablauf des Geschäftsjahres, in dem die Umsatzgrenze erreicht wird". Art. 66 Abs. 1 MWSTG: register "innert 30 Tagen nach Beginn ihrer Steuerpflicht". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_66).
[^new]: Art. 14 Abs. 3 MWSTG ("absehbar ist, dass diese Grenze innerhalb von 12 Monaten nach der Aufnahme … überschritten wird"); Art. 9 Abs. 1–2 MWSTV (reassessment "spätestens nach drei Monaten"). [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_14).
[^waiver]: Art. 11 MWSTG: the right "auf die Befreiung von der Steuerpflicht zu verzichten", held "mindestens während einer Steuerperiode". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_11); [MWST-Info 02, Ziff. 3](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1010164).
[^art27]: Art. 27 Abs. 1 MWSTG: "Wer nicht im Register der steuerpflichtigen Personen eingetragen ist …, darf in Rechnungen nicht auf die Steuer hinweisen." Abs. 2: who shows a tax without being entitled to "schuldet die ausgewiesene Steuer". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_27).
[^bezug]: Art. 45 Abs. 2 Bst. b MWSTG. [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_45).
[^einlage]: Art. 32 MWSTG ("Treten die Voraussetzungen des Vorsteuerabzugs nachträglich ein (Einlageentsteuerung) …"); Art. 72–74 MWSTV, including the services presumed already consumed. [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_32). Under the Saldosteuersatz there is none: Art. 78 Abs. 5 MWSTV.
[^sss-limits]: Art. 37 Abs. 1 MWSTG: "nicht mehr als 5 024 000 Franken Umsatz aus steuerbaren Leistungen … und … nicht mehr als 108 000 Franken Steuern". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_37); [MWST-Info 12 *Saldosteuersätze*](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1004992), Ziff. 1.3.
[^sss-apply]: Art. 78 Abs. 1 and 3 MWSTV: notice "innert 60 Tagen nach Zustellung der Mehrwertsteuernummer", otherwise "mindestens drei ganze Steuerperioden nach der effektiven Abrechnungsmethode". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/828/de#art_78).
[^switch]: Art. 37 Abs. 4 MWSTG: the Saldosteuersatz "muss während mindestens einer Steuerperiode beibehalten werden. Entscheidet sich die steuerpflichtige Person für die effektive Abrechnungsmethode, so kann sie frühestens nach drei Jahren zur Saldosteuersatzmethode wechseln. Wechsel sind jeweils auf Beginn einer Steuerperiode möglich." The tax period is the calendar year (Art. 34 Abs. 2). Notice within 60 days of the period's start: Art. 79 Abs. 1 and 81 Abs. 1 MWSTV.
[^to-sss]: Art. 79 Abs. 3 MWSTV: the input VAT "an die ESTV zurückzuerstatten. Die Deklaration hat in der letzten Abrechnungsperiode vor dem Wechsel zu erfolgen." [MWST-Info 12, Ziff. 2.2.3](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/cipherDisplay.xhtml?publicationId=1004992&componentId=1005207) puts it under Ziffer 415.
[^to-eff]: Art. 81 Abs. 4 MWSTV: the tax "kann in der ersten Abrechnungsperiode nach dem Wechsel als Vorsteuer abgezogen werden". [MWST-Info 12, Ziff. 3.2.3](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/cipherDisplay.xhtml?publicationId=1004992&componentId=1005242) puts it under Ziffer 410.
[^annual]: Art. 35 Abs. 1bis Bst. b MWSTG ("bei einem Umsatz von nicht mehr als 5 005 000 Franken pro Jahr aus steuerbaren Leistungen: jährlich"); conditions in Art. 35a MWSTG and Art. 76a–76c MWSTV; instalments in Art. 86a MWSTG. [MWST-Info 15](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1013189), Ziff. 2.1.1.
[^end]: Art. 14 Abs. 2 and 5 MWSTG: deregistration "frühestens möglich auf das Ende der Steuerperiode, in der der massgebende Umsatz nicht erreicht worden ist"; "Die Nichtabmeldung gilt als Verzicht auf die Befreiung". Art. 66 Abs. 2: deregister within 30 days of ceasing business. 60 days after the period: [MWST-Info 02, Ziff. 6.2](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/cipherDisplay.xhtml?publicationId=1010164&componentId=1010339).
[^final]: Art. 71 Abs. 2 MWSTG: "Endet die Steuerpflicht, so läuft die Frist von diesem Zeitpunkt an." [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_71).
[^eigen]: Art. 31 Abs. 2 Bst. d MWSTG: goods and services that "sich bei Wegfall der Steuerpflicht noch in ihrer Verfügungsmacht befinden"; the current value under Abs. 3 and Art. 69–71 MWSTV. [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_31).
[^sss-end]: Art. 82 Abs. 1 MWSTV; MWST-Info 12, Ziff. 3.1: "Korrekturen auf den … verbleibenden Gegenständen … erfolgen keine".
