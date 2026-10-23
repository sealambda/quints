# Email your books

`quints-mailroom` watches a mailbox. Every email from an allowed sender
becomes one agent run inside your bookkeeping repository: attachments are
saved to `inbox/`, the agent drafts bookings into `staging/` and validates
them with `quints check`, and its answer is emailed back — threaded onto
your message. Questions ("why is Ziffer 382 so high?") work the same way,
minus the attachments.

Nothing books directly. The agent proposes into `staging/`, `quints`
verifies, you review — the mailroom only moves mail and runs the agent.

It speaks standard protocols only: IMAP in, SMTP out, or a local Maildir on
either side (what postfix/dovecot deliver into). No vendor APIs, so any
mailbox host works — and the common setups below need no new infrastructure
at all.

Until the first PyPI release, install from a monorepo checkout:

<!-- no-test: installs dependencies from the network -->
```bash
uv tool install ./packages/quints-mailroom
```

## Try it locally — no mailbox, no credentials

The Maildir transports and the `command` agent backend run the entire
pipeline offline. `cat` stands in for the agent — the reply is simply the
prompt — so you can watch a message flow end to end before touching a real
mailbox. This exact sequence runs in CI; if it is on this page, it works.

```bash
mkdir -p demo/books
cd demo
cat > mailroom.toml <<'EOF'
books = "books"
from = "books@example.com"
allowed_senders = ["you@example.com"]

inbound = "maildir"
outbound = "maildir"

[maildir]
inbox = "mail-in"
outbox = "mail-out"

[auth]
# Hand-delivered test mail has no receiving server to vouch for the sender.
# Never switch this off for a real mailbox.
require_authentication = false

[agent]
backend = "command"
command = ["cat"]   # echoes the prompt back as the reply
EOF
```

Deliver a message into the inbound Maildir — exactly what a mailserver
would do:

```bash
python3 - <<'EOF'
import mailbox
from email.message import EmailMessage

msg = EmailMessage()
msg["From"] = "you@example.com"
msg["To"] = "books@example.com"
msg["Subject"] = "Invoice ACME July"
msg["Message-ID"] = "<demo-1@example.com>"
msg.set_content("Please book the attached invoice.")
msg.add_attachment(b"%PDF-1.4 demo", maintype="application",
                   subtype="pdf", filename="acme.pdf")
mailbox.Maildir("mail-in", create=True).add(msg)
EOF
```

One pass over the mailbox:

```bash
quints-mailroom run --once
ls books/inbox    # acme.pdf, saved for the agent
ls mail-out/new   # the reply, threaded onto the original message
```

The reply carries `In-Reply-To` and `References`, so a mail client shows it
in the same thread. For the real thing, point `books` at an actual quints
repository ([getting started](../getting-started.md)) and switch `[agent]`
to `backend = "claude"` — the Claude Agent SDK, headless, working directory
pinned to the books repo, with a pre-approved tool list (read, edit,
`quints`, read-only `git`) that never leaves it. File writes outside
`staging/` and `inbox/` are refused in code, whatever the email says. A run
that takes longer than `agent.timeout_seconds` (default 900) is an error,
and the message is retried on the next pass.

## Connect a real mailbox

The recommended shape is one dedicated mailbox that the mailroom owns, on
any IMAP host:

```toml
books = "~/bookkeeping"
from = "books@firm.ch"
allowed_senders = ["you@firm.ch", "partner@firm.ch"]

inbound = "imap"
outbound = "smtp"

[imap]
host = "imap.migadu.com"
user = "books@firm.ch"

[smtp]
host = "smtp.migadu.com"
port = 587
user = "books@firm.ch"

[auth]
authserv_id = "mx.example.net"   # replace: see "Authenticate senders" below

[agent]
backend = "claude"
```

Secrets stay out of the file:

<!-- no-test: needs real credentials -->
```bash
export QUINTS_MAILROOM_IMAP_PASSWORD=...
export QUINTS_MAILROOM_SMTP_PASSWORD=...
quints-mailroom run   # poll loop, every 60s by default (--interval)
```

Why dedicated? The mailroom acknowledges a message by marking it read
(IMAP `\Seen`) and only ever fetches unread mail. In a mailbox humans also
read, anything you open before the next poll becomes invisible to the
mailroom — and its acks mark mail as read under you. One mailbox, one
owner.

## Authenticate senders

Anyone can write any address into `From:`. The mailroom only trusts the
verdict your receiving mail server records: an `Authentication-Results`
header showing `dmarc=pass` for the sender's domain, or `dkim=pass` signed
by it. Mail from an allowed address without that verdict is treated like
mail from a stranger: recorded, no agent run, no reply.

The header names the server that wrote it (its *authserv-id*), and only
the topmost one carrying the id you configure counts — a sender can add
their own headers, with any name, further down. To find yours, open a message the mailbox received, show its
raw source (Gmail: *Show original*), and take the first token of the
topmost `Authentication-Results` line:

```text
Authentication-Results: mx.google.com;
       dkim=pass header.i=@firm.ch header.s=google;
       dmarc=pass (p=REJECT) header.from=firm.ch
```

Here it is `mx.google.com` — the value for any Gmail or Google Workspace
mailbox. The live check below prints these headers too.

## When the address is a Google Group

A common starting point: `payables@firm.ch` is a Google Group (perhaps
with `receivables@firm.ch` as a group alias), because that was the
zero-cost way to get a shared address. A group is a distribution list, not
a mailbox — there is nothing to log into, so no IMAP endpoint exists for
the group itself. The group fans each message out to its members' real
mailboxes, and a real mailbox is where the mailroom connects. You do not
need to move the address or host anything new; pick one of two routes.

### Route 1 — a dedicated mailbox as a group member (recommended)

Create a real mailbox for the books and add it to the group as one more
member:

- a new Workspace user (`books@firm.ch` — costs a seat), or
- a mailbox on any other host (Migadu, mailbox.org, Fastmail, …) added as
  an **external member** — group settings must allow external members,
  which a Workspace admin can enable per group.

Mail to `payables@` and `receivables@` keeps flowing to every human member
exactly as before; the mailroom gets a mailbox it fully owns. The
configuration is the dedicated-mailbox one above. Two details:

- Groups may rewrite `From:` to the group address ("Ada via payables"),
  depending on the sender's DMARC policy. Check one delivered message. If
  it was rewritten, the mailroom sees the group as the sender, and allowing
  it allows everyone who can post to the group — restrict posting first.
- Leave the group's subject prefix and footer options off (they are by
  default) so subjects, threading, and attachments arrive unmodified. A
  modified message also breaks the sender's DKIM signature, and with it
  the authentication check.

### Route 2 — a label in a member's Gmail

No new mailbox: the group already delivers into your own Gmail account.
Fence the mailroom into a label — Gmail exposes every label as an IMAP
folder:

1. Gmail → Settings → *Filters and blocked addresses* → new filter
   matching `to:(payables@firm.ch OR receivables@firm.ch)`; apply a label
   `mailroom` and tick *Skip the Inbox*, so you and the mailroom stop
   sharing read state.
2. Create an [app password](https://myaccount.google.com/apppasswords)
   (requires 2-Step Verification; if the page is missing, a Workspace
   admin has app passwords switched off).
3. Point the mailroom at the label:

```toml
books = "~/bookkeeping"
from = "you@firm.ch"   # the account itself, or a configured send-as alias
allowed_senders = ["partner@firm.ch", "trustee@treuhand.ch"]

inbound = "imap"
outbound = "smtp"

[imap]
host = "imap.gmail.com"
user = "you@firm.ch"
folder = "mailroom"    # the Gmail label, exposed as an IMAP folder

[smtp]
host = "smtp.gmail.com"
port = 587
user = "you@firm.ch"

[auth]
authserv_id = "mx.google.com"

[agent]
backend = "claude"
```

Set both password environment variables to the app password. Three
Gmail-specific caveats:

- **Read state is shared with you.** The `\Seen` ack means a message you
  open under the `mailroom` label before the poll is never processed.
  *Skip the Inbox* keeps group mail out of your way — then leave that
  label unread.
- **Gmail deduplicates your own posts.** A message you send to the group
  from this same account keeps only the copy in Sent — it may never appear
  as new mail under the label. Send tests from a different address.
- **`from` must be an identity Gmail accepts.** The account address always
  works; the group address works only after you add it under *Send mail
  as* — otherwise Gmail rewrites the header on the way out.

This route is fine for a pilot. Once more than one person forwards
invoices, move to route 1 — the shared-mailbox caveats stop being worth
it.

## Your own mailserver

You never need one — a small hosted mailbox does everything above. If you
already run postfix/dovecot, or want mail to stay on your hardware, skip
IMAP entirely: have the MTA deliver into a Maildir and the mailroom reads
it in place.

```toml
books = "~/bookkeeping"
from = "books@firm.ch"
allowed_senders = ["@firm.ch"]

inbound = "maildir"
outbound = "smtp"

[maildir]
inbox = "/var/mail/books"

[smtp]
host = "mail.firm.ch"
port = 587
user = "books@firm.ch"

[auth]
authserv_id = "mail.firm.ch"   # what your DMARC milter stamps (OpenDMARC: AuthservID)

[agent]
backend = "claude"
```

Your server must check DKIM and DMARC (OpenDMARC, rspamd) and stamp
`Authentication-Results` on every message it accepts, removing any
incoming header that already claims its authserv-id.[^rfc8601] Otherwise a
sender can write that header themselves, and the mailroom trusts it.

A Google Group can still front this: add the server's mailbox address as an
external group member and the group forwards there — receiving stays on
Google, processing on your machine.

## Best practices

- **Allowlist tightly.** `allowed_senders` matches the `From:` header,
  and [authentication](#authenticate-senders) proves the sender's domain,
  not the person. List specific people; use `"@firm.ch"` only for a domain
  whose every mailbox you trust. Unknown and unauthenticated senders are
  dropped without a reply — no backscatter, no agent run.
- **Oversized mail is rejected, not truncated.** More than 20 attachments,
  more than 25 MiB of attachments, or a body over 50,000 characters: the
  message is recorded as `rejected`, never reaches the agent, and gets no
  reply; `run --once` exits 1. Each pass takes at most 50 messages; the
  rest wait for the next pass.
- **The agent proposes, you dispose.** Drafts land in `staging/` and are
  validated by `quints check`; review them before anything moves into
  `books/` — the same flow as [statement imports](importing.md). A
  malicious email can steer what the agent *proposes*; it cannot skip
  `staging/`, the checks, or your review.
- **Secrets live in the environment**, never in `mailroom.toml` — the file
  itself is safe to commit next to the books.
- **One mailroom per mailbox.** Acknowledgement is read-state, so two
  concurrent pollers would both process the same unread mail. The
  `.mailroom/processed` file in the books repo is the idempotency backstop
  across restarts; add `.mailroom/` to the books repo's `.gitignore` — it
  is runner state, not books.
- **Email is async — poll gently.** An agent run takes a minute or three
  and nobody stares at a spinner in their inbox; the default 60-second
  interval is plenty.

## Verify a live setup

Everything on this page up to the mailbox boundary is tested in CI: the
local demo runs verbatim, and every `mailroom.toml` example must load
against the real config parser. What CI cannot reach is *your* mailbox —
credentials, the label-as-folder mapping, group fan-out. There is an
opt-in live check for exactly that: it logs in, verifies the folder exists
(printing the mailbox's actual folder list when it does not), and lists
unseen mail with its `Authentication-Results` headers **without
acknowledging anything**, so running it never hides a message from the
mailroom.

<!-- no-test: needs real credentials -->
```bash
QUINTS_MAILROOM_LIVE_IMAP_HOST=imap.gmail.com \
QUINTS_MAILROOM_LIVE_IMAP_USER=you@firm.ch \
QUINTS_MAILROOM_LIVE_IMAP_FOLDER=mailroom \
QUINTS_MAILROOM_IMAP_PASSWORD=the-app-password \
uv run pytest packages/quints-mailroom/tests/test_live_imap.py -s
```

Then do one supervised pass: send a test email from an allowed sender —
not from the mailroom's own account, see the dedupe caveat above — and run

<!-- no-test: needs real credentials -->
```bash
quints-mailroom run --once
```

Read the reply in the thread and the drafts in `staging/`, and only then
leave `quints-mailroom run` unattended.

[^rfc8601]: RFC 8601 *Message Header Field for Indicating Message Authentication Status*, [§5 Removing Existing Header Fields](https://www.rfc-editor.org/rfc/rfc8601#section-5): an MTA "MUST delete any discovered instance of this header field that claims, by virtue of its authentication service identifier, to have been added within its trust boundary but that did not come directly from another trusted MTA."
