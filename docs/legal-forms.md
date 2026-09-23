# Legal forms

Pick the legal form when you scaffold the books. It decides the account
namespace and the equity block. The rest of the chart (VAT, receivables,
income, expenses) is the same for every form.

!!! abstract "Applies if"
    - **Legal form:** an Einzelunternehmen (sole proprietorship, freelancer),
      a GmbH or an AG.[^forms] These are the three families the Swiss KMU
      Kontenrahmen prints an equity variant for.[^kmu]
    - **VAT status:** any. The legal form and the VAT registration are
      independent: an Einzelfirma can be registered, and a GmbH can be below
      the threshold.
    - **Not covered:** Kollektiv- and Kommanditgesellschaft (per-partner
      capital blocks), Genossenschaft, Verein, Stiftung.

| `--legal-form` | Entity | Account namespace |
|---|---|---|
| `einzelfirma` | Einzelunternehmen — sole proprietorship, freelancer | `:CH:Einzelfirma:` |
| `gmbh` | GmbH | `:CH:GmbH:` |
| `ag` | AG | `:CH:AG:` |

`quints init` rejects the other forms instead of scaffolding them wrongly.

## What changes: Klasse 28 (equity)

The KMU chart is identical across legal forms except the equity class. quints
scaffolds the official variant:

=== "Einzelfirma"

    ```beancount
    2026-01-01 open Equity:CH:Einzelfirma:Capital CHF
      kmu: "2800"  ; Owner's equity
    2026-01-01 open Equity:CH:Einzelfirma:Contributions CHF
      kmu: "2820"  ; Capital contributions and withdrawals
    2026-01-01 open Equity:CH:Einzelfirma:Private CHF
      kmu: "2850"  ; Private account
    ```

    No share capital and no statutory reserves. Private withdrawals go
    through 2850. The owner is not an employee of their own business, so
    what you take out is not a salary expense: the profit is your
    income.[^owner]

=== "GmbH / AG"

    ```beancount
    2026-01-01 open Equity:CH:GmbH:Capital:Share CHF
      kmu: "2800"  ; Share capital
    ```

    2800 renders as *Stammkapital* (GmbH) or *Aktienkapital* (AG) on German
    statements. An owner-manager is employed by the company, so their salary
    is a Klasse-5 personnel expense, unlike in an Einzelfirma.

Everything else — VAT accounts, receivables, income and expense classes — is
the same chart with a different namespace.

## What follows from the form

- Every account name carries the namespace: `Assets:CH:Einzelfirma:Current:…`
- `quints.toml` records it: `legal_form` in `[entity]`, `entity_marker` and
  all account names in `[accounts]`.
- `main.bean` passes the marker to the validation plugin:
  `plugin "quints.plugins.kmu" ":CH:Einzelfirma:"`.
- The Bilanz labels the equity section correctly per form (Eigenkapital /
  Stammkapital / Aktienkapital), in English and German.

## Changing form later

There's no migration command. Rename the namespace in `accounts.bean`,
`books/*.bean`, and `quints.toml` together, and swap the Klasse-28 block for
the target form's variant. `quints check` tells you when you're done.

[^forms]: GmbH: Art. 772 ff. OR; AG: Art. 620 ff. OR; Kollektiv- and Kommanditgesellschaft: Art. 552 ff. and 594 ff. OR. An Einzelunternehmen with at least CHF 100'000 turnover must be entered in the commercial register (Art. 931 Abs. 1 OR). [SR 220](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_931).
[^kmu]: The Schweizer Kontenrahmen KMU, edition 2023, published by [SwissAccounting (formerly veb.ch)](https://swissaccounting.org/kontenrahmen). Klasse 28 is the equity class, with one variant per legal-form family.
[^owner]: The profit of an Einzelunternehmen is the owner's income from self-employment, taxed as such: Art. 18 Abs. 1 DBG, [SR 642.11](https://www.fedlex.admin.ch/eli/cc/1991/1184_1184_1184/de#art_18).
