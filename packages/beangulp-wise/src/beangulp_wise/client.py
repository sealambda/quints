"""Thin Wise API client — just what statement imports need.

Wise has no official Python SDK; the community clients either carry heavy
dependencies or AGPL licensing. The import workflow needs exactly three GET
endpoints, so this stays deliberately small:

- ``profiles()``                          — GET /v2/profiles
- ``balances(profile_id)``                — GET /v4/profiles/{id}/balances?types=STANDARD
- ``balance_statement(...)``    — GET /v1/profiles/{id}/balance-statements/{bid}/statement.json

Statement endpoints are protected by Strong Customer Authentication: the API
answers 403 with a one-time token in the ``x-2fa-approval`` header, which must
be signed with an RSA private key whose public half is registered on the Wise
account (Settings → API tokens → Manage public keys). The client signs and
retries transparently when constructed with ``private_key_pem``; without a
key it raises :class:`ScaChallenge` so callers can explain the fix.
"""

from __future__ import annotations

import base64
from typing import Any

import requests

PROD_HOST = "https://api.transferwise.com"
SANDBOX_HOST = "https://api.sandbox.transferwise.tech"


class WiseError(RuntimeError):
    """Unexpected Wise API response."""


class ScaChallenge(WiseError):
    """The endpoint requires SCA and no private key is configured.

    Fix: generate an RSA keypair, upload the public key in Wise
    (Settings → API tokens → Manage public keys), and construct the client
    with the private key PEM.
    """


def sign_sca_token(one_time_token: str, private_key_pem: bytes) -> str:
    """RSA-SHA256 (PKCS#1 v1.5) signature of the SCA one-time token, base64."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("Wise SCA signing requires an RSA private key")
    signature = key.sign(one_time_token.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")


class WiseClient:
    def __init__(
        self,
        token: str,
        *,
        private_key_pem: bytes | None = None,
        host: str = PROD_HOST,
        session: requests.Session | None = None,
    ):
        self._host = host
        self._key = private_key_pem
        self._session = session or requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {token}", "User-Agent": "beangulp-wise"}
        )

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        response = self._session.get(self._host + path, params=params, timeout=30)
        ott = response.headers.get("x-2fa-approval")
        if response.status_code == 403 and ott:
            if not self._key:
                raise ScaChallenge(
                    f"GET {path} requires SCA signing and no private key is configured"
                )
            response = self._session.get(
                self._host + path,
                params=params,
                headers={"x-2fa-approval": ott, "X-Signature": sign_sca_token(ott, self._key)},
                timeout=30,
            )
            if response.status_code == 403:
                raise ScaChallenge(
                    f"GET {path}: SCA signature rejected "
                    f"({response.headers.get('x-2fa-approval-result', 'no result header')}) — "
                    "is this private key's PUBLIC half uploaded in Wise "
                    "(Settings → API tokens → Manage public keys)?"
                )
        if response.status_code != 200:
            raise WiseError(f"GET {path} → {response.status_code}: {response.text[:200]}")
        return response.json()

    def profiles(self) -> list[dict[str, object]]:
        return self._get("/v2/profiles")

    def profile_id(self, business_name: str) -> int:
        """Profile id by business name (a token may see several entities)."""
        for profile in self.profiles():
            if profile.get("businessName") == business_name:
                ident = profile["id"]
                if not isinstance(ident, int):
                    raise WiseError(f"profile {business_name!r} has a non-integer id: {ident!r}")
                return ident
        raise WiseError(f"no profile named {business_name!r} visible to this token")

    def balances(self, profile_id: int) -> list[dict[str, object]]:
        return self._get(f"/v4/profiles/{profile_id}/balances", {"types": "STANDARD"})

    def balance_statement(
        self,
        profile_id: int,
        balance_id: int,
        currency: str,
        interval_start: str,
        interval_end: str,
        statement_type: str = "COMPACT",
    ) -> dict[str, object]:
        """Balance statement JSON for one currency balance.

        ``interval_start``/``interval_end`` are ISO instants
        (``2026-07-01T00:00:00.000Z``); COMPACT folds fees into ``totalFees``
        per transaction, which is what the importer expects.
        """
        return self._get(
            f"/v1/profiles/{profile_id}/balance-statements/{balance_id}/statement.json",
            {
                "currency": currency,
                "intervalStart": interval_start,
                "intervalEnd": interval_end,
                "type": statement_type,
            },
        )
