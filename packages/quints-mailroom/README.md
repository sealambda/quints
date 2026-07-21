# quints-mailroom

Your books have an email address.

`quints-mailroom` watches a mailbox. Every email from an allowed sender
becomes one agent run inside your bookkeeping repository: attachments are
saved to `inbox/`, a coding agent (Claude Agent SDK by default) reads them,
drafts bookings into `staging/`, validates with `quints check`, and its final
answer is emailed back — threaded onto the original message. Questions
("why is Ziffer 382 so high?") work the same way, minus the attachments.

This is the short-term entry point for people who will never open a
terminal: forwarding an invoice is something every accountant and founder
already does daily. The long-term entry point (a web portal with a review
queue) replaces the *surface*, not the pipeline — email stays as an
ingestion channel.

## How it works

```
mailbox ──fetch──▶ allowlist gate ──▶ inbox/<attachment>
                                        │
                                        ▼
                              agent run (cwd = books repo)
                              drafts → staging/, `quints check`
                                        │
mailbox ◀──reply (same thread)──────────┘
```

Nothing books directly: the agent proposes into `staging/`, `quints`
verifies, a human reviews the diff. The mailroom never parses invoices or
decides bookings itself — it moves mail and runs the agent
(the deterministic boundary lives in `quints`, not here).

## Design decisions

- **Standards only, no vendor APIs.** Inbound is IMAP (`imap-tools`) or a
  local **Maildir** (stdlib `mailbox`) — Maildir is what postfix/dovecot
  deliver to, so running your own MX needs zero extra code here. Outbound is
  SMTP (stdlib `smtplib`) or a Maildir outbox. Transports sit behind two
  small protocols; JMAP is a planned third backend on the same seam.
- **The agent is swappable.** `backend = "claude"` uses the Claude Agent SDK
  headless (loads the books repo's CLAUDE.md/AGENTS.md, pre-approved tool
  list: read/edit/`quints`/read-only `git` — nothing that leaves the repo).
  `backend = "command"` runs any executable with the prompt on stdin and the
  reply on stdout (`claude -p`, codex, a shell script, the test suite).
- **At-least-once, idempotent.** A message is acknowledged (IMAP `\Seen`,
  Maildir `new/`→`cur/`) only after the reply is sent, and every processed
  Message-ID is recorded in `<books>/.mailroom/processed`. A crash retries;
  a redelivery is skipped.
- **Untrusted input is treated as such.** Only `allowed_senders` get an
  agent run at all — everyone else is dropped without a reply (no
  backscatter). The email body is fenced as data in the prompt; attachment
  filenames are sanitised to bare basenames; email content never reaches any
  argv. Prompt injection can still steer the *proposal* — it cannot skip
  `staging/`, `quints check`, or your review of the diff.
- **Async by nature.** An agent run takes a minute or three. Email makes
  that latency invisible — nobody stares at a spinner in their inbox.

## Quick start

```bash
uv tool install quints-mailroom
quints-mailroom init          # writes a commented mailroom.toml
# edit mailroom.toml: books repo path, mailbox, allowed senders
export QUINTS_MAILROOM_IMAP_PASSWORD=...
export QUINTS_MAILROOM_SMTP_PASSWORD=...
quints-mailroom run           # poll loop; --once for a single pass
```

`mailroom.toml`, minimal IMAP + SMTP example:

```toml
books = "~/bookkeeping"
from = "books@firm.ch"
allowed_senders = ["you@firm.ch", "@firm.ch"]   # "@domain" allows a domain

inbound = "imap"
outbound = "smtp"

[imap]
host = "imap.migadu.com"
user = "books@firm.ch"

[smtp]
host = "smtp.migadu.com"
port = 587
user = "books@firm.ch"

[agent]
backend = "claude"
```

Own-mailserver / local setup: set `inbound = "maildir"` with
`[maildir] inbox = "/var/mail/books"` and let your MTA deliver there.

## License

**AGPL-3.0-only** — unlike the rest of the monorepo. The split is deliberate
and stable:

- `quints` and the building blocks link beancount (GPL-2.0-only) as a
  library, so they are GPL-2.0-only and stay that way.
- The mailroom drives `quints` strictly out-of-process (the agent runs the
  CLI; import-linter forbids a library import), so it is a separate work —
  and as the server-side piece of a hosted product, AGPL: run it, self-host
  it, fork it, but a hosted derivative must publish its source.

Commercial / white-label licensing outside AGPL terms is available — talk
to Sealambda.

## Status & roadmap

Early. Planned: JMAP transport, an `aiosmtpd`-based built-in MX mode,
per-sender routing to multiple books repos, and the web portal that will
sit on the same pipeline. Not yet published to PyPI — run it from this repo.
