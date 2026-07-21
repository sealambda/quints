"""Mail transports — standard protocols only, swappable behind two protocols.

Inbound: :class:`MaildirInbound` (stdlib ``mailbox``; what postfix/dovecot
deliver to when you run your own MX) or :class:`ImapInbound` (imap-tools;
any hosted mailbox). Outbound: :class:`SmtpOutbound` (stdlib ``smtplib``)
or :class:`MaildirOutbound` (drop into a local outbox for a relay to pick
up — also the no-network test path). JMAP is a planned third backend; the
protocols here are the seam it plugs into.

Acknowledgement is transport-native: IMAP sets ``\\Seen``, Maildir moves
``new/`` → ``cur/`` with the ``S`` flag. The pipeline only acks after the
reply is sent, so delivery is at-least-once and a crash re-processes.
"""

from __future__ import annotations

import hashlib
import mailbox
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from imap_tools import AND, MailBox, MailMessageFlags

from .config import ImapConfig, SmtpConfig
from .message import Attachment, InboundEmail, parse_email, sanitize_filename


class InboundTransport(Protocol):
    def fetch(self) -> list[InboundEmail]:
        """Return messages not yet acknowledged, oldest first."""
        ...

    def ack(self, message_id: str) -> None:
        """Mark a fetched message as handled so it is never fetched again."""
        ...


class OutboundTransport(Protocol):
    def send(self, reply: EmailMessage) -> None: ...


# ── Maildir ───────────────────────────────────────────────────────────────────


class MaildirInbound:
    def __init__(self, path: Path) -> None:
        self._maildir = mailbox.Maildir(str(path), factory=None, create=True)
        self._keys: dict[str, str] = {}  # message_id → maildir key

    def fetch(self) -> list[InboundEmail]:
        out: list[InboundEmail] = []
        for key in sorted(self._maildir.keys()):
            if "S" in self._maildir[key].get_flags():
                continue
            inbound = parse_email(self._maildir.get_bytes(key))
            self._keys[inbound.message_id] = key
            out.append(inbound)
        return out

    def ack(self, message_id: str) -> None:
        key = self._keys.get(message_id)
        if key is None:
            return
        msg = self._maildir[key]
        msg.set_subdir("cur")
        msg.add_flag("S")
        self._maildir[key] = msg


class MaildirOutbound:
    def __init__(self, path: Path) -> None:
        self._maildir = mailbox.Maildir(str(path), factory=None, create=True)

    def send(self, reply: EmailMessage) -> None:
        self._maildir.add(reply)


# ── IMAP + SMTP ───────────────────────────────────────────────────────────────


class ImapInbound:
    """Unseen mail over IMAP (imap-tools). ``ack`` sets the ``\\Seen`` flag."""

    def __init__(self, cfg: ImapConfig, password: str) -> None:
        self._cfg = cfg
        self._password = password
        self._box: MailBox | None = None
        self._uids: dict[str, str] = {}  # message_id → IMAP UID

    def _mailbox(self) -> MailBox:
        if self._box is None:
            self._box = MailBox(self._cfg.host, self._cfg.port).login(
                self._cfg.user, self._password, initial_folder=self._cfg.folder
            )
        return self._box

    def fetch(self) -> list[InboundEmail]:
        out: list[InboundEmail] = []
        for msg in self._mailbox().fetch(AND(seen=False), mark_seen=False):
            header = msg.headers.get("message-id", ())
            message_id = header[0].strip() if header else ""
            if not message_id:
                digest = hashlib.sha256(
                    f"{msg.from_}|{msg.subject}|{msg.date_str}".encode()
                ).hexdigest()[:24]
                message_id = f"<{digest}@quints-mailroom>"
            refs_header = msg.headers.get("references", ())
            attachments = tuple(
                Attachment(sanitize_filename(att.filename or ""), att.payload)
                for att in msg.attachments
                if att.payload
            )
            inbound = InboundEmail(
                message_id=message_id,
                sender=msg.from_,
                subject=msg.subject.strip(),
                body=(msg.text or msg.html or "").strip(),
                attachments=attachments,
                references=tuple(refs_header[0].split()) if refs_header else (),
            )
            if msg.uid:
                self._uids[message_id] = msg.uid
            out.append(inbound)
        return out

    def ack(self, message_id: str) -> None:
        uid = self._uids.get(message_id)
        if uid is not None:
            self._mailbox().flag([uid], MailMessageFlags.SEEN, True)


class SmtpOutbound:
    """One connection per send — polling is minutes apart, keep-alive buys nothing."""

    def __init__(self, cfg: SmtpConfig, password: str) -> None:
        self._cfg = cfg
        self._password = password

    def send(self, reply: EmailMessage) -> None:
        context = ssl.create_default_context()
        if self._cfg.starttls:
            with smtplib.SMTP(self._cfg.host, self._cfg.port) as smtp:
                smtp.starttls(context=context)
                if self._cfg.user:
                    smtp.login(self._cfg.user, self._password)
                smtp.send_message(reply)
        else:
            with smtplib.SMTP_SSL(self._cfg.host, self._cfg.port, context=context) as smtp:
                if self._cfg.user:
                    smtp.login(self._cfg.user, self._password)
                smtp.send_message(reply)
