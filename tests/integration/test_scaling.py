"""Scaling end to end (D-14.8): the job proposes a due step for a funded Lite
account, a member of staff approves, the engine adds the allocation."""

import asyncio
from dataclasses import replace
from fractions import Fraction

import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_customers import eventually, platform
from tests.integration.test_onboarding import funded_review_approved
from tests.integration.test_progression import STAFF, quick_lite

pytestmark = pytest.mark.integration
DAY = 86_400_000


def test_a_due_step_is_proposed_approved_and_added(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    base = quick_lite()
    # every condition met at once, but the period: one day
    policy = replace(
        base,
        scaling=replace(
            base.scaling, review_days=1, min_net_profit=Fraction(0), min_paid_payouts=0
        ),
    )

    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, policy, funded=True) as p:
            assert p.onboarding is not None and p.scaling is not None
            await funded_review_approved(p)
            await p.onboarding.advance(now_ms())
            first = await p.call("POST", "/me/onboardings/ONB00000001/verification")
            await p.call("POST", first.json()["verification_url"], body={"result": "approved"})
            contract = (await p.call("GET", "/me/onboardings/ONB00000001/contract")).json()
            good = {"full_name": "Ada Trader", "sha256": contract["sha256"]}
            signed = await p.call("POST", "/me/onboardings/ONB00000001/sign", body=good)
            acc = signed.json()["acc"]
            before = await eventually(lambda: p.live.account(acc))

            assert await p.scaling.run(now_ms()) == []  # not a day yet
            assert await p.scaling.run(now_ms() + 2 * DAY, every_ms=0) == ["SCL00000001"]
            assert await p.scaling.run(now_ms() + 2 * DAY, every_ms=0) == []  # one open

            listed = (await p.call("GET", "/staff/scaling", "staff-1", None, STAFF)).json()
            (step,) = listed
            assert (step["acc"], step["step"], step["add_cents"], step["status"]) == (
                acc,
                1,
                200_000,  # 20 % of 10,000
                "proposed",
            )
            approved = await p.call(
                "POST", "/staff/scaling/SCL00000001/approve", "staff-1", None, STAFF
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == "applied"
            again = await p.call(
                "POST", "/staff/scaling/SCL00000001/approve", "staff-2", None, STAFF
            )
            assert again.json()["detail"]["code"] == "decided"

            async def scaled() -> dict | None:  # type: ignore[type-arg]
                view = await p.live.account(acc)
                grown = view is not None and view["balance_cents"] > before["balance_cents"]
                return view if grown else None

            view = await eventually(scaled)
            assert view["balance_cents"] - before["balance_cents"] == 200_000
            assert await p.scaling.steps_taken(step["ord"]) == 1

    asyncio.run(scenario())
