"""Email in, email out — parsing and reply building on stdlib ``email`` only.

Everything speaks RFC 5322: the inbound side normalises any transport's
message into :class:`InboundEmail`; the outbound side builds a properly
threaded reply (``In-Reply-To`` + ``References``) so the conversation stays
one thread in the sender's mail client.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr


@dataclass(frozen=True)
class Attachment:
    filename: str  # already sanitised — safe as a bare basename
    payload: bytes


@dataclass(frozen=True)
class InboundEmail:
    message_id: str
    sender: str  # addr-spec only ("ada@example.com")
    subject: str
    body: str  # text/plain, or tag-stripped text/html fallback
    attachments: tuple[Attachment, ...]
    references: tuple[str, ...]  # existing References chain, for the reply


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_TAGS = re.compile(r"<[^>]+>")


def sanitize_filename(name: str) -> str:
    """Reduce any attachment filename to a safe basename (no traversal).

    Separators become underscores rather than truncation points, so a
    display name like "fattura n° 12/2026.pdf" keeps its meaning.
    """
    cleaned = _UNSAFE.sub("_", name.replace("\\", "_").replace("/", "_")).strip("._")
    return cleaned or "attachment.bin"


def parse_email(raw: bytes) -> InboundEmail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(str(msg.get("From", "")))[1]
    subject = str(msg.get("Subject", "")).strip()
    message_id = str(msg.get("Message-ID", "")).strip()
    if not message_id:
        # No Message-ID (rare, but legal): derive a stable one from content.
        message_id = f"<{hashlib.sha256(raw).hexdigest()[:24]}@quints-mailroom>"
    references = tuple(str(msg.get("References", "")).split())

    body = ""
    body_part = msg.get_body(preferencelist=("plain", "html"))
    if body_part is not None:
        content = str(body_part.get_content())
        body = _TAGS.sub(" ", content) if body_part.get_content_subtype() == "html" else content

    attachments: list[Attachment] = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            continue
        attachments.append(Attachment(sanitize_filename(part.get_filename() or ""), payload))

    return InboundEmail(
        message_id=message_id,
        sender=sender,
        subject=subject,
        body=body.strip(),
        attachments=tuple(attachments),
        references=references,
    )


def build_reply(inbound: InboundEmail, from_addr: str, body: str) -> EmailMessage:
    reply = EmailMessage()
    reply["From"] = from_addr
    reply["To"] = inbound.sender
    subject = inbound.subject
    reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    reply["In-Reply-To"] = inbound.message_id
    reply["References"] = " ".join((*inbound.references, inbound.message_id))
    reply["Date"] = formatdate(localtime=True)
    domain = from_addr.rpartition("@")[2]
    reply["Message-ID"] = make_msgid(domain=domain) if domain else make_msgid()
    reply.set_content(body)
    return reply
