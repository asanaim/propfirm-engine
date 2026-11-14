"""Retake after a breach (D-13.6): a breached Lite 10K gives one new Lite 10K
at 10 % off; one retake at a time; the price shows on the trader's page."""

import asyncio
from typing import Any

import pytest

from tests.integration.test_customers import ADULT, LITE_10K, eventually, platform
from tests.integration.test_progression import Market

pytestmark = pytest.mark.integration


def test_a_breach_gives_one_retake_at_ten_percent_off(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            market = Market(p)
            await market.price(110_000)
            await p.call("POST", "/me", body=ADULT)
            first = (await p.call("POST", "/orders", body=LITE_10K)).json()
            acc = (await p.call("POST", first["pay_url"])).json()["acc"]
            await eventually(lambda: p.live.account(acc))
            assert (await p.call("GET", "/me/retakes")).json() == []  # not breached

            trade = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 200}
            opened = await p.call("POST", f"/accounts/{acc}/orders", body=trade)
            assert opened.json()["status"] == "done", opened.text
            await market.price(109_700)  # 30 pips on 2 lots: $600, past the 3 % daily

            async def breached() -> dict[str, Any] | None:
                view = await p.live.account(acc)
                return view if view is not None and view["status"] == "breached" else None

            await eventually(breached)
            offers = (await p.call("GET", "/me/retakes")).json()
            assert offers == [
                {
                    "of_ord": first["ord"],
                    "product_id": "lite",
                    "size_cents": 1_000_000,
                    "list_price_cents": 5_500,
                    "discount_percent": "10",
                    "price_cents": 4_950,
                }
            ]

            other_size = await p.call("POST", "/orders", body={**LITE_10K, "size_cents": 2_500_000})
            assert other_size.json()["discount_kind"] == "none"  # same size only

            retake = (await p.call("POST", "/orders", body=LITE_10K)).json()
            assert (retake["discount_kind"], retake["discount_percent"]) == ("retake", "10")
            assert retake["price_cents"] == 4_950
            history = await p.store.order_history(retake["ord"])
            assert history[0].detail["retake_of"] == first["ord"]

            # one retake at a time, and the breach's one retake is taken
            assert (await p.call("GET", "/me/retakes")).json() == []
            again = (await p.call("POST", "/orders", body=LITE_10K)).json()
            assert (again["discount_kind"], again["price_cents"]) == ("none", 5_500)

            paid = (await p.call("POST", retake["pay_url"])).json()
            assert paid["status"] == "provisioned", paid
            assert (await p.call("GET", "/me/retakes")).json() == []

    asyncio.run(scenario())
