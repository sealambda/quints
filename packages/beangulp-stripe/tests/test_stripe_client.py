"""Tests for the thin Stripe API client (stubbed session, no network)."""

import pytest

from beangulp_stripe.client import StripeClient, StripeError


class StubResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class StubSession:
    def __init__(self, responses):
        self.headers = {}
        self.calls = []
        self._responses = list(responses)

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self._responses.pop(0)


def test_balance_transactions_paginates_and_sorts_ascending():
    session = StubSession(
        [
            StubResponse(
                200,
                {
                    "object": "list",
                    "data": [{"id": "txn_3", "created": 300}, {"id": "txn_2", "created": 200}],
                    "has_more": True,
                },
            ),
            StubResponse(
                200,
                {
                    "object": "list",
                    "data": [{"id": "txn_1", "created": 100}],
                    "has_more": False,
                },
            ),
        ]
    )
    client = StripeClient("rk_test_x", session=session)
    transactions = client.balance_transactions(created_gte=1, created_lte=400)

    assert [t["id"] for t in transactions] == ["txn_1", "txn_2", "txn_3"]
    first, second = session.calls
    assert first[0].endswith("/v1/balance_transactions")
    assert first[1]["created[gte]"] == 1
    assert first[1]["created[lte]"] == 400
    assert first[1]["expand[]"] == "data.source"
    assert second[1]["starting_after"] == "txn_2"


def test_auth_header_and_error_message():
    session = StubSession(
        [
            StubResponse(401, {"error": {"message": "Invalid API Key provided"}}),
        ]
    )
    client = StripeClient("rk_test_x", session=session)
    assert session.headers["Authorization"] == "Bearer rk_test_x"

    with pytest.raises(StripeError, match="Invalid API Key provided"):
        client.balance()
