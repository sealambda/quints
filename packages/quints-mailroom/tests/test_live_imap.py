"""Opt-in live IMAP check — verify a real mailbox setup with real credentials.

CI never runs this: it skips unless ``QUINTS_MAILROOM_LIVE_IMAP_HOST`` is
set. It exists so a mailbox setup from docs/guides/mailroom.md (dedicated
mailbox, Gmail label, own server) can be verified before pointing
``quints-mailroom run`` at it:

    QUINTS_MAILROOM_LIVE_IMAP_HOST=imap.gmail.com \\
    QUINTS_MAILROOM_LIVE_IMAP_USER=you@firm.ch \\
    QUINTS_MAILROOM_LIVE_IMAP_FOLDER=mailroom \\
    QUINTS_MAILROOM_IMAP_PASSWORD=... \\
    uv run pytest packages/quints-mailroom/tests/test_live_imap.py -s

It logs in, asserts the configured folder exists (the failure message lists
the folders the mailbox actually exposes — for Gmail, labels show up here),
and fetches unseen mail through the production ``ImapInbound`` WITHOUT
acknowledging anything, so running it never hides a message from the
mailroom.
"""

from __future__ import annotations

import os

import pytest
from imap_tools import MailBox

from quints_mailroom.config import IMAP_PASSWORD_ENV, ImapConfig
from quints_mailroom.transport import ImapInbound

HOST = os.environ.get("QUINTS_MAILROOM_LIVE_IMAP_HOST", "")

pytestmark = pytest.mark.skipif(
    not HOST, reason="live mailbox check — set QUINTS_MAILROOM_LIVE_IMAP_HOST to run"
)


def test_login_folder_and_unseen_fetch() -> None:
    user = os.environ["QUINTS_MAILROOM_LIVE_IMAP_USER"]
    folder = os.environ.get("QUINTS_MAILROOM_LIVE_IMAP_FOLDER", "INBOX")
    password = os.environ[IMAP_PASSWORD_ENV]

    with MailBox(HOST).login(user, password) as box:
        folders = [f.name for f in box.folder.list()]
    assert folder in folders, f"folder {folder!r} not found on {HOST} — mailbox exposes: {folders}"

    inbound = ImapInbound(ImapConfig(host=HOST, user=user, folder=folder), password)
    messages = inbound.fetch()  # searches UNSEEN; never sets \Seen
    print(f"\n{HOST} {folder!r}: {len(messages)} unseen message(s)")
    for m in messages:
        print(f"  {m.sender!r}  {m.subject!r}  ({len(m.attachments)} attachment(s))")
