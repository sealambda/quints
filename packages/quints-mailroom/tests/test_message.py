"""Parsing and reply threading against real RFC 5322 messages."""

from email.message import EmailMessage

from quints_mailroom.message import build_reply, parse_email, sanitize_filename

PDF = b"%PDF-1.4 fake invoice bytes"


def make_email(
    body: str = "Please book this.",
    subject: str = "Invoice from ACME",
    attach: bool = True,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "Ada Example <ada@example.com>"
    msg["To"] = "books@firm.ch"
    msg["Subject"] = subject
    msg["Message-ID"] = "<original-123@example.com>"
    msg.set_content(body)
    if attach:
        msg.add_attachment(PDF, maintype="application", subtype="pdf", filename="invoice_07.pdf")
    return msg


def test_parse_email_extracts_everything() -> None:
    parsed = parse_email(make_email().as_bytes())
    assert parsed.sender == "ada@example.com"
    assert parsed.subject == "Invoice from ACME"
    assert parsed.message_id == "<original-123@example.com>"
    assert parsed.body == "Please book this."
    assert [a.filename for a in parsed.attachments] == ["invoice_07.pdf"]
    assert parsed.attachments[0].payload == PDF


def test_parse_email_without_message_id_gets_stable_fallback() -> None:
    msg = make_email(attach=False)
    del msg["Message-ID"]
    raw = msg.as_bytes()
    first, second = parse_email(raw), parse_email(raw)
    assert first.message_id == second.message_id
    assert first.message_id.endswith("@quints-mailroom>")


def test_parse_html_only_email_strips_tags() -> None:
    msg = EmailMessage()
    msg["From"] = "ada@example.com"
    msg["Subject"] = "Q"
    msg.set_content("<p>How much <b>VAT</b>?</p>", subtype="html")
    assert "VAT" in parse_email(msg.as_bytes()).body
    assert "<b>" not in parse_email(msg.as_bytes()).body


def test_sanitize_filename_blocks_traversal_and_junk() -> None:
    assert sanitize_filename("../../etc/passwd") == "etc_passwd"
    assert sanitize_filename("..\\..\\evil.exe") == "evil.exe"
    assert sanitize_filename("fattura n° 12/2026.pdf") == "fattura_n_12_2026.pdf"
    assert sanitize_filename("") == "attachment.bin"
    assert sanitize_filename("...") == "attachment.bin"


def test_build_reply_threads_correctly() -> None:
    inbound = parse_email(make_email().as_bytes())
    reply = build_reply(inbound, "books@firm.ch", "Booked, see staging/.")
    assert reply["To"] == "ada@example.com"
    assert reply["Subject"] == "Re: Invoice from ACME"
    assert reply["In-Reply-To"] == "<original-123@example.com>"
    assert "<original-123@example.com>" in reply["References"]
    assert reply["Message-ID"].endswith("@firm.ch>")
    assert reply.get_content().strip() == "Booked, see staging/."


def test_build_reply_does_not_stack_re_prefixes() -> None:
    inbound = parse_email(make_email(subject="Re: Invoice from ACME").as_bytes())
    reply = build_reply(inbound, "books@firm.ch", "ok")
    assert reply["Subject"] == "Re: Invoice from ACME"
