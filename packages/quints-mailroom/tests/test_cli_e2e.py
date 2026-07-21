"""The real user steps, end to end: init → configure → run --once.

Uses the Maildir transports (stdlib, no network) and the ``command`` agent
backend, so everything except the model itself is the production code path.
"""

import json
import mailbox
import sys
from email.message import EmailMessage
from pathlib import Path

from typer.testing import CliRunner

from quints_mailroom.cli import app

runner = CliRunner()

# Stands in for the model: reads the prompt from stdin, replies on stdout.
AGENT = (
    "import sys; p = sys.stdin.read(); "
    "print('Booked into staging/. Prompt mentioned inbox:', 'inbox/' in p)"
)


def deliver(maildir_path: Path, sender: str = "ada@example.com") -> None:
    msg = EmailMessage()
    msg["From"] = f"Ada <{sender}>"
    msg["To"] = "books@firm.ch"
    msg["Subject"] = "Invoice ACME July"
    msg["Message-ID"] = "<e2e-1@example.com>"
    msg.set_content("Please book the attached invoice.")
    msg.add_attachment(b"%PDF-1.4 e2e", maintype="application", subtype="pdf", filename="acme.pdf")
    mailbox.Maildir(str(maildir_path), factory=None, create=True).add(msg)


def write_config(root: Path, books: Path) -> Path:
    cfg = root / "mailroom.toml"
    cfg.write_text(
        f"""
books = {json.dumps(str(books))}
from = "books@firm.ch"
allowed_senders = ["ada@example.com"]
inbound = "maildir"
outbound = "maildir"

[maildir]
inbox = "mail-in"
outbox = "mail-out"

[agent]
backend = "command"
command = [{json.dumps(sys.executable)}, "-c", {json.dumps(AGENT)}]
"""
    )
    return cfg


def test_run_once_end_to_end(tmp_path: Path) -> None:
    books = tmp_path / "books"
    books.mkdir()
    deliver(tmp_path / "mail-in")
    cfg = write_config(tmp_path, books)

    res = runner.invoke(app, ["run", "--once", "--config", str(cfg)])
    assert res.exit_code == 0, res.output
    assert "replied" in res.output

    # The attachment landed in the books inbox, quints-convention territory.
    assert (books / "inbox" / "acme.pdf").read_bytes() == b"%PDF-1.4 e2e"

    # The reply is in the outbox Maildir, threaded onto the original mail.
    out = list(mailbox.Maildir(str(tmp_path / "mail-out"), factory=None))
    assert len(out) == 1
    reply = out[0]
    assert reply["To"] == "ada@example.com"
    assert reply["Subject"] == "Re: Invoice ACME July"
    assert reply["In-Reply-To"] == "<e2e-1@example.com>"
    assert "<e2e-1@example.com>" in reply["References"]
    assert "Booked into staging/." in reply.get_payload()
    assert "Prompt mentioned inbox: True" in reply.get_payload()

    # Second pass: the ack (Maildir new/ → cur/ + S flag) hides the message
    # from fetch entirely — nothing to do, no second reply.
    res2 = runner.invoke(app, ["run", "--once", "--config", str(cfg)])
    assert res2.exit_code == 0, res2.output
    assert len(list(mailbox.Maildir(str(tmp_path / "mail-out"), factory=None))) == 1


def test_unknown_sender_gets_no_reply(tmp_path: Path) -> None:
    books = tmp_path / "books"
    books.mkdir()
    deliver(tmp_path / "mail-in", sender="mallory@evil.example")
    cfg = write_config(tmp_path, books)

    res = runner.invoke(app, ["run", "--once", "--config", str(cfg)])
    assert res.exit_code == 0, res.output
    assert "ignored" in res.output
    assert not (tmp_path / "mail-out").exists() or not list(
        mailbox.Maildir(str(tmp_path / "mail-out"), factory=None)
    )


def test_init_scaffolds_config(tmp_path: Path) -> None:
    res = runner.invoke(app, ["init", str(tmp_path)])
    assert res.exit_code == 0, res.output
    text = (tmp_path / "mailroom.toml").read_text()
    assert "allowed_senders" in text and "QUINTS_MAILROOM_IMAP_PASSWORD" in text
    # Refuses to clobber an existing config.
    res2 = runner.invoke(app, ["init", str(tmp_path)])
    assert res2.exit_code == 2
