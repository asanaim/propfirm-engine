"""Admin settings against PostgreSQL, Valkey and a running engine, through
the API with signed tokens."""

import asyncio

import asyncpg
import httpx
import pytest

from propfirm.market.types import PriceUpdate
from propfirm.policy import load_policy
from propfirm.service.feeder import now_ms
from propfirm.settings.overlay import CountryGating, PolicyPage, apply_changes, stage_limits
from propfirm.settings.store import ProposalDecided, SettingsStore, migrate, policy_in_force
from propfirm.staff.directory import Owner
from tests.integration.test_customers import ADULT, Platform, eventually, platform

pytestmark = pytest.mark.integration

BASE = load_policy()
ADMIN = ("admin",)
LIMITS_URL = "/admin/products/one-step/stages/s1/limits"
NEW_LIMITS = {
    "profit_target_percent": "8",
    "daily_loss_percent": "4",
    "max_loss_percent": "5",
    "max_loss_kind": "static",
    "reason": "launch offer",
}
ONE_STEP_25K = {"product_id": "one-step", "size_cents": 2_500_000}


async def two_admins(p: Platform, url: str, body: object) -> httpx.Response:
    """admin-1 proposes, admin-2 approves (maker-checker): the approval."""
    proposed = await p.call("PUT", url, "admin-1", body, ADMIN)
    assert proposed.json()["status"] == "proposed", proposed.text
    pid = proposed.json()["proposal"]["id"]
    return await p.call(
        "POST", f"/admin/settings/proposals/{pid}/approve", "admin-2", {"reason": "ok"}, ADMIN
    )


OWNERS = (Owner("owner-a", "Owner A"), Owner("owner-b", "Owner B"))


async def two_owners(p: Platform, url: str, body: object) -> httpx.Response:
    """Rule AC01: owner A proposes a product change, owner B approves it."""
    proposed = await p.call("PUT", url, "owner-a", body, ())
    assert proposed.json()["status"] == "proposed", proposed.text
    pid = proposed.json()["proposal"]["id"]
    return await p.call(
        "POST", f"/admin/settings/proposals/{pid}/approve", "owner-b", {"reason": "ok"}, ()
    )


def test_store_is_append_only_and_gives_the_policy_in_force(
    database_url: str, pg_schema: str
) -> None:
    schema = f"{pg_schema}_settings"

    async def scenario() -> None:
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
        try:
            store = await SettingsStore.open(pool, schema)
            gating = CountryGating(True, frozenset({"BD"}))
            limits = stage_limits("one-step", "s1", "8", "4", "5", "static")
            first = await store.record(gating, "a-1", None, "counsel", 10, applied=True)
            second = await store.record(limits, "a-2", "a2@example.com", "offer", 20, applied=False)
            assert (first.id, second.id) == (1, 2) and second.setting == limits
            assert await store.version() == 1
            assert await store.applied_changes() == [first]
            assert await store.unapplied_changes() == [second]
            assert await policy_in_force(database_url, schema, BASE) == apply_changes(BASE, [first])
            await store.mark_applied(2, 41, 30)
            await store.mark_applied(2, 99, 31)  # a repeat changes nothing
            assert await store.version() == 2 and await store.unapplied_changes() == []
            latest, earlier = await store.history()
            assert (latest.change, latest.applied_at, latest.journal_seq) == (second, 30, 41)
            assert (earlier.change, earlier.applied_at) == (first, 10)
            in_force = await policy_in_force(database_url, schema, BASE)
            rule_set = in_force.product("one-step").rule_sets[0]
            assert rule_set.rsv_id == "one-step-s1-v2-a2"
            # maker-checker: one decision per proposal; the change names both admins
            prop = await store.propose(gating, "a-1", None, "counsel", 40)
            change = await store.approve(prop, "a-2", "checked", 41, applied=True)
            assert (change.author, change.approved_by, change.proposal_id) == ("a-1", "a-2", 1)
            with pytest.raises(ProposalDecided):
                await store.approve(prop, "a-3", "again", 42, applied=True)
            with pytest.raises(ProposalDecided):
                await store.decide(prop.id, "rejected", "a-3", "late", 43)
            found = await store.proposal(prop.id)
            assert found is not None and (found.decision, found.change_id) == ("approved", 3)
            assert await store.proposals(open_only=True) == []
            # rule AC10: policy pages are settings changes; acceptances kept once
            page = PolicyPage("terms", "Terms", "Simulated accounts only.")
            published = await store.record(page, "o-1", None, "launch", 50, applied=True)
            assert published.setting == page
            await store.accept("u1", "terms", 1, 60)
            await store.accept("u1", "terms", 1, 61)  # a repeat keeps the first
            assert await store.accepted("u1") == {("terms", 1)}
            assert await store.accepted("u2") == set()
            for sql in (
                f"UPDATE {schema}.changes SET reason = 'edited'",
                f"DELETE FROM {schema}.changes",
                f"UPDATE {schema}.applied SET journal_seq = 1",
                f"DELETE FROM {schema}.applied",
                f"DELETE FROM {schema}.proposals",
                f"UPDATE {schema}.proposal_decisions SET decision = 'approved'",
                f"DELETE FROM {schema}.policy_acceptances",
            ):
                with pytest.raises(asyncpg.RaiseError, match="append-only"):
                    await pool.execute(sql)
            async with pool.acquire() as conn:
                assert await migrate(conn, schema) == []
        finally:
            await pool.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            await pool.close()

    asyncio.run(scenario())


def test_new_limits_apply_to_accounts_bought_after_the_change(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, owners=OWNERS) as p:
            await p.call("POST", "/me", body=ADULT)

            async def buy() -> str:
                order = (await p.call("POST", "/orders", body=ONE_STEP_25K)).json()
                paid = (await p.call("POST", order["pay_url"])).json()
                assert paid["status"] == "provisioned", paid
                acc: str = paid["acc"]
                return acc

            before = await buy()
            refused = await p.call("PUT", LIMITS_URL, "u1", NEW_LIMITS)  # a trader
            assert refused.status_code == 403
            by_admin = await p.call("PUT", LIMITS_URL, "admin-1", NEW_LIMITS, ADMIN)
            assert by_admin.json()["detail"]["code"] == "owners_only"
            changed = await two_owners(p, LIMITS_URL, NEW_LIMITS)
            assert changed.status_code == 200, changed.text
            assert changed.json()["rsv_id"] == "one-step-s1-v2-a1"
            after = await buy()

            t = now_ms()
            await p.writer.publish(PriceUpdate("EUR/USD", 1, t, t, 110_000))
            old = await eventually(lambda: p.live.account(before))
            new = await eventually(lambda: p.live.account(after))
            # 25,000 USD: the file's 10 % target, 3 % daily and 6 % drawdown,
            # then 8 %, 4 % and 5 %
            floors = ("rsv_id", "target_balance_cents", "daily_floor_cents", "max_floor_cents")
            assert tuple(old[k] for k in floors) == (
                "one-step-s1-v2",
                2_750_000,
                2_425_000,
                2_350_000,
            )
            assert tuple(new[k] for k in floors) == (
                "one-step-s1-v2-a1",
                2_700_000,
                2_400_000,
                2_375_000,
            )
            # each account shows its own version's numbers, even when the
            # catalogue has moved on
            assert (old["rules"]["profit_target_percent"], old["rules"]["max_loss_percent"]) == (
                "10",
                "6",
            )
            assert (new["rules"]["profit_target_percent"], new["rules"]["max_loss_percent"]) == (
                "8",
                "5",
            )
            seen = await p.call("GET", f"/accounts/{before}")  # the owner's view
            assert seen.json()["target_balance_cents"] == 2_750_000

            await p.stop_engine()
            await p.engine()  # restarts under the policy in force: nothing to refuse
            await eventually(lambda: p.live.account(after))
            history = (
                await p.call("GET", "/admin/settings/changes", "admin-1", None, ADMIN)
            ).json()
            assert [
                (c["id"], c["status"], c["author"], c["approved_by"], c["proposal_id"])
                for c in history
            ] == [(1, "applied", "owner-a", "owner-b", 1)]

    asyncio.run(scenario())


def test_limits_changed_while_the_engine_is_down_apply_once_it_has_them(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(
            database_url, valkey_url, pg_schema, wait_s=0.3, start_engine=False, owners=OWNERS
        ) as p:
            pending = await two_owners(p, LIMITS_URL, NEW_LIMITS)
            assert pending.status_code == 202 and pending.json()["status"] == "pending"
            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body=ONE_STEP_25K)).json()
            assert order["rsv_id"] == "one-step-s1-v2"  # the previous limits stay in force
            await p.engine()  # journals the waiting AddRuleSet from the stream

            async def published() -> bool:
                return await p.settings.publish_pending(now_ms()) == 1

            await eventually(published)  # a retry finds it journaled: duplicate = done
            catalogue = (await p.call("GET", "/catalogue")).json()
            one_step = next(x for x in catalogue if x["id"] == "one-step")
            assert one_step["stages"][0]["rsv_id"] == "one-step-s1-v2-a1"
            second = (await p.call("POST", "/orders", body=ONE_STEP_25K)).json()
            assert second["rsv_id"] == "one-step-s1-v2-a1"

    asyncio.run(scenario())


def test_an_admin_switches_country_gating(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, start_engine=False) as p:
            on = {"country_gating": True, "blocked_countries": ["BD"], "reason": "counsel C8"}
            url = "/admin/access/country-gating"
            assert (await p.call("PUT", url, "staff-1", on, ("staff",))).status_code == 403
            proposed = await p.call("PUT", url, "admin-1", on, ADMIN)
            assert proposed.json()["status"] == "proposed"
            bd = {**ADULT, "residence_country": "BD"}
            assert (await p.call("POST", "/me", "bd-0", bd)).status_code == 201  # not yet
            # the second admin sees it on their staff page, then approves
            todo = (await p.call("GET", "/staff/me/todo", "admin-2", None, ADMIN)).json()
            assert ("settings_proposal", "1") in {(t["kind"], t["ref"]) for t in todo}
            mine = (await p.call("GET", "/staff/me/todo", "admin-1", None, ADMIN)).json()
            assert "settings_proposal" not in {t["kind"] for t in mine}
            notes = (await p.call("GET", "/staff/notifications", "admin-2", None, ADMIN)).json()
            assert [n["ref"] for n in notes] == ["setting-proposal:1"]
            approve = "/admin/settings/proposals/1/approve"
            assert (
                await p.call("POST", approve, "admin-2", {"reason": "ok"}, ADMIN)
            ).status_code == 200
            assert (
                await p.call("GET", "/staff/notifications", "admin-2", None, ADMIN)
            ).json() == []
            refused = await p.call("POST", "/me", "bd-1", bd)
            assert refused.status_code == 403 and "declared:BD" in refused.text
            assert (
                await p.call("POST", "/me", "in-1", {**ADULT, "residence_country": "IN"})
            ).status_code == 201
            off = {**on, "country_gating": False}
            assert (await two_admins(p, url, off)).status_code == 200
            assert (await p.call("POST", "/me", "bd-2", bd)).status_code == 201
            access = (await p.call("GET", "/admin/access", "admin-1", None, ADMIN)).json()
            assert access == {"country_gating": False, "blocked_countries": ["BD"], "min_age": 18}

    asyncio.run(scenario())


def test_the_news_action_log_is_append_only_and_names_manual_events(
    database_url: str, pg_schema: str
) -> None:
    from propfirm.settings.newsdesk import NewsAction, NewsStore

    schema = f"{pg_schema}_news"

    def action(event_id: str, kind: str, title: str | None, result: str, ts: int) -> NewsAction:
        return NewsAction(
            0, kind, event_id, title, None, "s-1", None, "why", f"{ts}-0", result, None, ts
        )

    async def scenario() -> None:
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
        try:
            await SettingsStore.open(pool, schema)  # runs the settings migrations
            store = NewsStore(pool, schema)
            first = await store.record(action("boj", "published", "BoJ", "done", 1))
            await store.record(action("boj", "published", "BoJ again", "rejected", 2))
            await store.record(action("snb", "manual", "SNB", "rejected", 3))
            await store.record(action("cpi", "calendar", "CPI", "done", 4))
            assert first.id == 1 and first.title == "BoJ"
            assert await store.titles() == {"boj": "BoJ"}  # accepted manual or published only
            assert [a.event_id for a in await store.history(2)] == ["cpi", "snb"]
            for sql in (
                f"UPDATE {schema}.news_actions SET reason = 'edited'",
                f"DELETE FROM {schema}.news_actions",
            ):
                with pytest.raises(asyncpg.RaiseError, match="append-only"):
                    await pool.execute(sql)
            with pytest.raises(asyncpg.CheckViolationError):
                await store.record(action("x", "delete", None, "done", 5))
        finally:
            await pool.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            await pool.close()

    asyncio.run(scenario())


def test_news_source_alerts_are_kept_append_only(database_url: str, pg_schema: str) -> None:
    from propfirm.newsfeed.alerts import SourceAlerts, SourceStore

    schema = f"{pg_schema}_srcalerts"

    async def scenario() -> None:
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
        try:
            await SettingsStore.open(pool, schema)
            alerts = SourceAlerts(SourceStore(pool, schema), None, ())
            await alerts.update([("bls", "403")], ["ecb"], 1_000)
            await alerts.update([("bls", "403")], [], 1_000 + 24 * 3_600_000)
            (alert,) = await alerts.open_alerts()
            assert (alert.source, alert.since, alert.last_notice) == (
                "bls",
                1_000,
                1_000 + 86_400_000,
            )
            await alerts.update([], ["bls"], 2 * 86_400_000)
            assert await alerts.open_alerts() == []
            await alerts.update([("bls", "500")], [], 3 * 86_400_000)
            (again,) = await alerts.open_alerts()
            assert (again.since, again.last_error) == (3 * 86_400_000, "500")
            with pytest.raises(asyncpg.RaiseError, match="append-only"):
                await pool.execute(f"DELETE FROM {schema}.news_source_events")
        finally:
            await pool.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            await pool.close()

    asyncio.run(scenario())
