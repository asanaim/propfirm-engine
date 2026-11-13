"""Stage progression against PostgreSQL, Valkey and a running engine: a pass
opens the next evaluation account; the last evaluation pass waits for an
employee's review."""

import asyncio
from dataclasses import replace
from fractions import Fraction

import asyncpg
import pytest

from propfirm.market.types import PriceUpdate
from propfirm.policy import Policy, load_policy
from propfirm.rules.products import RuleSet, Stage
from propfirm.service.feeder import now_ms
from tests.integration.test_customers import ADULT, LITE_10K, Platform, eventually, platform

pytestmark = pytest.mark.integration

STAFF = ("staff",)


def quick_lite() -> Policy:
    """Every evaluation stage with a 1 % target, one trading day and no
    consistency rule, so a single trade passes it (a new journal per test,
    so the ids stay v1). Funded stages are unchanged."""
    policy = load_policy()

    def quick(rule_sets: tuple[RuleSet, ...]) -> tuple[RuleSet, ...]:
        return tuple(
            r
            if r.stage is Stage.FUNDED
            else replace(
                r,
                profit_target=Fraction(1, 100),
                min_trading_days=1,
                consistency=None,
                consistency_at_pass=False,
            )
            for r in rule_sets
        )

    products = tuple(replace(p, rule_sets=quick(p.rule_sets)) for p in policy.products)
    # every product on sale, Classic included (off in the shipped catalogue,
    # K2): accounts bought before keep progressing
    catalogue = replace(policy.catalogue, products_off=frozenset())
    return replace(policy, products=products, catalogue=catalogue)


class Market:
    def __init__(self, p: Platform) -> None:
        self.p, self.seq, self.mid = p, 0, 110_000

    async def price(self, mid: int) -> None:
        self.seq, self.mid = self.seq + 1, mid
        t = now_ms()
        await self.p.writer.publish(PriceUpdate("EUR/USD", self.seq, t, t, mid))

    async def ramp(self, points: int, steps: int = 20) -> None:
        """Move the price in small steps: the spread follows the mean price
        change over the last 20 updates, so one jump would widen it."""
        for _ in range(steps):
            await self.price(self.mid + points // steps)

    async def pass_stage(self, acc: str) -> None:
        """Buy 1 lot, raise EUR/USD 300 points in 20 steps, close: about
        +$280 on $10,000, above the 1 % target."""
        await eventually(lambda: self.p.live.account(acc))
        trade = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 100}
        opened = (await self.p.call("POST", f"/accounts/{acc}/orders", body=trade)).json()
        assert opened["status"] == "done", opened
        (position,) = [e["position_id"] for e in opened["events"] if e["type"] == "PositionOpened"]
        await self.ramp(300)
        closed = (
            await self.p.call("POST", f"/accounts/{acc}/positions/{position}/close", body={})
        ).json()
        kinds = [e["type"] for e in closed["events"]]
        assert "StagePassed" in kinds, [(e["type"], e.get("reason")) for e in closed["events"]]


def test_a_pass_opens_the_next_stage_and_the_last_waits_for_review(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, quick_lite()) as p:
            market = Market(p)
            await market.price(110_000)
            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body=LITE_10K)).json()
            s1 = (await p.call("POST", order["pay_url"])).json()["acc"]

            await market.pass_stage(s1)
            assert await p.lifecycle.poll(now_ms()) == ["PRG00000001"]
            assert await p.lifecycle.poll(now_ms()) == []  # the cursor moved past it
            head = await asyncpg.connect(database_url)
            try:
                last = await head.fetchval(f"SELECT max(seq) FROM {p.config.schema}.journal")
            finally:
                await head.close()
            assert await p.lifecycle.store.cursor() == last
            assert await p.lifecycle.advance(now_ms()) == 1
            me = (await p.call("GET", "/me")).json()
            s2 = me["accounts"][1]
            assert (s2["acc"], s2["stage"], s2["rsv_id"], s2["ord"], s2["progression"]) == (
                "ACC00000002",
                "s2",
                "lite-s2-v3",
                order["ord"],
                "PRG00000001",
            )
            first = me["progressions"][0]
            assert (first["status"], first["from_acc"], first["to_acc"]) == (
                "provisioned",
                s1,
                "ACC00000002",
            )

            await market.pass_stage("ACC00000002")
            assert await p.lifecycle.poll(now_ms()) == ["PRG00000002"]
            assert await p.lifecycle.advance(now_ms()) == 0  # funded: nothing opens
            # D-14.1: each pass is emailed, once, with the next step
            assert p.notifications is not None and p.mailer is not None
            await p.notifications.advance(now_ms())
            passes = [m for m in p.mailer.sent if m[1].startswith("You passed")]
            assert [m[1] for m in passes] == [
                f"You passed Stage 1 on account {s1}",
                "You passed Stage 2 on account ACC00000002",
            ]
            assert "Stage 2 account opens after a short review" in passes[0][2]
            assert "identity check and the trader agreement" in passes[1][2]
            waiting = (await p.call("GET", "/staff/progressions", "staff-1", roles=STAFF)).json()
            assert [(w["prg"], w["status"], w["to_stage"]) for w in waiting] == [
                ("PRG00000002", "review", "funded")
            ]

            url = "/staff/progressions/PRG00000002"
            decision = {"reason": "clean record, no flags"}
            assert (await p.call("POST", f"{url}/approve", body=decision)).status_code == 403
            own = await p.call("POST", f"{url}/approve", "u1", decision, ("trader", "staff"))
            assert own.status_code == 403 and own.json()["detail"]["code"] == "own_progression"
            # rule AC00: staff-2 proposes; the proposer cannot carry it out; staff-1 does
            proposed = await p.call("POST", f"{url}/approve", "staff-2", decision, STAFF)
            assert proposed.json()["status"] == "review", proposed.text
            assert proposed.json()["proposal"]["decision"] == "approve"
            own_prop = await p.call("POST", f"{url}/approve", "staff-2", decision, STAFF)
            assert own_prop.json()["detail"]["code"] == "four_eyes"
            approved = await p.call("POST", f"{url}/approve", "staff-1", decision, STAFF)
            assert approved.status_code == 200, approved.text
            assert (approved.json()["status"], approved.json()["decided_by"]) == (
                "approved",
                "staff-1",
            )
            again = await p.call("POST", f"{url}/deny", "staff-2", decision, STAFF)
            assert again.status_code == 409

            detail = (await p.call("GET", url, "staff-1", roles=STAFF)).json()
            assert [h["kind"] for h in detail["history"]] == ["created", "proposed", "approved"]
            created = detail["history"][0]["detail"]
            assert (created["from_rsv_id"], created["trading_days"], created["to_stage"]) == (
                "lite-s2-v3",
                1,
                "funded",
            )
            assert created["balance_cents"] > 1_010_000  # passed above the 1 % target

            store = p.lifecycle.store
            history = [e.kind for e in await store.history("PRG00000001")]
            assert history == [
                "created",
                "account_assigned",
                "provision_attempted",
                "provisioned",
            ]
            s = p.config.customer_schema
            conn = await asyncpg.connect(database_url)
            try:
                for sql in (
                    f"UPDATE {s}.progression_events SET ts = 0",
                    f"DELETE FROM {s}.progression_events",
                    f"UPDATE {s}.progressions SET from_acc = 'ACC00000009'",
                    f"DELETE FROM {s}.progressions",
                ):
                    with pytest.raises(asyncpg.RaiseError):
                        await conn.execute(sql)
                await conn.execute(f"UPDATE {s}.progressions SET status = 'denied'")
            finally:
                await conn.close()
            assert await store.rebuild_status() == ["PRG00000001", "PRG00000002"]
            assert (await store.progression("PRG00000002")).status == "approved"  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_an_engine_answer_missed_is_retried_and_a_pass_of_an_unknown_account_ignored(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, quick_lite(), wait_s=0.3) as p:
            market = Market(p)
            await market.price(110_000)
            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body=LITE_10K)).json()
            s1 = (await p.call("POST", order["pay_url"])).json()["acc"]
            await market.pass_stage(s1)
            await p.stop_engine()
            assert await p.lifecycle.poll(now_ms()) == ["PRG00000001"]
            assert await p.lifecycle.advance(now_ms()) == 0  # no engine answer in time
            await p.engine()

            async def opened() -> bool:
                return await p.lifecycle.advance(now_ms()) == 1

            await eventually(opened)  # the retry finds it opened (duplicate) or opens it
            kinds = [e.kind for e in await p.lifecycle.store.history("PRG00000001")]
            assert kinds.count("provision_attempted") >= 2 and kinds[-1] == "provisioned"
            assert kinds.count("account_assigned") == 1  # the same ACC on every try

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("product", "status", "to_stage", "opened_stage"),
    [
        ("lite", "provisioned", "s2", "s2"),  # 2-Step: S1 -> S2 at once
        ("classic", "provisioned", "s2", "s2"),  # 2-Step: S1 -> S2 at once
        ("one-step", "review", "funded", None),  # 1-Step: S1 -> funded review
    ],
)
def test_each_product_progresses_its_own_way(
    database_url: str,
    valkey_url: str,
    pg_schema: str,
    product: str,
    status: str,
    to_stage: str,
    opened_stage: str | None,
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, quick_lite()) as p:
            market = Market(p)
            await market.price(110_000)
            await p.call("POST", "/me", body=ADULT)
            buy = {"product_id": product, "size_cents": 1_000_000}
            order = (await p.call("POST", "/orders", body=buy)).json()
            first = (await p.call("POST", order["pay_url"])).json()["acc"]
            await market.pass_stage(first)
            (prg,) = await p.lifecycle.poll(now_ms())
            await p.lifecycle.advance(now_ms())
            me = (await p.call("GET", "/me")).json()
            (made,) = me["progressions"]
            assert (made["prg"], made["status"], made["to_stage"]) == (prg, status, to_stage)
            stages = [a["stage"] for a in me["accounts"]]
            assert stages == (["s1", opened_stage] if opened_stage else ["s1"])

    asyncio.run(scenario())


def test_instant_is_funded_from_purchase_and_has_no_stage_to_pass(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, quick_lite()) as p:
            market = Market(p)
            await market.price(110_000)
            await p.call("POST", "/me", body=ADULT)
            buy = {"product_id": "instant", "size_cents": 1_000_000}
            order = (await p.call("POST", "/orders", body=buy)).json()
            acc = (await p.call("POST", order["pay_url"])).json()["acc"]
            await eventually(lambda: p.live.account(acc))
            trade = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 100}
            opened = (await p.call("POST", f"/accounts/{acc}/orders", body=trade)).json()
            (position,) = [
                e["position_id"] for e in opened["events"] if e["type"] == "PositionOpened"
            ]
            await market.ramp(300)
            closed = (
                await p.call("POST", f"/accounts/{acc}/positions/{position}/close", body={})
            ).json()
            assert "StagePassed" not in [e["type"] for e in closed["events"]]  # no target
            assert await p.lifecycle.poll(now_ms()) == []
            me = (await p.call("GET", "/me")).json()
            assert [a["stage"] for a in me["accounts"]] == ["funded"] and me["progressions"] == []

    asyncio.run(scenario())
