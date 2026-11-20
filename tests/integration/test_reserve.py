"""Payout reserve (D-13.7, D-14.20) through real records: a sale sweeps
its share into the reserve, a paid payout is drawn from it, and the desk
shows coverage K and its state."""

import asyncio
from fractions import Fraction
from typing import Any

import pytest

from propfirm.customers.books import sweep_cents
from propfirm.customers.reserve import k_text, state_of
from propfirm.policy import load_policy
from propfirm.service.feeder import now_ms
from tests.integration.test_cases import funded_instant
from tests.integration.test_customers import ADULT, LITE_10K, eventually, platform
from tests.integration.test_payouts import DAY, USDT, _payable, instant_without_consistency
from tests.integration.test_progression import STAFF

pytestmark = pytest.mark.integration

ADMIN = ("admin",)


def test_sweep_draw_and_coverage(database_url: str, valkey_url: str, pg_schema: str) -> None:
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
            assert p.books is not None and p.payouts is not None

            async def desk() -> Any:
                return (await p.call("GET", "/staff/liabilities", "admin-1", None, ADMIN)).json()

            empty = (await desk())["reserve"]
            assert (empty["reserve_cents"], empty["required_cents"]) == (0, 0)
            assert (empty["k"], empty["state"]) == (None, "normal")  # nothing owed

            acc = await funded_instant(p)
            await eventually(lambda: _payable(p, acc))
            paid = dict(load_policy().product("instant").prices_cents)[500_000]
            swept = sweep_cents(paid, Fraction(27, 100))
            await p.books.sync(p.clock(), every_ms=0)
            await p.books.sync(p.clock(), every_ms=0)  # once
            amount = (await p.call("GET", f"/accounts/{acc}/payout")).json()["trader_amount_cents"]

            first = await desk()
            reserve = first["reserve"]
            assert reserve["reserve_cents"] == swept
            assert reserve["certain_cents"] == amount  # the expected tier of the desk
            assert reserve["largest_customer_cents"] == amount
            assert reserve["required_cents"] == 2 * amount  # no open profit, no evaluation
            assert first["open_profit_cents"] == 0
            k = Fraction(swept, 2 * amount)
            assert reserve["k"] == k_text(k)
            assert reserve["state"] == state_of(k, load_policy().reserve)

            # a payout, approved by two and paid, is drawn from the reserve
            await p.call("POST", "/me/wallet", body={"address": USDT})
            offset["ms"] = 15 * DAY
            po = (await p.call("POST", f"/accounts/{acc}/payouts")).json()["po"]
            for who, roles in (("staff-1", STAFF), ("admin-1", ADMIN)):
                await p.call("POST", f"/staff/payouts/{po}/approve", who, {"reason": "ok"}, roles)
            await p.payouts.advance(clock() + 1)
            assert (await p.payouts.store.payout(po)).status == "paid"  # type: ignore[union-attr]
            await p.books.sync(p.clock(), every_ms=0)
            after = (await desk())["reserve"]
            assert after["reserve_cents"] == swept - amount
            tb = (await p.call("GET", "/staff/books/trial-balance", "admin-1", None, ADMIN)).json()
            assert tb["total_cents"] == 0
            kinds = sorted(
                e["kind"]
                for e in (
                    await p.call("GET", "/staff/books/entries", "admin-1", None, ADMIN)
                ).json()
            )
            assert kinds.count("reserve_sweep") == 1 and kinds.count("reserve_draw") == 1

    asyncio.run(scenario())


def test_an_active_evaluation_counts_its_expected_payout(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            assert p.books is not None
            await p.call("POST", "/me", "u1", ADULT)
            order = (await p.call("POST", "/orders", "u1", LITE_10K)).json()
            await p.call("POST", order["pay_url"], "u1")

            await eventually(lambda: p.live.account("ACC00000001"))  # the evaluation
            await p.call("POST", "/me/trial", "u1")  # a free trial counts nothing
            await p.books.sync(p.clock(), every_ms=0)
            reserve = (await p.call("GET", "/staff/liabilities", "admin-1", None, ADMIN)).json()[
                "reserve"
            ]
            price = int(order["price_cents"])
            assert reserve["evaluations"] == 1
            # 72 % sweep / 1.25 = 57.6 % expected payout share, rounded up
            assert reserve["expected_cents"] == -(-price * 576 // 1000)
            assert reserve["required_cents"] == -(-reserve["expected_cents"] * 3 // 2)
            assert reserve["reserve_cents"] == sweep_cents(price, Fraction(72, 100))
            assert reserve["state"] == "breach"  # one sale cannot cover 1.5 x its own share

    asyncio.run(scenario())
