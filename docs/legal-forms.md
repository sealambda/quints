# Legal forms

`quints init` supports the three legal-form families the official Swiss KMU
Kontenrahmen (veb.ch) defines equity variants for:

| `--legal-form` | Entity | Account namespace |
|---|---|---|
| `einzelfirma` | Einzelunternehmen — sole proprietorship, freelancer | `:CH:Einzelfirma:` |
| `gmbh` | GmbH | `:CH:GmbH:` |
| `ag` | AG | `:CH:AG:` |

Kollektiv- and Kommanditgesellschaften (per-partner capital blocks) are not
supported yet — `quints init` rejects them instead of mis-scaffolding.

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

    No share capital, no statutory reserves. Private withdrawals go through
    2850 — your "salary" is legally not an expense; profit is your
    compensation.

=== "GmbH / AG"

    ```beancount
    2026-01-01 open Equity:CH:GmbH:Capital:Share CHF
      kmu: "2800"  ; Share capital
    ```

    2800 renders as *Stammkapital* (GmbH) or *Aktienkapital* (AG) on German
    statements. Owner-managers are employees — their salary is a Klasse-5
    expense, unlike an Einzelfirma.

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
