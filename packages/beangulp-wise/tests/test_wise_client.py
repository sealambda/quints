"""Tests for the thin Wise API client (no network)."""

import pytest

from beangulp_wise.client import ScaChallenge, WiseClient, sign_sca_token


def _keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return key, pem


def test_sign_sca_token_produces_verifiable_signature():
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    key, pem = _keypair()
    token = "cca278c6-c24b-4b08-8560-39937b59ae43"
    signature = base64.b64decode(sign_sca_token(token, pem))
    key.public_key().verify(  # raises on mismatch
        signature, token.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
    )


class _Response:
    def __init__(self, status_code, headers=None, payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    """Fake requests.Session: first GET returns the SCA 403, then 200."""

    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(headers or {})
        if len(self.calls) == 1:
            return _Response(403, headers={"x-2fa-approval": "OTT-123"})
        return _Response(200, payload={"ok": True})


def test_client_signs_and_retries_sca_challenge():
    _, pem = _keypair()
    session = _Session()
    client = WiseClient("token", private_key_pem=pem, session=session)
    assert client.profiles() == {"ok": True}
    retry_headers = session.calls[1]
    assert retry_headers["x-2fa-approval"] == "OTT-123"
    assert retry_headers["X-Signature"]


def test_client_raises_actionable_error_without_key():
    client = WiseClient("token", session=_Session())
    with pytest.raises(ScaChallenge):
        client.profiles()
