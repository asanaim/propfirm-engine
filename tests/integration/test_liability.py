"""Liability desk (D-14.18): a funded account in profit is an expected
claim; once requested it is open, once approved certain; it is never
counted twice."""

import asyncio
from typing import Any

import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_cases import funded_instant
from tests.integration.test_customers import eventually, platform
from tests.integration.test_payouts import DAY, USDT, _payable, instant_without_consistency
from tests.integration.test_progression import STAFF

pytestmark = pytest.mark.integration


def test_a_claim_moves_from_expected_to_open_to_certain(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    offset = {"ms": 0}

    def clock() -> int:
        return now_ms() + offset["ms"]

    async def scenario() -> None:
        async with platform(
            database_url,
            valkey_url,
            pg_schema,
            instant_without_consistency(),
            funded=True,
            clock=clock,
            wait_s=2.0,
        ) as p:

            async def desk() -> Any:
                return (
                    await p.call("GET", "/staff/liabilities", "admin-1", None, ("admin",))
                ).json()

            empty = await desk()
            assert (empty["total_cents"], empty["largest"]) == (0, None)
            acc = await funded_instant(p)
            await eventually(lambda: _payable(p, acc))
            amount = (await p.call("GET", f"/accounts/{acc}/payout")).json()["trader_amount_cents"]
            assert amount > 0

            first = await desk()
            assert (first["certain_cents"], first["open_cents"], first["expected_cents"]) == (
                0,
                0,
                amount,
            )
            assert (first["funded_accounts"], first["largest"]["acc"]) == (1, acc)
            assert first["largest"]["total_cents"] == amount

            await p.call("POST", "/me/wallet", body={"address": USDT})
            offset["ms"] = 15 * DAY
            created = await p.call("POST", f"/accounts/{acc}/payouts")
            assert created.status_code == 201, created.text
            requested = await desk()
            assert (requested["open_cents"], requested["expected_cents"]) == (
                amount,
                0,
            )  # not twice
            assert requested["total_cents"] == amount

            po = created.json()["po"]
            for who, roles in (("staff-1", STAFF), ("admin-1", ("admin",))):
                await p.call("POST", f"/staff/payouts/{po}/approve", who, {"reason": "ok"}, roles)
            approved = await desk()
            assert (
                approved["certain_cents"],
                approved["open_cents"],
                approved["expected_cents"],
            ) == (
                amount,
                0,
                0,
            )
            assert approved["claims"][0]["certain_cents"] == amount

            for who, roles in (("u1", ("trader",)), ("staff-1", STAFF)):
                refused = await p.call("GET", "/staff/liabilities", who, None, roles)
                assert refused.status_code == 403  # money figures need settings.view

    asyncio.run(scenario())
