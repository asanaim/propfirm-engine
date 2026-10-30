"""Market data for the portal through the API: the price source, bars and
the account's live stream (server-sent events), with fakes."""

import asyncio
import json
from typing import Any

import httpx

from propfirm.api.app import Deps, create_app
from propfirm.policy import FixedPolicy, load_policy
from tests.auth_rig import FakeCustomers, bearer, verifier
from tests.test_service import FakeGateway, FakeHistory

CUSTOMERS = FakeCustomers(
    cifs={"user-1": "CIF00000001", "user-2": "CIF00000002"},
    owners={"A": "CIF00000001", "B": "CIF00000002"},
)


class ChangingLive:
    """Account A's equity moves on every read until the third; quotes stay."""

    def __init__(self) -> None:
        self.reads = 0

    async def head(self) -> dict[str, Any] | None:
        return {"seq": 1}

    async def account(self, account_id: str) -> dict[str, Any] | None:
        if account_id != "A":
            return None
        self.reads += 1
        return {"account_id": "A", "equity_cents": 1_000_000 + min(self.reads, 3)}

    async def quotes(self) -> dict[str, dict[str, Any]]:
        return {"EUR/USD": {"bid": 109_995, "ask": 110_005}}

    async def news(self) -> list[dict[str, Any]]:
        return [{"event_id": "us-nfp", "start_ts": 1, "end_ts": 2, "symbols": ["EUR/USD"]}]


def run(
    url: str,
    *,
    user: str | None = "user-1",
    roles: tuple[str, ...] = ("trader",),
    source: str = "synthetic",
    history: FakeHistory | None = None,
) -> httpx.Response:
    deps = Deps(
        ChangingLive(),
        history or FakeHistory(),
        FakeGateway(),
        verifier(),
        CUSTOMERS,
        policy=FixedPolicy(load_policy()),
        price_source=source,
        stream_poll_s=0.01,
        stream_max_s=0.1,
    )
    headers = bearer(user, roles) if user else {}

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(deps))
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            return await client.get(url, headers=headers)

    return asyncio.run(go())


def events(text: str) -> list[tuple[str, Any]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if not line.startswith(":"))
        if lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_the_market_names_the_price_source_and_symbols() -> None:
    market = run("/market").json()
    assert (market["source"], market["attribution"]) == (
        "synthetic",
        {"text": "Synthetic prices (development)", "link": None, "url": None},
    )
    # Twelve Data's attribution guidelines: this text, "Twelve Data" a followed link
    assert run("/market", source="twelve_data").json()["attribution"] == {
        "text": "Data provided by Twelve Data",
        "link": "Twelve Data",
        "url": "https://twelvedata.com/",
    }
    assert {"symbol": "EUR/USD", "digits": 5} in market["symbols"]
    assert market["intervals"] == ["1m", "5m", "15m", "1h", "4h", "1d"]
    assert run("/market", user=None).status_code == 401  # licensed data: signed in only


def test_bars_of_a_known_symbol_only() -> None:
    history = FakeHistory()
    found = run("/bars?symbol=XAU/USD&interval=15m&limit=50&before=120000", history=history)
    assert found.status_code == 200 and found.json()[0]["close"] == 2
    assert history.asked == [("XAU/USD", "15m", 50, 120_000)]
    assert run("/bars?symbol=EUR/USD", history=history).status_code == 200
    assert history.asked[-1] == ("EUR/USD", "1m", 300, None)
    assert run("/bars?symbol=ABC/XYZ").status_code == 404
    assert run("/bars?symbol=EUR/USD&interval=2m").status_code == 422
    assert run("/bars?symbol=EUR/USD&limit=1001").status_code == 422
    assert run("/bars?symbol=EUR/USD", user=None).status_code == 401


def test_the_stream_sends_changes_then_ends() -> None:
    response = run("/accounts/A/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    got = events(response.text)
    accounts = [data["equity_cents"] for name, data in got if name == "account"]
    # the first read is the endpoint's check; then only changes, in order
    assert accounts == [1_000_002, 1_000_003]
    assert [name for name, _ in got].count("quotes") == 1
    assert got[-1] == ("end", {})


def test_the_stream_is_the_owners_or_staffs() -> None:
    assert run("/accounts/B/stream").status_code == 404  # someone else's account
    assert run("/accounts/Z/stream", roles=("staff",)).status_code == 404
    assert run("/accounts/A/stream", user="user-9", roles=("staff",)).status_code == 200
    assert run("/accounts/A/stream", user=None).status_code == 401


def test_the_news_calendar_for_signed_in_users() -> None:
    assert run("/news").json() == [
        {"event_id": "us-nfp", "start_ts": 1, "end_ts": 2, "symbols": ["EUR/USD"]}
    ]
    assert run("/news", user=None).status_code == 401
