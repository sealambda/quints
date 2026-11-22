"""Pipeline behaviour with fake transports and a fake agent — no network."""

from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path

import pytest

from quints_mailroom import pipeline
from quints_mailroom.config import AgentConfig, AuthConfig, MailroomConfig
from quints_mailroom.message import Attachment, InboundEmail
from quints_mailroom.pipeline import process_once

MX = "mx.firm.ch"


class FakeInbound:
    def __init__(self, messages: list[InboundEmail], store: Path | None = None) -> None:
        self.messages = messages
        self.acked: list[str] = []
        self.limits: list[int] = []
        self.store = store  # when set, ack asserts the message was recorded first

    def fetch(self, limit: int) -> list[InboundEmail]:
        self.limits.append(limit)
        return list(self.messages[:limit])

    def ack(self, message_id: str) -> None:
        if self.store is not None:
            assert message_id in self.store.read_text().split()
        self.acked.append(message_id)


class FailingAckInbound(FakeInbound):
    def ack(self, message_id: str) -> None:
        raise OSError("IMAP connection dropped")


class FakeOutbound:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, reply: EmailMessage) -> None:
        self.sent.append(reply)


class FailingOutbound:
    def send(self, reply: EmailMessage) -> None:
        raise OSError("SMTP refused")


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


def verdict(sender: str, authserv_id: str = MX) -> str:
    return f"{authserv_id}; dmarc=pass header.from={sender.rpartition('@')[2]}"


def message(
    mid: str = "<m1@example.com>",
    sender: str = "ada@example.com",
    auth_results: tuple[str, ...] | None = None,
    attachments: tuple[Attachment, ...] = (Attachment("invoice.pdf", b"%PDF fake"),),
    body: str = "Please book the attached invoice.",
) -> InboundEmail:
    return InboundEmail(
        message_id=mid,
        sender=sender,
        subject="Invoice ACME July",
        body=body,
        attachments=attachments,
        references=(),
        auth_results=(verdict(sender),) if auth_results is None else auth_results,
    )


def config(books: Path) -> MailroomConfig:
    return MailroomConfig(
        books=books,
        from_addr="books@firm.ch",
        allowed_senders=("ada@example.com", "@firm.ch"),
        inbound="maildir",
        outbound="maildir",
        agent=AgentConfig(backend="command", command=("true",)),
        auth=AuthConfig(authserv_id=MX),
    )


def processed(books: Path) -> str:
    path = books / ".mailroom" / "processed"
    return path.read_text() if path.exists() else ""


def test_happy_path_saves_replies_records_then_acks(tmp_path: Path) -> None:
    store = tmp_path / ".mailroom" / "processed"
    inbound, outbound, agent = FakeInbound([message()], store=store), FakeOutbound(), FakeAgent()
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
    # FakeInbound(store=...) fails the ack unless the store already has the id.
    assert inbound.acked == ["<m1@example.com>"]
    assert "<m1@example.com>" in processed(tmp_path)


def test_second_pass_is_idempotent(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    outbound = FakeOutbound()
    process_once(FakeInbound([message()]), outbound, FakeAgent(), cfg)
    outcomes = process_once(FakeInbound([message()]), outbound, FakeAgent(), cfg)
    assert [o.status for o in outcomes] == ["duplicate"]
    assert len(outbound.sent) == 1


def test_unknown_sender_is_dropped_without_agent_or_reply(tmp_path: Path) -> None:
    store = tmp_path / ".mailroom" / "processed"
    inbound = FakeInbound([message(sender="mallory@evil.example")], store=store)
    outbound, agent = FakeOutbound(), FakeAgent()
    outcomes = process_once(inbound, outbound, agent, config(tmp_path))
    assert [o.status for o in outcomes] == ["ignored"]
    assert outbound.sent == [] and agent.prompts == []
    assert inbound.acked == [message(sender="x").message_id]
    assert not (tmp_path / "inbox").exists()


@pytest.mark.parametrize(
    "auth_results",
    [
        (),  # no verdict at all: a forged From: on a server that adds none
        (verdict("ada@example.com", authserv_id="mx.evil.example"),),  # sender-written header
        (f"{MX}; dmarc=fail header.from=example.com",),
    ],
)
def test_unauthenticated_allowed_sender_is_dropped_like_an_unknown_one(
    tmp_path: Path, auth_results: tuple[str, ...]
) -> None:
    inbound = FakeInbound([message(auth_results=auth_results)])
    outbound, agent = FakeOutbound(), FakeAgent()
    outcomes = process_once(inbound, outbound, agent, config(tmp_path))
    assert [o.status for o in outcomes] == ["ignored"]
    assert "not authenticated" in outcomes[0].detail
    assert outbound.sent == [] and agent.prompts == []
    assert inbound.acked == ["<m1@example.com>"]
    assert "<m1@example.com>" in processed(tmp_path)
    assert not (tmp_path / "inbox").exists()


def test_authentication_can_be_switched_off_explicitly(tmp_path: Path) -> None:
    cfg = replace(config(tmp_path), auth=AuthConfig(required=False))
    outcomes = process_once(
        FakeInbound([message(auth_results=())]), FakeOutbound(), FakeAgent(), cfg
    )
    assert [o.status for o in outcomes] == ["replied"]


def test_domain_allowlist_matches(tmp_path: Path) -> None:
    inbound = FakeInbound([message(sender="partner@firm.ch")])
    outcomes = process_once(inbound, FakeOutbound(), FakeAgent(), config(tmp_path))
    assert [o.status for o in outcomes] == ["replied"]


def test_agent_failure_leaves_message_unacked_for_retry(tmp_path: Path) -> None:
    inbound, outbound = FakeInbound([message()]), FakeOutbound()
    outcomes = process_once(inbound, outbound, FailingAgent(), config(tmp_path))
    assert [o.status for o in outcomes] == ["error"]
    assert outcomes[0].detail == "RuntimeError: model exploded"
    assert outbound.sent == [] and inbound.acked == []
    assert processed(tmp_path) == ""


def test_send_failure_is_an_error_not_a_reply(tmp_path: Path) -> None:
    inbound = FakeInbound([message()])
    outcomes = process_once(inbound, FailingOutbound(), FakeAgent(), config(tmp_path))
    assert [o.status for o in outcomes] == ["error"]
    assert "SMTP refused" in outcomes[0].detail
    assert inbound.acked == [] and processed(tmp_path) == ""


def test_ack_failure_after_record_is_an_error_then_a_duplicate(tmp_path: Path) -> None:
    cfg, outbound = config(tmp_path), FakeOutbound()
    first = process_once(FailingAckInbound([message()]), outbound, FakeAgent(), cfg)
    assert [o.status for o in first] == ["error"]
    # Recorded before the ack, so the retry acks without a second reply.
    retry = FakeInbound([message()])
    assert [o.status for o in process_once(retry, outbound, FakeAgent(), cfg)] == ["duplicate"]
    assert retry.acked == ["<m1@example.com>"] and len(outbound.sent) == 1


@pytest.mark.parametrize(
    ("msg", "detail"),
    [
        (
            message(attachments=tuple(Attachment(f"a{i}.pdf", b"x") for i in range(21))),
            "21 attachments",
        ),
        (
            message(
                attachments=(Attachment("big.pdf", b"x" * (pipeline.MAX_ATTACHMENT_BYTES + 1)),)
            ),
            "attachment bytes",
        ),
        (message(body="x" * (pipeline.MAX_BODY_CHARS + 1)), "character body"),
    ],
)
def test_over_limit_mail_is_rejected_without_agent_run(
    tmp_path: Path, msg: InboundEmail, detail: str
) -> None:
    inbound, outbound, agent = FakeInbound([msg]), FakeOutbound(), FakeAgent()
    outcomes = process_once(inbound, outbound, agent, config(tmp_path))
    assert [o.status for o in outcomes] == ["rejected"]
    assert detail in outcomes[0].detail and "not retried" in outcomes[0].detail
    assert agent.prompts == [] and outbound.sent == []
    assert not (tmp_path / "inbox").exists()
    # Terminal: recorded and acked, so it can't wedge every later pass.
    assert inbound.acked == ["<m1@example.com>"]
    assert "<m1@example.com>" in processed(tmp_path)


def test_pass_takes_a_bounded_batch_and_leaves_the_rest(tmp_path: Path) -> None:
    many = [message(mid=f"<m{i}@example.com>", attachments=()) for i in range(60)]
    inbound = FakeInbound(many)
    outcomes = process_once(inbound, FakeOutbound(), FakeAgent(), config(tmp_path))
    assert inbound.limits == [pipeline.MAX_MESSAGES_PER_PASS]
    assert len(outcomes) == pipeline.MAX_MESSAGES_PER_PASS
    assert "<m59@example.com>" not in inbound.acked


def test_attachment_name_collision_gets_suffixed(tmp_path: Path) -> None:
    first = message()
    second = message(
        mid="<m2@example.com>", attachments=(Attachment("invoice.pdf", b"%PDF different"),)
    )
    cfg = config(tmp_path)
    process_once(FakeInbound([first]), FakeOutbound(), FakeAgent(), cfg)
    process_once(FakeInbound([second]), FakeOutbound(), FakeAgent(), cfg)
    assert (tmp_path / "inbox" / "invoice.pdf").read_bytes() == b"%PDF fake"
    assert (tmp_path / "inbox" / "invoice-1.pdf").read_bytes() == b"%PDF different"
    assert sorted(p.name for p in (tmp_path / "inbox").iterdir()) == [
        "invoice-1.pdf",
        "invoice.pdf",
    ]


def test_resent_identical_attachment_reuses_its_file(tmp_path: Path) -> None:
    saved = pipeline.save_attachments(tmp_path, message())
    again = pipeline.save_attachments(tmp_path, message(mid="<m2@example.com>"))
    assert saved == again == ["inbox/invoice.pdf"]


def test_collision_suffixes_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "MAX_NAME_SUFFIX", 3)
    for i in range(3):
        msg = message(
            mid=f"<m{i}@example.com>", attachments=(Attachment("invoice.pdf", b"v%d" % i),)
        )
        pipeline.save_attachments(tmp_path, msg)
    with pytest.raises(ValueError, match=r"3 files named like invoice\.pdf"):
        pipeline.save_attachments(
            tmp_path, message(attachments=(Attachment("invoice.pdf", b"v9"),))
        )
