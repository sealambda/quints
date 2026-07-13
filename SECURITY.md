# Security policy

`quints` runs locally against your own ledger; it has no server component. The
sensitive surfaces are the optional importer API clients (Wise, Stripe), which
read credentials from environment variables / `.env` and never write them to
the ledger.

## Reporting a vulnerability

Please report privately — do **not** open a public issue for a security
problem. Use GitHub's **Report a vulnerability** button under the repository's
Security tab (private advisory), and we'll respond as soon as we can.

Helpful to include: affected version/commit, reproduction steps, and impact.

This is an early-stage project maintained on a best-effort basis; there is no
formal SLA yet.
