"""Did the sender really send this? Ask the receiving MTA, not the ``From:``.

``From:`` is whatever the sender typed. The receiving server (Gmail, your
own MX) checks DKIM/SPF/DMARC and records the verdict in an
``Authentication-Results`` header (RFC 8601) stamped with its own
authserv-id. Only the topmost header carrying the *configured* authserv-id
is trusted: headers are prepended in transit, so anything below it — or
with any other id — may have been written by the sender.

A message is authenticated when that header shows ``dmarc=pass`` for the
From domain, or ``dkim=pass`` with a signing domain aligned to it.
"""

from __future__ import annotations

import authres


def _domain(value: str) -> str:
    return value.rpartition("@")[2].strip().lower().rstrip(".")


def _aligned(signing: str, from_domain: str) -> bool:
    # Relaxed alignment, minus a public-suffix lookup: the signer is the From
    # domain or a parent of it, matched on a label boundary (not "evil-firm.ch").
    return bool(signing) and (from_domain == signing or from_domain.endswith("." + signing))


def sender_authenticated(sender: str, auth_results: tuple[str, ...], authserv_id: str) -> bool:
    """True when the trusted MTA's verdict covers ``sender``'s domain."""
    from_domain = _domain(sender)
    if not from_domain:
        return False
    trusted = authserv_id.lower()
    for raw in auth_results:  # top (most recent hop) first
        try:
            header = authres.parse_value(" ".join(raw.split()))
        except authres.AuthResError:
            # Fail closed: skipping it could fall through to a lower header
            # the sender wrote themselves.
            return False
        if str(header.authserv_id).lower() != trusted:
            continue
        for result in header.results:
            props = {f"{p.type}.{p.name}".lower(): str(p.value) for p in result.properties}
            if result.result != "pass":
                continue
            if result.method == "dmarc" and _domain(props.get("header.from", "")) == from_domain:
                return True
            signer = props.get("header.d") or props.get("header.i", "")
            if result.method == "dkim" and _aligned(_domain(signer), from_domain):
                return True
        return False  # the trusted MTA's own (topmost) verdict decides; older ones don't
    return False
