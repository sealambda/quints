"""Inbound transports: per-pass limits and Authentication-Results, no network.

IMAP runs through the production :class:`ImapInbound` over imap-tools'
own ``MailMessage`` parser; only the server connection is faked.
"""

from __future__ import annotations

import mailbox
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from imap_tools import MailMessage

from quints_mailroom.auth import sender_authenticated
from quints_mailroom.config import ImapConfig
from quints_mailroom.transport import ImapInbound, MaildirInbound

# Two hops, the trusted (topmost) one folded across lines as servers write it.
RAW = (
    b"Authentication-Results: mx.google.com;\r\n"
    b"       dkim=pass header.i=@example.com header.s=s1;\r\n"
    b"       dmarc=pass (p=REJECT) header.from=example.com\r\n"
    b"Authentication-Results: relay.example; none\r\n"
    b"From: Ada <ada@example.com>\r\n"
    b"Subject: Invoice\r\n"
    b"Message-ID: <imap-1@example.com>\r\n"
    b"\r\n"
    b"Please book.\r\n"
)


class FakeImapBox:
    def __init__(self, raws: list[bytes]) -> None:
        self.raws = raws
        self.limits: list[Any] = []

    def fetch(
        self, *_criteria: object, limit: int | None = None, **_kw: object
    ) -> Iterator[MailMessage]:
        self.limits.append(limit)
        for raw in self.raws[:limit]:
            yield MailMessage.from_bytes(raw)


def test_imap_carries_authentication_results_top_first(monkeypatch: pytest.MonkeyPatch) -> None:
    box = FakeImapBox([RAW])
    inbound = ImapInbound(ImapConfig(host="imap.example.com", user="books"), "pw")
    monkeypatch.setattr(inbound, "_mailbox", lambda: box)

    [msg] = inbound.fetch(limit=7)

    assert box.limits == [7]
    assert len(msg.auth_results) == 2
    assert msg.auth_results[0].startswith("mx.google.com")
    assert sender_authenticated(msg.sender, msg.auth_results, "mx.google.com")
    assert not sender_authenticated(msg.sender, msg.auth_results, "relay.example")


def test_maildir_fetch_returns_the_oldest_up_to_limit(tmp_path: Path) -> None:
    md = mailbox.Maildir(str(tmp_path / "in"), factory=None, create=True)
    for i in range(3):
        msg = EmailMessage()
        msg["From"] = "ada@example.com"
        msg["Message-ID"] = f"<md-{i}@example.com>"
        msg.set_content("x")
        md.add(msg)
    # Maildir keys start with the delivery time; make the order explicit.
    keys = sorted(md.keys())
    ids = [str(md[k]["Message-ID"]) for k in keys]

    fetched = MaildirInbound(tmp_path / "in").fetch(limit=2)
    assert [m.message_id for m in fetched] == ids[:2]
