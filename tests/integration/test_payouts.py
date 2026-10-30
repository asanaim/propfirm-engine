"""Payable profit and payout eligibility of a funded account, through the API,
against PostgreSQL, Valkey and a running engine."""

import asyncio
from typing import Any

import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_customers import ADULT, eventually, platform
from tests.integration.test_onboarding import INSTANT_5K, verify
from tests.integration.test_progression import STAFF, Market

pytestmark = pytest.mark.integration
DAY = 86_400_000


def test_a_funded_accounts_payable_profit_and_checks(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    offset = {"ms": 0}  # moves the API's clock

    async def scenario() -> None:
        async with platform(
            database_url,
            valkey_url,
            pg_schema,
            funded=True,
            clock=lambda: now_ms() + offset["ms"],
        ) as p:
            market = Market(p)
            await market.price(110_000)
            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body=INSTANT_5K)).json()
            await p.call("POST", order["pay_url"])
            onb = (await verify(p, "ONB00000001"))["onb"]
            sha = (await p.call("GET", f"/me/onboardings/{onb}/contract")).json()["sha256"]
            body = {"full_name": "Ada Trader", "sha256": sha}
            acc = (await p.call("POST", f"/me/onboardings/{onb}/sign", body=body)).json()["acc"]
            await eventually(lambda: p.live.account(acc))

            async def payout(user: str = "u1", roles: tuple[str, ...] = ("trader",)) -> Any:
                return await p.call("GET", f"/accounts/{acc}/payout", user, roles=roles)

            fresh = (await payout()).json()
            assert (fresh["payable_cents"], fresh["baseline_cents"], fresh["eligible"]) == (
                0,
                500_000,
                False,
            )

            trade = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 100}
            opened = (await p.call("POST", f"/accounts/{acc}/orders", body=trade)).json()
            position = next(
                e["position_id"] for e in opened["events"] if e["type"] == "PositionOpened"
            )
            await market.ramp(300)
            open_now = {c["name"]: c["status"] for c in (await payout()).json()["checks"]}
            assert open_now["flat"] == "fail"
            closed = await p.call("POST", f"/accounts/{acc}/positions/{position}/close", body={})
            pnl = next(
                e["pnl_cents"] for e in closed.json()["events"] if e["type"] == "PositionClosed"
            )
            assert pnl > 25_000

            async def settled() -> Any:
                view = (await payout()).json()
                return view if view["payable_cents"] == pnl else None

            now = await eventually(settled)
            assert (now["split_percent"], now["trader_share_cents"]) == ("80", pnl * 4 // 5)
            assert now["firm_share_cents"] == pnl - pnl * 4 // 5
            checks = {c["name"]: c["status"] for c in now["checks"]}
            assert checks == {
                "funded": "pass",
                "waiting_period": "fail",  # funded today
                "trading_days": "pass",
                "consistency": "fail",  # Instant: best day <= 50 %; one day is 100 %
                "minimum": "pass",
                "flat": "pass",
                "identity": "pass",
                "residence": "pass",
                "cases": "pass",
                "wallet": "not_decided_here",
            }
            assert now["trading_days"] == 1 and not now["eligible"]

            offset["ms"] = 15 * DAY
            later = {c["name"]: c["status"] for c in (await payout()).json()["checks"]}
            assert later["waiting_period"] == "pass" and later["consistency"] == "fail"

            staff = await payout("staff-1", STAFF)
            assert staff.status_code == 200 and staff.json()["acc"] == acc
            await p.call("POST", "/me", "u2", ADULT)
            assert (await payout("u2")).status_code == 404  # someone else's account

    asyncio.run(scenario())
