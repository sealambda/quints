"""`quints-mailroom` — poll the mailbox, run the agent, reply."""

from __future__ import annotations

import os
import time
from pathlib import Path

import typer

from . import config as config_mod
from .agent import build_agent
from .pipeline import process_once
from .transport import (
    ImapInbound,
    InboundTransport,
    MaildirInbound,
    MaildirOutbound,
    OutboundTransport,
    SmtpOutbound,
)

app = typer.Typer(
    no_args_is_help=True,
    add_completion=True,
    help="quints-mailroom — your books have an email address.",
)


def _print_version(value: bool) -> None:
    if value:
        from . import __version__

        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        False, "--version", callback=_print_version, is_eager=True, help="Print version and exit."
    ),
) -> None:
    pass


def _password(env: str, needed_for: str) -> str:
    value = os.environ.get(env, "")
    if not value:
        typer.echo(f"error: {needed_for} needs the {env} environment variable", err=True)
        raise typer.Exit(2)
    return value


def _build_transports(cfg: config_mod.MailroomConfig) -> tuple[InboundTransport, OutboundTransport]:
    inbound: InboundTransport
    outbound: OutboundTransport
    # load() guarantees the section for the selected backend exists; the
    # re-checks here narrow the Optionals without assert (S101).
    if cfg.inbound == "imap" and cfg.imap is not None:
        inbound = ImapInbound(cfg.imap, _password(config_mod.IMAP_PASSWORD_ENV, "IMAP"))
    elif cfg.maildir.inbox is not None:
        inbound = MaildirInbound(cfg.maildir.inbox)
    else:  # pragma: no cover — unreachable after load()
        raise config_mod.MailroomConfigError("no inbound transport configured")
    if cfg.outbound == "smtp" and cfg.smtp is not None:
        password = _password(config_mod.SMTP_PASSWORD_ENV, "SMTP") if cfg.smtp.user else ""
        outbound = SmtpOutbound(cfg.smtp, password)
    elif cfg.maildir.outbox is not None:
        outbound = MaildirOutbound(cfg.maildir.outbox)
    else:  # pragma: no cover — unreachable after load()
        raise config_mod.MailroomConfigError("no outbound transport configured")
    return inbound, outbound


@app.command()
def run(
    config: Path = typer.Option(
        Path("mailroom.toml"), "--config", "-c", help="Path to mailroom.toml."
    ),
    once: bool = typer.Option(False, "--once", help="One pass over the mailbox, then exit."),
    interval: int = typer.Option(60, "--interval", help="Seconds between polls."),
) -> None:
    """Watch the mailbox and answer each email with an agent run."""
    try:
        cfg = config_mod.load(config)
    except config_mod.MailroomConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    inbound, outbound = _build_transports(cfg)
    agent = build_agent(cfg.agent)
    had_error = False
    while True:
        for o in process_once(inbound, outbound, agent, cfg):
            typer.echo(f"{o.status:9}  {o.sender}  {o.subject!r}  {o.detail}")
            had_error = had_error or o.status == "error"
        if once:
            raise typer.Exit(1 if had_error else 0)
        time.sleep(interval)


TEMPLATE = """\
# quints-mailroom — your books have an email address.
# Secrets stay out of this file: set QUINTS_MAILROOM_IMAP_PASSWORD and
# QUINTS_MAILROOM_SMTP_PASSWORD in the environment.

books = "."                            # the bookkeeping repo (contains quints.toml)
from = "books@example.com"             # From: on replies
allowed_senders = ["you@example.com"]  # or "@example.com" for a whole domain

inbound = "imap"     # imap | maildir (maildir = own MX / local delivery)
outbound = "smtp"    # smtp | maildir (maildir = drop replies in a local outbox)

[imap]
host = "imap.example.com"
port = 993
user = "books@example.com"
folder = "INBOX"

[smtp]
host = "smtp.example.com"
port = 587
user = "books@example.com"
starttls = true

# [maildir]
# inbox = "/var/mail/books"   # when inbound = "maildir"
# outbox = "outbox"           # when outbound = "maildir"

[agent]
backend = "claude"   # claude (Agent SDK) | command (prompt on stdin, reply on stdout)
# model = "claude-sonnet-5"
# command = ["claude", "-p"]
# instructions = "Extra guidance appended to every prompt."
"""


@app.command()
def init(
    directory: Path = typer.Argument(Path("."), help="Where to write mailroom.toml."),
) -> None:
    """Scaffold a commented mailroom.toml."""
    target = directory / "mailroom.toml"
    if target.exists():
        typer.echo(f"error: {target} already exists", err=True)
        raise typer.Exit(2)
    directory.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE)
    typer.echo(f"wrote {target} — edit it, set the password env vars, then `quints-mailroom run`")


def main() -> None:
    app()
