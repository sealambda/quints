"""One pass over the mailbox: fetch → gate → save → agent → reply → record → ack.

Per message: unknown senders, and allowed ones whose mail the receiving
server did not authenticate (see :mod:`.auth`), are recorded and
acknowledged without a reply (no backscatter, no agent run — these are the
outer security gates). Mail over the size bounds is ``rejected`` the same
way, before anything is saved. Allowed mail has its attachments saved into
the books repo's ``inbox/``, the agent drafts/answers, the reply goes out,
then the message is recorded in ``.mailroom/processed`` and only then
acknowledged. A failure before the record leaves the message
unacknowledged, so the next pass retries it: at-least-once, with the
processed file — written before the transport ack — as the idempotency
backstop across transports.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .agent import AgentRunner, build_prompt
from .auth import sender_authenticated
from .config import MailroomConfig, sender_allowed
from .message import InboundEmail, build_reply
from .transport import InboundTransport, OutboundTransport

# Bounds on what a sender can make the mailroom do. A message over one is
# rejected whole: a silently truncated invoice or question is worse than none.
MAX_MESSAGES_PER_PASS = 50  # the rest wait, unacknowledged, for the next pass
MAX_ATTACHMENTS = 20
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # all attachments of one message
MAX_BODY_CHARS = 50_000
MAX_NAME_SUFFIX = 1000  # invoice.pdf, invoice-1.pdf, … invoice-999.pdf


@dataclass(frozen=True)
class ProcessOutcome:
    message_id: str
    sender: str
    subject: str
    status: str  # "replied" | "ignored" | "rejected" | "duplicate" | "error"
    detail: str = ""


class ProcessedStore:
    """Message-IDs already handled — one per line, append-only."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._seen: set[str] = set()
        if path.is_file():
            self._seen = {line.strip() for line in path.read_text().splitlines() if line.strip()}

    def __contains__(self, message_id: str) -> bool:
        return message_id in self._seen

    def add(self, message_id: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as fh:
            fh.write(message_id + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._seen.add(message_id)


def over_limit(message: InboundEmail) -> str | None:
    """Why ``message`` exceeds a bound, or None."""
    if len(message.attachments) > MAX_ATTACHMENTS:
        return f"{len(message.attachments)} attachments (limit {MAX_ATTACHMENTS})"
    size = sum(len(a.payload) for a in message.attachments)
    if size > MAX_ATTACHMENT_BYTES:
        return f"{size} attachment bytes (limit {MAX_ATTACHMENT_BYTES})"
    if len(message.body) > MAX_BODY_CHARS:
        return f"{len(message.body)}-character body (limit {MAX_BODY_CHARS})"
    return None


def _write_new(path: Path, payload: bytes) -> None:
    """Write ``path`` whole or not at all; FileExistsError if the name is taken.

    Same recipe as ``quints.ledger.atomic_write(overwrite=False)``: fsynced
    temp file in the same directory, then a hard link, which fails atomically
    instead of clobbering.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def save_attachments(books: Path, message: InboundEmail) -> list[str]:
    """Write attachments into ``inbox/``; return repo-relative paths.

    An existing file is never overwritten: a re-sent identical document
    reuses its file, a different one with the same name gets a suffix.
    """
    inbox = books / "inbox"
    saved: list[str] = []
    for att in message.attachments:
        inbox.mkdir(parents=True, exist_ok=True)
        stem, suffix = Path(att.filename).stem, Path(att.filename).suffix
        for n in range(MAX_NAME_SUFFIX):
            target = inbox / (att.filename if n == 0 else f"{stem}-{n}{suffix}")
            if target.exists() and target.read_bytes() == att.payload:
                break
            try:
                _write_new(target, att.payload)
                break
            except FileExistsError:
                continue
        else:
            raise ValueError(
                f"inbox/ already holds {MAX_NAME_SUFFIX} files named like {att.filename}"
            )
        saved.append(str(target.relative_to(books)))
    return saved


def _gate(message: InboundEmail, cfg: MailroomConfig) -> tuple[str, str] | None:
    """(status, detail) for mail that gets no agent run, or None to proceed."""
    if not sender_allowed(message.sender, cfg.allowed_senders):
        return "ignored", "sender not in allowed_senders"
    auth = cfg.auth
    if auth.required and not (
        auth.authserv_id
        and sender_authenticated(message.sender, message.auth_results, auth.authserv_id)
    ):
        return "ignored", f"sender not authenticated (no DMARC/DKIM pass from {auth.authserv_id})"
    reason = over_limit(message)
    if reason is not None:
        return "rejected", f"{reason}; not processed, not retried"
    return None


def process_once(
    inbound: InboundTransport,
    outbound: OutboundTransport,
    agent: AgentRunner,
    cfg: MailroomConfig,
) -> list[ProcessOutcome]:
    store = ProcessedStore(cfg.books / ".mailroom" / "processed")
    outcomes: list[ProcessOutcome] = []
    for message in inbound.fetch(limit=MAX_MESSAGES_PER_PASS):
        try:
            if message.message_id in store:
                inbound.ack(message.message_id)
                outcomes.append(_outcome(message, "duplicate", "already processed"))
                continue
            gated = _gate(message, cfg)
            if gated is not None:
                store.add(message.message_id)
                inbound.ack(message.message_id)
                outcomes.append(_outcome(message, *gated))
                continue
            saved = save_attachments(cfg.books, message)
            prompt = build_prompt(message, saved, cfg.agent.instructions)
            reply_text = agent.run(prompt, cwd=cfg.books)
            outbound.send(build_reply(message, cfg.from_addr, reply_text))
            store.add(message.message_id)
            inbound.ack(message.message_id)
        # One bad email must not stop the mailroom, and the transports and
        # agent are pluggable, so their failure types are open-ended.
        except Exception as exc:  # noqa: BLE001 — per-message isolation; reported as "error", never success
            outcomes.append(_outcome(message, "error", f"{type(exc).__name__}: {exc}"))
            continue  # next pass: retried if unrecorded, else acked as a duplicate
        outcomes.append(_outcome(message, "replied", f"{len(saved)} attachment(s) saved"))
    return outcomes


def _outcome(message: InboundEmail, status: str, detail: str) -> ProcessOutcome:
    return ProcessOutcome(
        message_id=message.message_id,
        sender=message.sender,
        subject=message.subject,
        status=status,
        detail=detail,
    )
