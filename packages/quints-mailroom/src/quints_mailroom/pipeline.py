"""One pass over the mailbox: fetch → gate → save → agent → reply → ack.

Per message: unknown senders are acknowledged and dropped without a reply
(no backscatter, no agent run — the allowlist is the outer security gate).
Allowed mail has its attachments saved into the books repo's ``inbox/``,
the agent drafts/answers, the reply goes out, and only then is the message
acknowledged and recorded in ``.mailroom/processed``. A failure leaves the
message unacknowledged, so the next pass retries it: at-least-once, with
the processed file as the idempotency backstop across transports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent import AgentRunner, build_prompt
from .config import MailroomConfig, sender_allowed
from .message import InboundEmail, build_reply
from .transport import InboundTransport, OutboundTransport


@dataclass(frozen=True)
class ProcessOutcome:
    message_id: str
    sender: str
    subject: str
    status: str  # "replied" | "ignored" | "duplicate" | "error"
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
        self._seen.add(message_id)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as fh:
            fh.write(message_id + "\n")


def save_attachments(books: Path, message: InboundEmail) -> list[str]:
    """Write attachments into ``inbox/``; return repo-relative paths."""
    inbox = books / "inbox"
    saved: list[str] = []
    for att in message.attachments:
        inbox.mkdir(parents=True, exist_ok=True)
        target = inbox / att.filename
        stem, suffix, n = target.stem, target.suffix, 1
        while target.exists() and target.read_bytes() != att.payload:
            target = inbox / f"{stem}-{n}{suffix}"
            n += 1
        target.write_bytes(att.payload)
        saved.append(str(target.relative_to(books)))
    return saved


def process_once(
    inbound: InboundTransport,
    outbound: OutboundTransport,
    agent: AgentRunner,
    cfg: MailroomConfig,
) -> list[ProcessOutcome]:
    store = ProcessedStore(cfg.books / ".mailroom" / "processed")
    outcomes: list[ProcessOutcome] = []
    for message in inbound.fetch():
        if message.message_id in store:
            inbound.ack(message.message_id)
            outcomes.append(_outcome(message, "duplicate", "already processed"))
            continue
        if not sender_allowed(message.sender, cfg.allowed_senders):
            inbound.ack(message.message_id)
            store.add(message.message_id)
            outcomes.append(_outcome(message, "ignored", "sender not in allowed_senders"))
            continue
        try:
            saved = save_attachments(cfg.books, message)
            prompt = build_prompt(message, saved, cfg.agent.instructions)
            reply_text = agent.run(prompt, cwd=cfg.books)
            outbound.send(build_reply(message, cfg.from_addr, reply_text))
        except Exception as exc:
            outcomes.append(_outcome(message, "error", str(exc)))
            continue  # not acked, not stored: retried on the next pass
        inbound.ack(message.message_id)
        store.add(message.message_id)
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
