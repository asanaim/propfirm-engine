"""Free trial through the API (D-13.17): no payment, one per customer, Lite
stage 1 rules on its own rule set; it does not count as a first account."""

import asyncio
from fractions import Fraction

import pytest

from tests.integration.test_customers import ADULT, LITE_10K, eventually, platform
from tests.integration.test_progression import Market

pytestmark = pytest.mark.integration


def test_a_free_trial_opens_once_without_payment(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            await Market(p).price(110_000)
            offer = (await p.call("GET", "/trial")).json()
            assert (offer["on"], offer["product_id"], offer["size_cents"], offer["days"]) == (
                True,
                "lite",
                1_000_000,
                14,
            )
            assert offer["stage"]["rsv_id"].endswith("-trial")

            await p.call("POST", "/me", body=ADULT)
            started = await p.call("POST", "/me/trial")
            assert started.status_code == 201, started.text
            trial = started.json()
            assert (trial["trial"], trial["price_cents"], trial["status"]) == (
                True,
                0,
                "provisioned",
            )
            assert trial["rsv_id"] == offer["stage"]["rsv_id"]
            view = await eventually(lambda: p.live.account(trial["acc"]))
            assert (view["status"], view["rules"]["rsv_id"]) == ("active", trial["rsv_id"])

            again = await p.call("POST", "/me/trial")
            assert again.status_code == 409
            assert again.json()["detail"]["code"] == "trial_used"

            # a trial is not a first account: a welcome code still applies
            await p.store.add_code("WELCOME10", Fraction(10, 100), 0)
            coded = await p.call(
                "POST", "/orders", body={**LITE_10K, "size_cents": 2_500_000, "code": "WELCOME10"}
            )
            assert coded.status_code == 201, coded.text
            assert (coded.json()["trial"], coded.json()["discount_kind"]) == (False, "code")

    asyncio.run(scenario())
