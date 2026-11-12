"""The allocation cap and one Instant per trader, at checkout (review D4),
against PostgreSQL and Valkey through the API."""

import asyncio
from typing import Any

import pytest

from propfirm.journal.inputs import VoidAccount
from propfirm.service.feeder import now_ms
from tests.integration.test_customers import ADULT, eventually, platform

pytestmark = pytest.mark.integration

HOUR = 3_600_000


def order(product: str, size: int) -> dict[str, Any]:
    return {"product_id": product, "size_cents": size}


def test_the_cap_counts_open_paid_and_recent_unpaid_orders(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    offset = {"ms": 0}

    def clock() -> int:
        return now_ms() + offset["ms"]

    async def go() -> None:
        async with platform(database_url, valkey_url, pg_schema, clock=clock) as p:
            await p.engine()
            await p.call("POST", "/me", body=ADULT)

            async def buy(product: str, size: int) -> Any:
                return await p.call("POST", "/orders", body=order(product, size))

            big = (await buy("lite", 20_000_000)).json()  # $200,000
            await p.call("POST", big["pay_url"])
            await eventually(lambda: p.live.account("ACC00000001"))
            unpaid = await buy("one-step", 20_000_000)  # another $200,000, not paid
            assert unpaid.status_code == 201, unpaid.text  # exactly the cap: allowed
            over = await buy("lite", 1_000_000)
            assert (over.status_code, over.json()["detail"]["code"]) == (422, "allocation_cap")
            assert "$410,000" in over.json()["detail"]["message"]

            offset["ms"] = 25 * HOUR  # the unpaid order no longer holds its capital
            assert (await buy("lite", 1_000_000)).status_code == 201

            first = await buy("instant", 500_000)
            assert first.status_code == 201  # unpaid, but it holds the Instant place
            second = await buy("instant", 500_000)
            assert (second.status_code, second.json()["detail"]["code"]) == (422, "one_instant")

            classic = await buy("classic", 1_000_000)  # K2: off
            assert (classic.status_code, classic.json()["detail"]["code"]) == (422, "not_on_sale")

            # an account that ends frees its capital
            await p.writer.publish(VoidAccount("ACC00000001", "CAS00000001", clock()))

            async def voided() -> Any:
                view = await p.live.account("ACC00000001")
                return view if view and view["status"] == "voided" else None

            await eventually(voided)
            assert (await buy("lite", 20_000_000)).status_code == 201

    asyncio.run(go())
