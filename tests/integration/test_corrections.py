"""A bad price end to end (build plan step 1, line 7; D-14.4): a spike
breaches a funded account; one person flags it, a second approves; the
loss comes back and the account trades again."""

import asyncio
from typing import Any

import pytest

from propfirm.service.projector import Projector
from tests.integration.test_cases import funded_instant
from tests.integration.test_customers import Platform, eventually, platform
from tests.integration.test_progression import STAFF, Market

pytestmark = pytest.mark.integration


async def project(p: Platform, database_url: str) -> None:
    projector = await Projector.connect(database_url, p.config.schema, p.config.read_schema)
    try:
        while await projector.run_once():
            pass
    finally:
        await projector.close()


def test_a_breach_from_a_bad_price_is_reversed_by_two_people(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            acc = await funded_instant(p)
            market = Market(p)
            market.seq = 1_000  # after funded_instant's own prices
            good = 110_300
            await market.price(good)
            trade = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 200}
            opened = await p.call("POST", f"/accounts/{acc}/orders", body=trade)
            assert opened.json()["status"] == "done", opened.text
            before = (await p.live.account(acc) or {})["balance_cents"]
            await market.price(good - 300)  # the bad print: 30 pips down, a breach
            bad_seq = market.seq

            async def breached() -> dict[str, Any] | None:
                view = await p.live.account(acc)
                return view if view is not None and view["status"] == "breached" else None

            await eventually(breached)
            await market.price(good)  # the real price, back
            await project(p, database_url)

            # the spike is offered without looking up its number
            found = await p.call(
                "GET", "/staff/corrections/suspects?hours=1", "staff-1", None, STAFF
            )
            assert found.status_code == 200, found.text
            spike = next(s for s in found.json() if s["price_seq"] == bad_seq)
            assert (spike["symbol"], spike["fills"], spike["suggested"]) == (
                "EUR/USD",
                1,
                good,
            )  # the stop-out at the spike
            q = f"symbol=EUR/USD&price_seq={bad_seq}&good_price={good}"
            seen = await p.call("GET", f"/staff/corrections/preview?{q}", "staff-1", None, STAFF)
            assert seen.status_code == 200, seen.text
            preview = seen.json()
            (item,) = preview["items"]
            assert (item["account_id"], item["reinstate"]) == (acc, True)
            assert item["amount_cents"] > 0 and item["manual"] == []

            body = {
                "symbol": "EUR/USD",
                "price_seq": bad_seq,
                "good_price": good,
                "reason": "feed spike",
            }
            proposed = await p.call("POST", "/staff/corrections", "staff-1", body, STAFF)
            assert proposed.status_code == 201, proposed.text
            cor = proposed.json()["cor"]
            assert cor == "COR00000001" and proposed.json()["status"] == "proposed"
            again = await p.call("POST", "/staff/corrections", "staff-2", body, STAFF)
            assert again.json()["detail"]["code"] == "duplicate"
            own = await p.call("POST", f"/staff/corrections/{cor}/approve", "staff-1", None, STAFF)
            assert own.json()["detail"]["code"] == "same_person"

            done = await p.call("POST", f"/staff/corrections/{cor}/approve", "staff-2", None, STAFF)
            assert done.status_code == 200, done.text
            assert done.json()["status"] == "applied"
            assert [o["status"] for o in done.json()["outcome"]] == ["done", "done"]

            async def active() -> dict[str, Any] | None:
                view = await p.live.account(acc)
                return view if view is not None and view["status"] == "active" else None

            view = await eventually(active)
            # as if closed at the good price: only the spread (10 points on 2
            # lots) and the commission are lost, not the 30-pip spike
            assert -4_000 <= view["balance_cents"] - before < 0
            small = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 1}
            traded = await p.call("POST", f"/accounts/{acc}/orders", body=small)
            assert traded.json()["status"] == "done", traded.text

    asyncio.run(scenario())
