"""Reserve states acted on (D-14.23): the watch records each change and
alerts the settings change audience; each state's limits hold at the
API; owners' top-ups and releases need two people and post to the books;
in red a funded account waits until the state improves."""

import asyncio
from typing import Any

import asyncpg
import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_customers import ADULT, LITE_10K, Platform, platform
from tests.integration.test_onboarding import funded_review_approved, verify
from tests.integration.test_progression import quick_lite

pytestmark = pytest.mark.integration

ADMIN = ("admin",)


async def force(p: Platform, database_url: str, state: str) -> None:
    """Record a state as the watch would (the table is append-only)."""
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            f"INSERT INTO {p.config.customer_schema}.reserve_states "
            f"(at, state, k, reserve_cents, required_cents) VALUES ($1, $2, NULL, 0, 0)",
            now_ms(),
            state,
        )
    finally:
        await conn.close()


def test_states_limits_and_moves(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            assert p.reserve is not None and p.books is not None

            async def staff(method: str, path: str, who: str, body: Any = None) -> Any:
                return await p.call(method, path, who, body, ADMIN)

            async def alerts() -> list[dict[str, Any]]:
                listed = (await staff("GET", "/staff/notifications", "admin-1")).json()
                return [n for n in listed if n["kind"] == "reserve"]

            assert await p.reserve.state() == "normal"
            assert await p.reserve.watch(p.clock(), every_ms=0) is None  # nothing owed yet
            status = (await p.call("GET", "/site/status")).json()
            assert (status["payouts_delayed"], status["payouts_delayed_since"]) == (False, None)

            # one Lite sale cannot cover 1.5 times its own expected payout
            await p.call("POST", "/me", "u1", ADULT)
            order = (await p.call("POST", "/orders", "u1", LITE_10K)).json()
            await p.call("POST", order["pay_url"], "u1")
            await p.books.sync(p.clock(), every_ms=0)
            assert await p.reserve.watch(p.clock(), every_ms=0) == "breach"
            assert await p.reserve.watch(p.clock(), every_ms=0) is None  # no change, no row
            (alert,) = await alerts()
            assert alert["title"] == "Reserve breach" and alert["urgent"] is True
            assert "Payout delay published" in alert["body"]
            status = (await p.call("GET", "/site/status")).json()
            assert status["payouts_delayed"] is True and status["payouts_delayed_since"]

            # the limits of the state, at the API
            await p.call("POST", "/me", "u2", {**ADULT, "full_name": "Second"})
            coded = await p.call("POST", "/orders", "u2", {**LITE_10K, "code": "ANY10"})
            assert (coded.status_code, coded.json()["detail"]["code"]) == (409, "promotions_paused")
            instant = {"product_id": "instant", "size_cents": 500_000}
            stopped = await p.call("POST", "/orders", "u2", instant)
            assert (stopped.status_code, stopped.json()["detail"]["code"]) == (
                409,
                "sold_out_today",
            )
            frozen = await staff("POST", "/staff/scaling/SCL00000001/approve", "admin-1")
            assert (frozen.status_code, frozen.json()["detail"]["code"]) == (409, "scaling_frozen")
            listed = await p.call("POST", "/orders", "u2", LITE_10K)
            assert listed.status_code == 201  # a sale at the list price goes on

            # owners add capital: two people, then the books, then the watch
            move = {"kind": "topup", "amount_cents": 1_000_000, "reason": "owners' capital"}
            made = (await staff("POST", "/staff/reserve/moves", "admin-1", move)).json()
            path = f"/staff/reserve/moves/{made['rsm']}/decision"
            same = await staff("POST", path, "admin-1", {"approve": True, "reason": "ok"})
            assert (same.status_code, same.json()["detail"]["code"]) == (403, "same_person")
            done = await staff("POST", path, "admin-2", {"approve": True, "reason": "ok"})
            assert done.json()["status"] == "approved"
            await p.books.sync(p.clock(), every_ms=0)
            assert await p.reserve.watch(p.clock(), every_ms=0) == "normal"
            assert await alerts() == []  # closed on the way back
            assert (await p.call("GET", "/site/status")).json()["payouts_delayed"] is False
            entries = (await staff("GET", "/staff/books/entries", "admin-1")).json()
            assert [e["kind"] for e in entries].count("reserve_topup") == 1

            # a release: bounded by what lies above the release line
            c = await p.reserve.coverage(p.clock())
            assert c is not None and c.releasable_cents > 0
            too_much = {"kind": "release", "amount_cents": c.releasable_cents + 1, "reason": "x"}
            over = await staff("POST", "/staff/reserve/moves", "admin-1", too_much)
            assert (over.status_code, over.json()["detail"]["code"]) == (409, "too_much")
            fits = {**too_much, "amount_cents": c.releasable_cents}
            release = (await staff("POST", "/staff/reserve/moves", "admin-1", fits)).json()
            await staff(
                "POST",
                f"/staff/reserve/moves/{release['rsm']}/decision",
                "admin-2",
                {"approve": True, "reason": "monthly"},
            )
            await p.books.sync(p.clock(), every_ms=0)
            after = await p.reserve.coverage(p.clock())
            assert after is not None and after.releasable_cents == 0
            moves = (await staff("GET", "/staff/reserve/moves", "admin-1")).json()
            assert [(m["kind"], m["status"]) for m in moves] == [
                ("release", "approved"),
                ("topup", "approved"),
            ]

            # amber: Instant capped at the trailing average, at least one a day
            await force(p, database_url, "amber")
            first = await p.call("POST", "/orders", "u2", instant)
            assert first.status_code == 201, first.text
            await p.call("POST", first.json()["pay_url"], "u2")
            await p.call("POST", "/me", "u3", {**ADULT, "full_name": "Third"})
            capped = await p.call("POST", "/orders", "u3", instant)
            assert (capped.status_code, capped.json()["detail"]["code"]) == (409, "sold_out_today")

    asyncio.run(scenario())


def test_in_red_a_funded_account_waits(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, quick_lite(), funded=True) as p:
            assert p.onboarding is not None
            await funded_review_approved(p)
            await p.onboarding.advance(now_ms())
            await verify(p, "ONB00000001")
            contract = (await p.call("GET", "/me/onboardings/ONB00000001/contract")).json()
            await force(p, database_url, "red")
            signed = await p.call(
                "POST",
                "/me/onboardings/ONB00000001/sign",
                body={"full_name": "Ada Trader", "sha256": contract["sha256"]},
            )
            assert signed.json()["status"] == "signed"  # queued, not opened
            await p.onboarding.advance(now_ms())
            assert (await p.call("GET", "/me/onboardings/ONB00000001")).json()["status"] == "signed"
            await force(p, database_url, "amber")  # better: the queue moves
            await p.onboarding.advance(now_ms())
            mine = (await p.call("GET", "/me/onboardings/ONB00000001")).json()
            assert mine["status"] == "provisioned"

    asyncio.run(scenario())
