"""Add-ons bought through the shop (D-13.18): priced on the list price, the
account opens on the derived rule set, and the engine applies it."""

import asyncio

import pytest

from tests.integration.test_customers import ADULT, LITE_10K, eventually, platform
from tests.integration.test_progression import Market

pytestmark = pytest.mark.integration


def test_an_account_bought_with_add_ons_runs_on_their_rules(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            await Market(p).price(110_000)
            await p.call("POST", "/me", body=ADULT)
            catalogue = (await p.call("GET", "/catalogue")).json()
            offered = {c["id"]: c["addons"] for c in catalogue}
            assert offered["lite"] == [
                {"id": "split90", "price_percent": "20"},
                {"id": "swing", "price_percent": "15"},
                {"id": "swapfree", "price_percent": "10"},
            ]
            assert offered["instant"] == [{"id": "swapfree", "price_percent": "10"}]

            refused = [
                await p.call("POST", "/orders", body={**LITE_10K, "addons": ["weekly"]}),
                await p.call("POST", "/orders", body={**LITE_10K, "addons": ["swing", "swing"]}),
                await p.call(
                    "POST",
                    "/orders",
                    body={"product_id": "instant", "size_cents": 1_000_000, "addons": ["swing"]},
                ),
            ]
            assert [r.status_code for r in refused] == [422, 422, 422]

            body = {**LITE_10K, "addons": ["swapfree", "swing"]}
            order = (await p.call("POST", "/orders", body=body)).json()
            # $55 + 15 % + 10 %
            assert (order["addons"], order["addon_cents"], order["price_cents"]) == (
                ["swing", "swapfree"],
                1_375,
                6_875,
            )
            assert order["rsv_id"].endswith("-swing-swapfree")
            acc = (await p.call("POST", order["pay_url"])).json()["acc"]
            view = await eventually(lambda: p.live.account(acc))
            rules = view["rules"]
            assert (rules["rsv_id"], rules["addons"]) == (order["rsv_id"], ["swing", "swapfree"])
            assert (rules["leverage_cap"], rules["swap_free_nights"]) == ("30", 5)

    asyncio.run(scenario())
