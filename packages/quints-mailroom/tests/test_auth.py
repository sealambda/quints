"""Sender authentication from the receiving MTA's Authentication-Results."""

import pytest

from quints_mailroom.auth import sender_authenticated

GMAIL = "mx.google.com"
# What Gmail actually writes: DKIM identifies the signer by header.i.
GMAIL_PASS = (
    "mx.google.com;\r\n       dkim=pass header.i=@firm.ch header.s=google header.b=Ab12Cd34;\r\n"
    "       spf=pass (google.com: domain of ada@firm.ch designates 203.0.113.5 as permitted"
    " sender) smtp.mailfrom=ada@firm.ch;\r\n"
    "       dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=firm.ch"
)


@pytest.mark.parametrize(
    ("sender", "headers"),
    [
        ("ada@firm.ch", (GMAIL_PASS,)),
        ("ada@firm.ch", ("mx.google.com; dmarc=pass header.from=firm.ch",)),
        ("ada@firm.ch", ("mx.google.com; dkim=pass header.d=firm.ch",)),
        ("ada@Firm.CH", ("MX.Google.com; dkim=pass header.i=@firm.ch",)),
        # Relaxed alignment: the parent domain signs for a subdomain.
        ("ada@mail.firm.ch", ("mx.google.com; dkim=pass header.d=firm.ch",)),
    ],
)
def test_pass_from_the_trusted_server(sender: str, headers: tuple[str, ...]) -> None:
    assert sender_authenticated(sender, headers, GMAIL)


@pytest.mark.parametrize(
    ("sender", "headers"),
    [
        ("ada@firm.ch", ()),
        ("ada@firm.ch", ("mx.google.com; none",)),
        ("ada@firm.ch", ("mx.google.com; dmarc=fail header.from=firm.ch",)),
        # Spoofed From: the pass is for the attacker's own domain.
        ("ada@firm.ch", ("mx.google.com; dmarc=pass header.from=evil.example",)),
        ("ada@firm.ch", ("mx.google.com; dkim=pass header.d=evil.example",)),
        # Label boundary: evil-firm.ch is not aligned with firm.ch.
        ("ada@firm.ch", ("mx.google.com; dkim=pass header.d=evil-firm.ch",)),
        # A subdomain does not vouch for its parent.
        ("ada@firm.ch", ("mx.google.com; dkim=pass header.d=mail.firm.ch",)),
        # Any other authserv-id is sender-writable.
        ("ada@firm.ch", ("mx.evil.example; dmarc=pass header.from=firm.ch",)),
        # The trusted (topmost) verdict decides; an injected one below can't overrule it.
        (
            "ada@firm.ch",
            (
                "mx.google.com; dmarc=fail header.from=firm.ch",
                "mx.google.com; dmarc=pass header.from=firm.ch",
            ),
        ),
        # Unparseable on top: fail closed, never fall through to a lower header.
        ("ada@firm.ch", ("mx.google.com;;; ===", "mx.google.com; dmarc=pass header.from=firm.ch")),
        ("", ("mx.google.com; dmarc=pass",)),
    ],
)
def test_no_pass_from_the_trusted_server(sender: str, headers: tuple[str, ...]) -> None:
    assert not sender_authenticated(sender, headers, GMAIL)
