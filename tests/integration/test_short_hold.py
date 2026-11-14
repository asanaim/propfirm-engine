"""Short holds end to end (build plan step 1, line 8): a funded account whose
period profit comes from a trade closed within seconds has its payout
request held for review by an integrity case, raised once per period."""

import asyncio

import pytest

from propfirm.service.feeder import now_ms
from propfirm.service.projector import Projector
from tests.integration.test_cases import funded_instant
from tests.integration.test_customers import platform
from tests.integration.test_payouts import DAY, USDT, instant_without_consistency

pytestmark = pytest.mark.integration


def test_a_payout_from_quick_trades_is_held_for_review(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    offset = {"ms": 0}

    async def scenario() -> None:
        async with platform(
            database_url,
            valkey_url,
            pg_schema,
            instant_without_consistency(),
            funded=True,
            clock=lambda: now_ms() + offset["ms"],
            short_holds=True,
        ) as p:
            assert p.payouts is not None and p.cases is not None
            acc = await funded_instant(p)  # one profitable trade, closed within seconds
            added = await p.call("POST", "/me/wallet", body={"address": USDT})
            assert added.status_code == 201, added.text
            projector = await Projector.connect(database_url, p.config.schema, p.config.read_schema)
            try:
                while await projector.run_once():  # the trade history the measure reads
                    pass
            finally:
                await projector.close()
            offset["ms"] = 15 * DAY
            checks = {
                c["name"]: c["status"]
                for c in (await p.call("GET", f"{'/accounts/'}{acc}/payout")).json()["checks"]
            }
            assert checks["cases"] == "pass"  # nothing open before the request
            measure = await p.payouts.short_hold(acc)
            assert measure is not None and measure.short_trades == 1
            assert measure.share_basis_points == 10_000  # all of the profit

            url = f"/accounts/{acc}/payouts"
            held = await p.call("POST", url)
            assert (held.status_code, held.json()["detail"]["code"]) == (409, "not_eligible")
            assert "cases" in held.json()["detail"]["message"]
            again = await p.call("POST", url)
            assert again.status_code == 409
            # one alert for the period, however often the trader asks
            assert await p.cases.has_alert(acc, "short_hold", f"period:{measure.since}")
            # D-14.5: class C from now on; staff can set it back with a reason
            assert p.payouts is not None
            found = await p.store.account_class(acc)
            assert found is not None and found[0] == "C"
            back = await p.call(
                "PUT",
                f"/staff/accounts/{acc}/class",
                "staff-1",
                {"class": "B", "reason": "reviewed: scalping within the rules"},
                ("staff",),
            )
            assert back.status_code == 200, back.text
            assert back.json()["class"] == "B"

    asyncio.run(scenario())
