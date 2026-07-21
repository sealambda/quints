"""Pipeline behaviour with fake transports and a fake agent — no network."""

from email.message import EmailMessage
from pathlib import Path

from quints_mailroom.config import AgentConfig, MailroomConfig
from quints_mailroom.message import Attachment, InboundEmail
from quints_mailroom.pipeline import process_once


class FakeInbound:
    def __init__(self, messages: list[InboundEmail]) -> None:
        self.messages = messages
        self.acked: list[str] = []

    def fetch(self) -> list[InboundEmail]:
        return list(self.messages)

    def ack(self, message_id: str) -> None:
        self.acked.append(message_id)


class FakeOutbound:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, reply: EmailMessage) -> None:
        self.sent.append(reply)


class FakeAgent:
    def __init__(self, reply: str = "Done, draft is in staging/.") -> None:
        self.reply = reply
        self.prompts: list[str] = []
        self.cwds: list[Path] = []

    def run(self, prompt: str, cwd: Path) -> str:
        self.prompts.append(prompt)
        self.cwds.append(cwd)
        return self.reply


class FailingAgent:
    def run(self, prompt: str, cwd: Path) -> str:
        raise RuntimeError("model exploded")


def message(mid: str = "<m1@example.com>", sender: str = "ada@example.com") -> InboundEmail:
    return InboundEmail(
        message_id=mid,
        sender=sender,
        subject="Invoice ACME July",
        body="Please book the attached invoice.",
        attachments=(Attachment("invoice.pdf", b"%PDF fake"),),
        references=(),
    )


def config(books: Path) -> MailroomConfig:
    return MailroomConfig(
        books=books,
        from_addr="books@firm.ch",
        allowed_senders=("ada@example.com", "@firm.ch"),
        inbound="maildir",
        outbound="maildir",
        agent=AgentConfig(backend="command", command=("true",)),
    )


def test_happy_path_saves_replies_acks_and_records(tmp_path: Path) -> None:
    inbound, outbound, agent = FakeInbound([message()]), FakeOutbound(), FakeAgent()
    outcomes = process_once(inbound, outbound, agent, config(tmp_path))

    assert [o.status for o in outcomes] == ["replied"]
    assert (tmp_path / "inbox" / "invoice.pdf").read_bytes() == b"%PDF fake"
    prompt = agent.prompts[0]
    assert "inbox/invoice.pdf" in prompt and "Invoice ACME July" in prompt
    assert "Please book the attached invoice." in prompt
    assert agent.cwds == [tmp_path]
    assert len(outbound.sent) == 1
    reply = outbound.sent[0]
    assert reply["In-Reply-To"] == "<m1@example.com>"
    assert reply.get_content().strip() == "Done, draft is in staging/."
    assert inbound.acked == ["<m1@example.com>"]
    assert "<m1@example.com>" in (tmp_path / ".mailroom" / "processed").read_text()


def test_second_pass_is_idempotent(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    outbound = FakeOutbound()
    process_once(FakeInbound([message()]), outbound, FakeAgent(), cfg)
    outcomes = process_once(FakeInbound([message()]), outbound, FakeAgent(), cfg)
    assert [o.status for o in outcomes] == ["duplicate"]
    assert len(outbound.sent) == 1


def test_unknown_sender_is_dropped_without_agent_or_reply(tmp_path: Path) -> None:
    inbound = FakeInbound([message(sender="mallory@evil.example")])
    outbound, agent = FakeOutbound(), FakeAgent()
    outcomes = process_once(inbound, outbound, agent, config(tmp_path))
    assert [o.status for o in outcomes] == ["ignored"]
    assert outbound.sent == [] and agent.prompts == []
    assert inbound.acked == [message(sender="x").message_id]
    assert not (tmp_path / "inbox").exists()


def test_domain_allowlist_matches(tmp_path: Path) -> None:
    inbound = FakeInbound([message(sender="partner@firm.ch")])
    outcomes = process_once(inbound, FakeOutbound(), FakeAgent(), config(tmp_path))
    assert [o.status for o in outcomes] == ["replied"]


def test_agent_failure_leaves_message_unacked_for_retry(tmp_path: Path) -> None:
    inbound, outbound = FakeInbound([message()]), FakeOutbound()
    outcomes = process_once(inbound, outbound, FailingAgent(), config(tmp_path))
    assert [o.status for o in outcomes] == ["error"]
    assert "model exploded" in outcomes[0].detail
    assert outbound.sent == [] and inbound.acked == []
    assert not (tmp_path / ".mailroom" / "processed").exists()


def test_attachment_name_collision_gets_suffixed(tmp_path: Path) -> None:
    first = message()
    second = InboundEmail(
        message_id="<m2@example.com>",
        sender="ada@example.com",
        subject="Another one",
        body="",
        attachments=(Attachment("invoice.pdf", b"%PDF different"),),
        references=(),
    )
    cfg = config(tmp_path)
    process_once(FakeInbound([first]), FakeOutbound(), FakeAgent(), cfg)
    process_once(FakeInbound([second]), FakeOutbound(), FakeAgent(), cfg)
    assert (tmp_path / "inbox" / "invoice.pdf").read_bytes() == b"%PDF fake"
    assert (tmp_path / "inbox" / "invoice-1.pdf").read_bytes() == b"%PDF different"
