"""Tests for the thin Stripe API client (stubbed session, no network)."""

import json
from pathlib import Path

import pytest
import requests

from beangulp_stripe.client import StripeClient, StripeError

INVOICES = json.loads((Path(__file__).parent / "fixture-invoices.json").read_text())


class StubResponse(requests.Response):
    def __init__(
        self,
        status_code: int,
        payload: object = None,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.status_code = status_code
        self._payload = payload
        # Pre-consumed body: ``.content`` and ``iter_content`` both replay it.
        self._content = body
        self._content_consumed = True
        self.headers.update(headers or {})

    def json(self, **kwargs: object) -> object:
        return self._payload


class StubSession(requests.Session):
    def __init__(self, responses: list[StubResponse]) -> None:
        super().__init__()
        # (url, params, every other kwarg) — the headers matter for auth.
        self.calls: list[tuple[str, dict[str, object], dict[str, object]]] = []
        self._responses = list(responses)

    def get(self, url: str | bytes, *args: object, **kwargs: object) -> requests.Response:
        params = kwargs.get("params")
        rest = {k: v for k, v in kwargs.items() if k != "params"}
        self.calls.append((str(url), dict(params) if isinstance(params, dict) else {}, rest))
        return self._responses.pop(0)


def test_balance_transactions_paginates_and_sorts_ascending() -> None:
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


def test_auth_travels_per_request_not_on_the_session() -> None:
    """The key must not sit on the session: document URLs share that transport."""
    session = StubSession(
        [
            StubResponse(401, {"error": {"message": "Invalid API Key provided"}}),
        ]
    )
    client = StripeClient("rk_test_x", session=session)
    assert "Authorization" not in session.headers

    with pytest.raises(StripeError, match="Invalid API Key provided"):
        client.balance()
    assert session.calls[0][2]["headers"] == {"Authorization": "Bearer rk_test_x"}


def test_invoices_paginates_and_sorts_ascending() -> None:
    page_one, page_two = INVOICES["data"][:2], INVOICES["data"][2:]
    session = StubSession(
        [
            StubResponse(200, {"object": "list", "data": page_one, "has_more": True}),
            StubResponse(200, {"object": "list", "data": page_two, "has_more": False}),
        ]
    )
    client = StripeClient("rk_test_x", session=session)
    invoices = client.invoices(created_gte=1778803000, created_lte=1781482000)

    assert [i["id"] for i in invoices] == [
        "in_00000000000001",
        "in_00000000000002",
        "in_00000000000003",
    ]
    first, second = session.calls
    assert first[0].endswith("/v1/invoices")
    assert first[1]["created[gte]"] == 1778803000
    assert first[1]["created[lte]"] == 1781482000
    assert second[1]["starting_after"] == "in_00000000000002"


def test_document_follows_redirects_and_sends_no_api_key() -> None:
    session = StubSession(
        [
            StubResponse(302, headers={"Location": "https://files.stripe.test/signed/abc"}),
            StubResponse(200, body=b"%PDF-1.7 invoice"),
        ]
    )
    client = StripeClient("rk_test_x", session=session)
    payload = client.document("https://pay.stripe.com/invoice/acct_TEST/live_TEST1/pdf")

    assert payload == b"%PDF-1.7 invoice"
    hops = [url for url, _, _ in session.calls]
    assert hops == [
        "https://pay.stripe.com/invoice/acct_TEST/live_TEST1/pdf",
        "https://files.stripe.test/signed/abc",
    ]
    # Neither hop may carry the key — the redirect target is not Stripe's API.
    assert all("headers" not in rest for _, _, rest in session.calls)
    assert all(rest["allow_redirects"] is False for _, _, rest in session.calls)


def test_document_relative_redirect_and_missing_location() -> None:
    session = StubSession(
        [
            StubResponse(303, headers={"Location": "/signed/abc"}),
            StubResponse(200, body=b"%PDF-1.7"),
            StubResponse(302),
        ]
    )
    client = StripeClient("rk_test_x", session=session)
    assert client.document("https://pay.stripe.test/invoice/x/pdf") == b"%PDF-1.7"
    assert session.calls[1][0] == "https://pay.stripe.test/signed/abc"

    with pytest.raises(StripeError, match="without a Location"):
        client.document("https://pay.stripe.test/invoice/x/pdf")


def test_document_gives_up_on_a_redirect_loop() -> None:
    loop = [
        StubResponse(307, headers={"Location": "https://pay.stripe.test/loop"}) for _ in range(4)
    ]
    session = StubSession(loop)
    client = StripeClient("rk_test_x", session=session)

    with pytest.raises(StripeError, match="more than 3 redirects"):
        client.document("https://pay.stripe.test/loop", max_redirects=3)
    assert len(session.calls) == 4


def test_document_reports_a_failed_download() -> None:
    session = StubSession([StubResponse(404)])
    client = StripeClient("rk_test_x", session=session)

    with pytest.raises(StripeError, match="404"):
        client.document("https://pay.stripe.test/invoice/gone/pdf")


def _page(ids: list[str], *, has_more: bool) -> StubResponse:
    return StubResponse(
        200,
        {"object": "list", "data": [{"id": i, "created": 1} for i in ids], "has_more": has_more},
    )


def test_pagination_refuses_an_empty_page_that_claims_more() -> None:
    """The old loop re-sent the previous cursor forever on such a page."""
    session = StubSession([_page(["txn_2"], has_more=True), _page([], has_more=True)])
    client = StripeClient("rk_test_x", session=session)
    with pytest.raises(StripeError, match="empty page with has_more"):
        client.balance_transactions()
    assert len(session.calls) == 2


def test_pagination_refuses_a_cursor_that_does_not_advance() -> None:
    session = StubSession([_page(["in_2"], has_more=True), _page(["in_2"], has_more=True)])
    client = StripeClient("rk_test_x", session=session)
    with pytest.raises(StripeError, match="cursor did not advance"):
        client.invoices()


def test_pagination_stops_at_the_item_cap() -> None:
    session = StubSession([_page([f"txn_{n}"], has_more=True) for n in range(3)])
    client = StripeClient("rk_test_x", session=session)
    with pytest.raises(StripeError, match=r"more than 2 .*narrow the window"):
        client.balance_transactions(page_size=1, max_items=2)
    assert len(session.calls) == 2


def test_pagination_refuses_non_objects_in_data() -> None:
    bad = StubResponse(200, {"object": "list", "data": ["txn_1"], "has_more": False})
    client = StripeClient("rk_test_x", session=StubSession([bad]))
    with pytest.raises(StripeError, match="not a list of objects"):
        client.balance_transactions()


def test_document_refuses_an_oversized_download() -> None:
    declared = StubResponse(200, body=b"%PDF", headers={"Content-Length": "11"})
    chunked = StubResponse(200, body=b"%PDF-1.7 too big")
    client = StripeClient("rk_test_x", session=StubSession([declared, chunked]))
    with pytest.raises(StripeError, match="larger than 10 bytes"):
        client.document("https://pay.stripe.test/invoice/x/pdf", max_bytes=10)
    with pytest.raises(StripeError, match="larger than 10 bytes"):
        client.document("https://pay.stripe.test/invoice/x/pdf", max_bytes=10)
