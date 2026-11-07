"""Integrity, fraud and compliance cases through the API, against
PostgreSQL, Valkey and a running engine: alerts, the four-eyes decision,
voiding and blocking, what cases hold back, automatic alerts, the record."""

import asyncio
from typing import Any

import asyncpg
import pytest

from propfirm.service.feeder import now_ms
from tests.integration.test_customers import ADULT, LITE_10K, Platform, eventually, platform
from tests.integration.test_onboarding import INSTANT_5K, verify
from tests.integration.test_progression import STAFF, Market, quick_lite

pytestmark = pytest.mark.integration
DAY = 86_400_000


async def staff(
    p: Platform, method: str, url: str, who: str = "staff-1", body: object = None
) -> Any:
    return await p.call(method, url, who, body, STAFF)


async def alert(p: Platform, **fields: Any) -> Any:
    body = {"cif": "CIF00000001", "kind": "fraud", "severity": "medium", "reason": "r", **fields}
    return await staff(p, "POST", "/staff/cases", body=body)


async def funded_instant(p: Platform) -> str:
    """CIF00000001 with an Instant account open (ACC00000001), in profit."""
    market = Market(p)
    await market.price(110_000)
    await p.call("POST", "/me", body=ADULT)
    order = (await p.call("POST", "/orders", body=INSTANT_5K)).json()
    await p.call("POST", order["pay_url"])
    onb = (await verify(p, "ONB00000001"))["onb"]
    sha = (await p.call("GET", f"/me/onboardings/{onb}/contract")).json()["sha256"]
    signed = await p.call(
        "POST", f"/me/onboardings/{onb}/sign", body={"full_name": "Ada Trader", "sha256": sha}
    )
    acc: str = signed.json()["acc"]
    await eventually(lambda: p.live.account(acc))
    trade = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 100}
    opened = (await p.call("POST", f"/accounts/{acc}/orders", body=trade)).json()
    position = next(e["position_id"] for e in opened["events"] if e["type"] == "PositionOpened")
    await market.ramp(300)
    await p.call("POST", f"/accounts/{acc}/positions/{position}/close", body={})
    return acc


async def cases_check(p: Platform, acc: str) -> Any:
    view = (await p.call("GET", f"/accounts/{acc}/payout")).json()
    return next(c for c in view["checks"] if c["name"] == "cases"), view


def test_an_alert_holds_payouts_until_an_employee_clears_it(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            acc = await funded_instant(p)
            assert (await cases_check(p, acc))[0]["status"] == "pass"

            first = await alert(p, acc=acc, severity="low", evidence={"ips": ["10.0.0.1"]})
            assert first.status_code == 201, first.text
            case = first.json()
            assert (case["cas"], case["status"], case["severity"], case["alert"]) == (
                "CAS00000001",
                "open",
                "low",
                "ALR00000001",
            )
            held, view = await cases_check(p, acc)
            assert (held["status"], held["detail"], view["eligible"]) == (
                "fail",
                "held for review",
                False,
            )
            second = (await alert(p, kind="aml", severity="high")).json()
            assert (second["cas"], second["severity"], second["alert"]) == (
                "CAS00000001",
                "high",
                "ALR00000002",
            )  # the same case; it can only get more urgent
            assert second["due_at"] < case["due_at"]

            assert (await alert(p, cif="CIF00000009")).status_code == 404
            assert (await alert(p, acc="ACC00000099")).status_code == 422
            own = await p.call(
                "POST",
                "/staff/cases",
                "u1",
                {"cif": "CIF00000001", "kind": "fraud", "severity": "low", "reason": "r"},
                ("trader", "staff"),
            )
            assert own.status_code == 403  # nobody raises a case about themselves
            assert (await p.call("GET", "/staff/cases")).status_code == 403  # traders never see

            url = "/staff/cases/CAS00000001"
            assert (await staff(p, "POST", f"{url}/assign")).json()["assignee"] == "staff-1"
            noted = await staff(p, "POST", f"{url}/notes", body={"text": "same IP as CIF2"})
            assert noted.status_code == 200
            cleared = await staff(
                p, "POST", f"{url}/decide", body={"outcome": "cleared", "reason": "shared office"}
            )
            assert (cleared.json()["status"], cleared.json()["outcome"]) == ("closed", "cleared")
            assert (await cases_check(p, acc))[0]["status"] == "pass"

            detail = (await staff(p, "GET", url)).json()
            assert [a["alr"] for a in detail["alerts"]] == ["ALR00000001", "ALR00000002"]
            assert [h["kind"] for h in detail["history"]] == [
                "opened",
                "alert",
                "alert",
                "assigned",
                "note",
                "closed",
            ]
            reopened = await staff(p, "POST", f"{url}/reopen", "staff-2", {"reason": "appeal"})
            assert reopened.json()["status"] == "open"

    asyncio.run(scenario())


def test_actions_need_a_second_employee_and_take_effect(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            acc = await funded_instant(p)
            profit = (await cases_check(p, acc))[1]["payable_cents"]
            assert profit > 25_000
            await alert(p, acc=acc, kind="integrity", severity="high")
            url = "/staff/cases/CAS00000001"
            actions = [
                {"action": "remove_profit", "acc": acc, "amount_cents": 10_000},
                {"action": "void_account", "acc": acc},
                {"action": "block_customer"},
            ]
            bad = await staff(
                p,
                "POST",
                f"{url}/decide",
                body={
                    "outcome": "actioned",
                    "actions": [{"action": "void_account"}],
                    "reason": "x",
                },
            )
            assert bad.status_code == 422
            await p.call("POST", "/me", "u2", ADULT)
            other = (await p.call("POST", "/orders", "u2", LITE_10K)).json()
            other_acc = (await p.call("POST", other["pay_url"], "u2")).json()["acc"]
            not_theirs = await staff(
                p,
                "POST",
                f"{url}/decide",
                body={
                    "outcome": "actioned",
                    "actions": [{"action": "void_account", "acc": other_acc}],
                    "reason": "x",
                },
            )
            assert not_theirs.status_code == 422  # another customer's account
            own = await p.call("POST", f"{url}/assign", "u1", None, ("trader", "staff"))
            assert own.status_code == 403 and own.json()["detail"]["code"] == "own_case"
            proposed = await staff(
                p,
                "POST",
                f"{url}/decide",
                body={"outcome": "actioned", "actions": actions, "reason": "latency arbitrage"},
            )
            assert proposed.json()["status"] == "decision_pending", proposed.text
            agree = {"reason": "agree"}
            senior = await staff(p, "POST", f"{url}/confirm", "staff-2", agree)
            assert (senior.status_code, senior.json()["detail"]["permission"]) == (
                403,
                "case.authorise",
            )  # a void and a profit removal: an admin authorises [decided 31 Oct 2026]
            mine = await p.call("POST", f"{url}/confirm", "staff-1", agree, ("admin",))
            assert mine.status_code == 409  # not the proposer
            assert (await p.call("GET", "/me")).json()["status"] == "registered"  # nothing yet

            confirmed = await p.call("POST", f"{url}/confirm", "admin-1", agree, ("admin",))
            assert (confirmed.json()["status"], confirmed.json()["outcome"]) == (
                "closed",
                "actioned",
            ), confirmed.text
            live = await eventually(
                lambda: _voided(p, acc)
            )  # the engine closed the account's positions and locked it
            assert live["locked"] and live["positions"] == []
            assert (await p.call("GET", "/me")).json()["status"] == "blocked"
            _, view = await cases_check(p, acc)
            assert view["violations_cents"] == 10_000
            assert view["payable_cents"] == profit - 10_000
            funded_check = next(c for c in view["checks"] if c["name"] == "funded")
            assert funded_check["status"] == "fail"
            history = (await staff(p, "GET", url)).json()["history"]
            kinds = [h["kind"] for h in history]
            assert kinds[-5:] == ["confirmed", "applied", "applied", "applied", "closed"]
            assert history[-5]["detail"]["as"] == "authorise"
            void = next(h for h in history if h["kind"] == "applied" and h["detail"]["index"] == 1)
            assert void["detail"]["result"]["voided"] is True
            assert void["detail"]["result"]["journal_seq"] > 0

    asyncio.run(scenario())


async def _voided(p: Platform, acc: str) -> Any:
    live = await p.live.account(acc)
    return live if live and live["status"] == "voided" else None


def test_an_open_case_holds_funded_reviews_and_contracts(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, quick_lite(), funded=True) as p:
            market = Market(p)
            await market.price(110_000)
            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body=LITE_10K)).json()
            s1 = (await p.call("POST", order["pay_url"])).json()["acc"]
            await market.pass_stage(s1)
            await p.lifecycle.poll(now_ms())
            await p.lifecycle.advance(now_ms())
            await market.pass_stage("ACC00000002")
            await p.lifecycle.poll(now_ms())
            await alert(p)
            url = "/staff/progressions/PRG00000002"
            held = await staff(p, "POST", f"{url}/approve", body={"reason": "ok"})
            assert held.status_code == 409 and held.json()["detail"]["code"] == "open_case"
            denied_ok = await staff(p, "GET", url)
            assert denied_ok.json()["status"] == "review"
            await staff(
                p,
                "POST",
                "/staff/cases/CAS00000001/decide",
                body={"outcome": "warning", "reason": "first time"},
            )
            # rule AC00: one proposes, another carries it out
            proposed = await staff(p, "POST", f"{url}/approve", "staff-2", {"reason": "ok"})
            assert proposed.json()["proposal"]["by"] == "staff-2", proposed.text
            approved = await staff(p, "POST", f"{url}/approve", body={"reason": "ok"})
            assert approved.status_code == 200 and approved.json()["status"] == "approved"

            # a case opened during onboarding holds the contract
            assert p.onboarding is not None
            await p.onboarding.advance(now_ms())
            await staff(p, "POST", "/staff/cases/CAS00000001/reopen", body={"reason": "new"})
            mine = await verify(p, "ONB00000001")
            assert (mine["status"], mine["contract"]) == ("contract", None)
            await staff(
                p,
                "POST",
                "/staff/cases/CAS00000001/decide",
                "staff-2",
                {"outcome": "cleared", "reason": "resolved"},
            )
            await p.onboarding.advance(now_ms())
            issued = (await p.call("GET", "/me/onboardings/ONB00000001")).json()
            assert issued["status"] == "signature"

    asyncio.run(scenario())


def test_the_platform_raises_its_own_alerts(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            # a screening hit at the first purchase
            await p.call("POST", "/me", "u1", {**ADULT, "full_name": "Ada SANCTIONED"})
            refused = await p.call("POST", "/orders", "u1", INSTANT_5K)
            assert refused.status_code in (403, 409, 422)
            (aml,) = (await staff(p, "GET", "/staff/cases?cif=CIF00000001")).json()
            detail = (await staff(p, "GET", f"/staff/cases/{aml['cas']}")).json()
            assert [(a["source"], a["kind"], a["severity"]) for a in detail["alerts"]] == [
                ("screening", "aml", "high")
            ]

            # the same verified person as another customer
            for user in ("u2", "u3"):
                await p.call("POST", "/me", user, ADULT)
                order = (await p.call("POST", "/orders", user, INSTANT_5K)).json()
                await p.call("POST", order["pay_url"], user)
            first = await verify(p, "ONB00000001", "u2")
            assert first["status"] == "signature"
            second = await verify(p, "ONB00000002", "u3")
            assert (second["status"], second["contract"]) == ("contract", None)  # held
            (fraud,) = (await staff(p, "GET", "/staff/cases?cif=CIF00000003")).json()
            found = (await staff(p, "GET", f"/staff/cases/{fraud['cas']}")).json()
            (duplicate,) = found["alerts"]
            assert (duplicate["source"], duplicate["kind"]) == ("duplicate_identity", "fraud")
            assert duplicate["detail"]["same_identity_as"] == ["CIF00000002"]

    asyncio.run(scenario())


def test_overdue_cases_and_the_record(database_url: str, valkey_url: str, pg_schema: str) -> None:
    offset = {"ms": 0}

    async def scenario() -> None:
        async with platform(
            database_url, valkey_url, pg_schema, clock=lambda: now_ms() + offset["ms"]
        ) as p:
            await p.call("POST", "/me", body=ADULT)
            await p.call("POST", "/me", "u2", ADULT)
            await alert(p, severity="high")
            await alert(p, cif="CIF00000002", severity="low")
            assert (await staff(p, "GET", "/staff/cases?overdue=true")).json() == []
            offset["ms"] = 2 * DAY  # past the 24 h of a high-severity case only
            overdue = (await staff(p, "GET", "/staff/cases?overdue=true")).json()
            assert [c["cas"] for c in overdue] == ["CAS00000001"]
            listed = (await staff(p, "GET", "/staff/cases?status=open")).json()
            assert [c["cas"] for c in listed] == ["CAS00000001", "CAS00000002"]  # soonest due

            await staff(
                p,
                "POST",
                "/staff/cases/CAS00000002/decide",
                body={"outcome": "cleared", "reason": "x"},
            )
            await alert(p, cif="CIF00000002", severity="low")  # opens a new case
            again = await staff(
                p, "POST", "/staff/cases/CAS00000002/reopen", body={"reason": "appeal"}
            )
            assert again.status_code == 409  # the customer has another case open

            s = p.config.customer_schema
            conn = await asyncpg.connect(database_url)
            try:
                for sql in (
                    f"UPDATE {s}.case_events SET ts = 0",
                    f"DELETE FROM {s}.case_events",
                    f"UPDATE {s}.alerts SET severity = 'low'",
                    f"DELETE FROM {s}.alerts",
                    f"UPDATE {s}.cases SET cif = 'CIF00000002'",
                    f"DELETE FROM {s}.cases",
                ):
                    with pytest.raises(asyncpg.RaiseError):
                        await conn.execute(sql)
                await conn.execute(
                    f"UPDATE {s}.cases SET status = 'closed' WHERE cas = 'CAS00000001'"
                )
            finally:
                await conn.close()
            assert p.cases is not None
            assert await p.cases.store.rebuild_status() == ["CAS00000001"]
            assert (await p.cases.store.case("CAS00000001")).status == "open"  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_a_stop_loss_inside_a_news_window_raises_an_integrity_alert(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    """As FTMO: a fill inside a restricted window on a funded account can end
    the account. The engine lets the stop-loss execute and records a
    NewsViolation; the sweep turns it into a high-severity integrity alert
    once, and staff decide."""
    from propfirm.journal.inputs import ScheduleNews
    from propfirm.market.types import PriceUpdate
    from propfirm.rules.news import CURRENT_MAP, EventScope, RestrictedEvent

    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            assert p.cases is not None
            acc = await funded_instant(p)
            trade = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 10}
            account = await p.live.account(acc)
            assert account is not None
            opened = (await p.call("POST", f"/accounts/{acc}/orders", body=trade)).json()
            fill = next(e for e in opened["events"] if e["type"] == "PositionOpened")
            stops = {"stop_loss": fill["price"] - 100, "take_profit": None}
            put = await p.call(
                "PUT", f"/accounts/{acc}/positions/{fill['position_id']}/stops", body=stops
            )
            assert put.json()["status"] == "done", put.text
            now = now_ms()
            nfp = RestrictedEvent(
                "t-nfp", now + 60_000, EventScope.CURRENCY, "USD", instrument_map=CURRENT_MAP
            )
            await p.writer.publish(ScheduleNews(nfp, now))
            t = now_ms()
            await p.writer.publish(PriceUpdate("EUR/USD", 10_000, t, t, fill["price"] - 400))

            async def violated() -> list[str]:
                assert p.cases is not None
                return await p.cases.news_violations(now_ms())

            (alr,) = await eventually(violated)
            assert await p.cases.news_violations(now_ms()) == []  # never twice
            listed = (await staff(p, "GET", "/staff/cases")).json()
            (case,) = [c for c in listed if c["cif"] == "CIF00000001"]
            detail = (await staff(p, "GET", f"/staff/cases/{case['cas']}")).json()
            (news,) = [a for a in detail["alerts"] if a["alr"] == alr]
            assert (news["source"], news["kind"], news["severity"], news["acc"]) == (
                "news_rule",
                "integrity",
                "high",
                acc,
            )
            assert news["detail"]["fill"] == "stop_loss" and news["detail"]["events"] == ["t-nfp"]

    asyncio.run(scenario())
