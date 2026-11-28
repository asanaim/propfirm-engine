"""Payable profit, payout eligibility and the payout process of a funded
account, through the API, against PostgreSQL, Valkey and a running engine."""

import asyncio
from dataclasses import replace
from typing import Any

import asyncpg
import pytest

from propfirm.customers.providers import PayoutOrder, PayoutStatus
from propfirm.journal.inputs import VoidAccount
from propfirm.policy import Policy, load_policy
from propfirm.rules.products import Stage
from propfirm.service.feeder import now_ms
from tests.integration.test_cases import funded_instant
from tests.integration.test_customers import ADULT, Platform, eventually, platform
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

            # Instant version 2: the floor locked at the starting balance stays
            # there after a payout, so the account must stay one cent above it
            payable = pnl - 1

            async def settled() -> Any:
                view = (await payout()).json()
                return view if view["payable_cents"] == payable else None

            now = await eventually(settled)
            assert (now["split_percent"], now["trader_share_cents"]) == ("80", payable * 4 // 5)
            assert now["firm_share_cents"] == payable - payable * 4 // 5
            checks = {c["name"]: c["status"] for c in now["checks"]}
            assert checks == {
                "floor": "pass",
                "funded": "pass",
                "waiting_period": "fail",  # funded today
                "trading_days": "pass",
                "consistency": "fail",  # Instant: best day <= 50 %; one day is 100 %
                "minimum": "pass",
                "flat": "pass",
                "identity": "pass",
                "residence": "pass",
                "cases": "pass",
                "cohort": "pass",
                "no_pending_payout": "pass",
                "wallet": "fail",  # none yet
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


USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
OTHER = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"


def instant_without_consistency() -> Policy:
    """The Instant funded stage without its consistency rule, so one trading
    day can be paid out, and with the retired version 1 floor (it restarts
    after a payout), so the whole profit can be paid; the floor that stays
    (version 2) has its own tests (tests/test_instant_floor.py and the
    payable profit test above)."""
    policy = load_policy()
    instant = policy.product("instant")
    rule_sets = tuple(
        replace(r, rsv_id="instant-funded-v1", consistency=None, floor_restarts_after_payout=True)
        if r.stage is Stage.FUNDED
        else r
        for r in instant.rule_sets
    )
    products = tuple(
        replace(p, rule_sets=rule_sets) if p.id == "instant" else p for p in policy.products
    )
    return replace(policy, products=products)


async def staff(p: Platform, method: str, url: str, who: str, body: object = None) -> Any:
    return await p.call(method, url, who, body, STAFF)


def test_a_payout_from_request_to_paid(database_url: str, valkey_url: str, pg_schema: str) -> None:
    offset = {"ms": 0}  # moves the API's clock

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
        ) as p:
            assert p.payouts is not None
            acc = await funded_instant(p)

            async def view() -> Any:
                return (await p.call("GET", f"/accounts/{acc}/payout")).json()

            async def in_profit() -> Any:
                found = await view()
                return found if found["payable_cents"] > 0 else None

            profit = (await eventually(in_profit))["payable_cents"]

            # profit removed for a violation: leaves the account with the payout, once
            alert = {"cif": "CIF00000001", "kind": "integrity", "severity": "low", "reason": "r"}
            assert (await staff(p, "POST", "/staff/cases", "staff-1", alert)).status_code == 201
            cas = "/staff/cases/CAS00000001"
            await staff(p, "POST", f"{cas}/assign", "staff-1")
            remove = {"action": "remove_profit", "acc": acc, "amount_cents": 1_000}
            decision = {"outcome": "actioned", "actions": [remove], "reason": "news"}
            await staff(p, "POST", f"{cas}/decide", "staff-1", decision)
            ok = {"reason": "ok"}
            closed = await p.call("POST", f"{cas}/confirm", "admin-1", ok, ("admin",))
            assert closed.json()["status"] == "closed", closed.text

            # the wallet: MFA, a TRON address; usable at once
            body = {"address": USDT}
            no_mfa = await p.call("POST", "/me/wallet", body=body, mfa=False)
            assert (no_mfa.status_code, no_mfa.json()["detail"]["code"]) == (401, "mfa_required")
            challenge = 'Bearer error="insufficient_user_authentication"'
            assert no_mfa.headers["www-authenticate"] == challenge
            # step-up: the second factor entered within 5 minutes (RFC 9470 max_age)
            stale = await p.call("POST", "/me/wallet", body=body, login_age_s=301)
            assert (stale.status_code, stale.json()["detail"]["code"]) == (401, "reauth_required")
            assert stale.headers["www-authenticate"] == f"{challenge}, max_age=300"
            assert (await p.call("GET", "/me/wallet")).status_code == 404
            bad = await p.call("POST", "/me/wallet", body={"address": USDT[:-1] + "u"})
            assert (bad.status_code, bad.json()["detail"]["code"]) == (422, "invalid")
            assert p.notifications is not None
            assert await p.notifications.store.pending() == []  # nothing changed: no email
            added = await p.call("POST", "/me/wallet", body=body)
            assert added.status_code == 201, added.text
            assert added.json()["wal"] == "WAL00000001"
            assert (await p.call("GET", "/me/wallet")).json()["address"] == USDT

            # the security email: to the registered address, sent by the next round
            assert p.notifications is not None and p.mailer is not None
            (queued,) = await p.notifications.store.for_customer("CIF00000001")
            assert (queued["ntf"], queued["recipient"], queued["events"]) == (
                "NTF00000001",
                "u1@example.com",
                [],
            )
            await p.notifications.advance(clock())
            ((to, subject, text),) = p.mailer.sent
            assert (to, subject) == ("u1@example.com", "Your payout wallet was added")
            assert USDT in text and "If you did not make this change" in text
            (sent,) = await p.notifications.store.for_customer("CIF00000001")
            assert [e["kind"] for e in sent["events"]] == ["sent"]
            await p.notifications.advance(clock())  # sent once
            assert len(p.mailer.sent) == 1
            assert await p.notifications.store.pending() == []
            cooling = {c["name"]: c["status"] for c in (await view())["checks"]}
            assert (cooling["wallet"], cooling["waiting_period"]) == ("pass", "fail")
            early = await p.call("POST", f"/accounts/{acc}/payouts")
            assert (early.status_code, early.json()["detail"]["code"]) == (409, "not_eligible")

            # 15 days later: eligible; the request needs MFA too
            offset["ms"] = 15 * DAY
            ready = await view()
            assert ready["eligible"], ready["checks"]
            assert (ready["violations_cents"], ready["payable_cents"]) == (1_000, profit - 1_000)
            assert (ready["debit_cents"], ready["trader_amount_cents"]) == (
                profit,
                (profit - 1_000) * 4 // 5,
            )
            url = f"/accounts/{acc}/payouts"
            refused = await p.call("POST", url, mfa=False)
            assert (refused.status_code, refused.json()["detail"]["code"]) == (401, "mfa_required")
            stale = await p.call("POST", url, login_age_s=301)
            assert (stale.status_code, stale.json()["detail"]["code"]) == (401, "reauth_required")
            assert (await p.call("POST", url, "u2")).status_code == 404  # not registered/owner

            # cancelled by the trader before approval
            first = (await p.call("POST", url, login_age_s=299)).json()  # within 5 minutes
            assert (first["po"], first["status"], first["debit_cents"]) == (
                "PO00000001",
                "requested",
                profit,
            )
            assert ready["opens_at"] <= clock()  # on request: no pay day to wait for
            pending = {c["name"]: c["status"] for c in (await view())["checks"]}
            assert pending["no_pending_payout"] == "fail"
            again = await p.call("POST", url)
            assert (again.status_code, again.json()["detail"]["code"]) == (409, "not_eligible")
            trade = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 1}
            paused = await p.call("POST", f"/accounts/{acc}/orders", body=trade)
            assert (paused.status_code, paused.json()["detail"]["code"]) == (409, "payout_pending")
            cancelled = await p.call("POST", "/me/payouts/PO00000001/cancel")
            assert cancelled.json()["status"] == "cancelled"
            again = await p.call("POST", "/me/payouts/PO00000001/cancel")
            assert again.status_code == 409

            # denied by one employee
            second = (await p.call("POST", url)).json()["po"]
            assert second == "PO00000002"
            reason = {"reason": "checked"}
            denied = await staff(p, "POST", f"/staff/payouts/{second}/deny", "staff-1", reason)
            assert (denied.json()["status"], denied.json()["failure"]) == ("denied", "checked")

            # a senior passes, an admin authorises [decided 31 Oct 2026]; never
            # the trader's own, never twice by one employee
            po = (await p.call("POST", url)).json()["po"]
            approve = f"/staff/payouts/{po}/approve"
            own = await p.call("POST", approve, "u1", reason, ("trader", "staff"))
            assert (own.status_code, own.json()["detail"]["code"]) == (403, "own_payout")
            assert (await p.call("POST", approve, "u1", reason)).status_code == 403  # a trader
            early = await p.call("POST", approve, "admin-1", reason, ("admin",), mfa=False)
            assert (early.status_code, early.json()["detail"]["code"]) == (401, "mfa_required")

            async def todo(who: str, roles: tuple[str, ...] = STAFF) -> set[tuple[str, str]]:
                items = (await p.call("GET", "/staff/me/todo", who, None, roles)).json()
                return {(t["kind"], t["ref"]) for t in items if t["ref"] == po}

            assert await todo("staff-1") == {("payout_pass", po)}  # on the staff page
            one = (await staff(p, "POST", approve, "staff-1", reason)).json()
            assert await todo("staff-1") == set()  # passed: an admin's step now
            assert await todo("staff-2") == set()
            assert await todo("admin-1", ("admin",)) == {("payout_authorise", po)}
            assert (one["status"], one["approvals"]) == ("requested", 1)
            await p.payouts.advance(clock())  # nothing is taken on a pass
            assert (await p.payouts.store.payout(po)).status == "requested"  # type: ignore[union-attr]
            senior = await staff(
                p, "POST", approve, "staff-2", reason
            )  # T2: passes, never authorises
            assert (senior.status_code, senior.json()["detail"]["permission"]) == (
                403,
                "payout.authorise",
            )
            twice = await p.call("POST", approve, "staff-1", reason, ("admin",))
            assert (twice.status_code, twice.json()["detail"]["code"]) == (409, "not_allowed")
            two = (await p.call("POST", approve, "admin-1", reason, ("admin",))).json()
            assert (two["status"], two["approvals"]) == ("approved", 2)
            late = await p.call("POST", f"/me/payouts/{po}/cancel")  # approved: too late
            assert (late.status_code, late.json()["detail"]["code"]) == (409, "not_allowed")
            await p.call("POST", "/me", "u2", ADULT)
            other = await p.call("POST", f"/me/payouts/{po}/cancel", "u2")
            assert (other.status_code, other.json()["detail"]["code"]) == (404, "unknown")

            # once approved: checked again, debited, sent, paid in the next round
            pay_ts = clock() + 1
            await p.payouts.advance(pay_ts)
            done = await p.payouts.store.payout(po)
            assert done is not None and done.status == "paid", done
            assert (done.provider_ref, done.debited_at, done.paid_at) == (
                f"sandbox-{po}",
                pay_ts,
                pay_ts,
            )
            history = (await staff(p, "GET", f"/staff/payouts/{po}", "staff-3")).json()
            assert [e["kind"] for e in history["history"]] == [
                "requested",
                "approved",
                "approved",
                "rechecked",
                "debit_attempted",
                "debited",
                "sent",
                "paid",
            ]
            assert history["history"][0]["detail"]["rsv_id"] == "instant-funded-v1"
            steps = [(e["detail"]["by"], e["detail"]["as"]) for e in history["history"][1:3]]
            assert steps == [("staff-1", "pass"), ("admin-1", "authorise")]
            assert history["history"][0]["detail"]["approval"] == "pass_authorise"
            assert history["history"][0]["detail"]["eligibility"]["debit_cents"] == profit
            assert history["wallet"]["address"] == USDT
            assert history["history"][-1]["detail"] == {"tx": f"sandbox-tx-sandbox-{po}"}

            async def debited() -> Any:
                live = await p.live.account(acc)
                return live if live and live["balance_cents"] == 500_000 else None

            await eventually(debited)  # back at the baseline
            offset["ms"] = pay_ts - now_ms() + 1
            after = await view()
            assert (after["payable_cents"], after["period_start"], after["eligible"]) == (
                0,
                pay_ts,
                False,
            )
            assert after["violations_cents"] == 0  # taken out with the payout
            cycle = next(c for c in after["checks"] if c["name"] == "waiting_period")
            assert (cycle["status"], after["opens_at"]) == ("fail", pay_ts + 14 * DAY)
            assert cycle["detail"].endswith("(14 days after the last payout)")
            await p.payouts.advance(pay_ts + 1)  # nothing more to do
            assert (await p.payouts.store.payout(po)).status == "paid"  # type: ignore[union-attr]
            mine = (await p.call("GET", url)).json()
            assert [(x["po"], x["status"]) for x in mine] == [
                ("PO00000001", "cancelled"),
                ("PO00000002", "denied"),
                (po, "paid"),
            ]
            assert (await p.call("GET", url, "u2")).status_code == 404
            listed = (await staff(p, "GET", "/staff/payouts?status=paid", "staff-1")).json()
            assert [x["po"] for x in listed] == [po]
            gate_open = await p.call("POST", f"/accounts/{acc}/orders", body=trade)
            assert gate_open.status_code == 200  # paused only until the debit

            # the transaction and email logs (rule AC20)
            log = "/staff/reports/transactions"
            outs = (await staff(p, "GET", f"{log}?type=payout", "staff-1")).json()
            assert [(r["ref"], r["status"]) for r in outs["rows"]] == [
                (po, "paid"),
                ("PO00000002", "denied"),
                ("PO00000001", "cancelled"),
            ]
            assert outs["totals"] == {
                "count": 3,
                "in_cents": 0,
                "out_cents": done.trader_amount_cents,
            }
            every = (await staff(p, "GET", log, "staff-1")).json()
            bought = [r for r in every["rows"] if r["type"] == "purchase"]
            assert [(r["direction"], r["status"]) for r in bought] == [("in", "provisioned")]
            assert every["totals"]["in_cents"] == bought[0]["amount_cents"] > 0
            assert (await staff(p, "GET", f"{log}?remark=nothing-like-it", "staff-1")).json()[
                "totals"
            ]["count"] == 0
            csv_text = (
                await staff(p, "GET", f"{log}/export?type=payout&status=paid", "staff-1")
            ).text
            header, row = csv_text.strip().split("\r\n")
            assert header == "ref,type,cif,acc,amount,direction,status,remark,at,settled_at"
            cents = done.trader_amount_cents
            assert row.startswith(f"{po},payout,CIF00000001,{acc},{cents // 100}.{cents % 100:02d}")
            assert (await p.call("GET", f"{log}/export")).status_code == 403  # traders never
            emails = (
                await staff(p, "GET", "/staff/reports/emails?delivery=sent", "staff-1")
            ).json()
            assert [(e["ntf"], e["delivery"]) for e in emails][-1] == ("NTF00000001", "sent")
            assert "body" not in emails[0]

    asyncio.run(scenario())


def test_the_pay_day_check_fails_a_payout_that_changed(
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
            assert p.payouts is not None and p.cases is not None
            acc = await funded_instant(p)
            await eventually(lambda: _payable(p, acc))
            await p.call("POST", "/me/wallet", body={"address": USDT})
            offset["ms"] = 15 * DAY
            url = f"/accounts/{acc}/payouts"
            reason = {"reason": "ok"}

            async def approved() -> tuple[str, int]:
                created = await p.call("POST", url)
                assert created.status_code == 201, created.text
                po: str = created.json()["po"]
                for who, roles in (("staff-1", STAFF), ("admin-1", ("admin",))):
                    await p.call("POST", f"/staff/payouts/{po}/approve", who, reason, roles)
                record = await p.payouts.store.payout(po)  # type: ignore[union-attr]
                assert record is not None and record.status == "approved"
                return po, clock() + 1

            # a case opened after approval: held, nothing taken
            po, pay_ts = await approved()
            await p.cases.alert("CIF00000001", acc, "staff", "fraud", "low", {}, clock())
            await p.payouts.advance(pay_ts)
            failed = await p.payouts.store.payout(po)
            assert failed is not None and failed.status == "failed"
            assert failed.failure == "checks failing: cases"
            live = await p.live.account(acc)
            assert live is not None and live["balance_cents"] > 500_000  # not debited
            cas = "/staff/cases/CAS00000001"
            await staff(p, "POST", f"{cas}/assign", "staff-1")
            cleared = {"outcome": "cleared", "reason": "fine"}
            await staff(p, "POST", f"{cas}/decide", "staff-1", cleared)

            # a wallet changed after the request
            po, _ = await approved()
            await p.call("POST", "/me/wallet", body={"address": OTHER})
            assert p.notifications is not None and p.mailer is not None
            await p.notifications.advance(clock())
            (_, changed_subject, changed_text) = p.mailer.sent[-1]
            assert changed_subject == "Your payout wallet was changed"
            assert f"Previous wallet: {USDT}" in changed_text and OTHER in changed_text
            await p.payouts.advance(clock() + 1)
            changed = await p.payouts.store.payout(po)
            assert changed is not None and changed.status == "failed"
            assert changed.failure == "the payout wallet changed since the request"

            # the engine does not answer: the debit is published again, at most
            # once a minute, and taken once
            offset["ms"] = 15 * DAY + 3_600_000
            slow, slow_ts = await approved()

            async def attempts() -> int:
                history = await p.payouts.store.history(slow)  # type: ignore[union-attr]
                return sum(e.kind == "debit_attempted" for e in history)

            await p.stop_engine()
            await p.payouts.advance(slow_ts)
            waiting = await p.payouts.store.payout(slow)
            assert waiting is not None and waiting.status == "approved"
            assert await attempts() == 1
            denied = await staff(p, "POST", f"/staff/payouts/{slow}/deny", "staff-3", reason)
            assert denied.status_code == 409  # the money may be on its way out
            await p.payouts.advance(slow_ts + 1_000)
            assert await attempts() == 1  # too soon
            await p.payouts.advance(slow_ts + 61_000)
            assert await attempts() == 2
            await p.engine()
            await p.payouts.advance(slow_ts + 122_000)  # the engine refuses the repeats
            paid = await p.payouts.store.payout(slow)
            assert paid is not None and paid.status == "paid", paid
            assert await attempts() == 3

            async def at_baseline() -> Any:
                live = await p.live.account(acc)
                return live if live and live["balance_cents"] == 500_000 else None

            await eventually(at_baseline)

            # the record cannot be edited
            pool = await asyncpg.connect(database_url)
            try:
                s = p.config.customer_schema
                for sql in (
                    f"UPDATE {s}.payout_events SET kind = 'paid'",
                    f"DELETE FROM {s}.payout_events",
                    f"UPDATE {s}.wallets SET address = 'x'",
                    f"UPDATE {s}.payouts SET debit_cents = 1",
                    f"DELETE FROM {s}.payouts",
                    f"UPDATE {s}.notification_events SET kind = 'sent'",
                ):
                    with pytest.raises(asyncpg.RaiseError):
                        await pool.execute(sql)
                # emails are personal data, but never deleted directly: erasure
                # runs through the two owner workflow (rule AC27; D-15.4)
                with pytest.raises(asyncpg.RaiseError):
                    await pool.execute(f"DELETE FROM {s}.notifications WHERE cif = 'CIF00000001'")
                await pool.execute(f"UPDATE {s}.payouts SET status = 'paid' WHERE po = $1", po)
            finally:
                await pool.close()
            assert await p.payouts.store.rebuild_status() == [po]  # the cache follows the record
            restored = await p.payouts.store.payout(po)
            assert restored is not None and restored.status == "failed"

    asyncio.run(scenario())


async def _payable(p: Platform, acc: str) -> Any:
    found = (await p.call("GET", f"/accounts/{acc}/payout")).json()
    return found["payable_cents"] > 0


async def ready_to_pay(p: Platform, offset: dict[str, int], clock: Any) -> tuple[str, str, int]:
    """An Instant account in profit, a wallet, a payout approved twice;
    returns (ACC, PO, a time after the approval)."""
    acc = await funded_instant(p)
    await eventually(lambda: _payable(p, acc))
    await p.call("POST", "/me/wallet", body={"address": USDT})
    offset["ms"] = 15 * DAY
    created = await p.call("POST", f"/accounts/{acc}/payouts")
    assert created.status_code == 201, created.text
    po: str = created.json()["po"]
    for who, roles in (("staff-1", STAFF), ("admin-1", ("admin",))):
        await p.call("POST", f"/staff/payouts/{po}/approve", who, {"reason": "ok"}, roles)
    assert p.payouts is not None
    record = await p.payouts.store.payout(po)
    assert record is not None and record.status == "approved"
    return acc, po, clock() + 1


class SlowProvider:
    """Down for the first send; confirms only when told."""

    name = "slow"

    def __init__(self) -> None:
        self.down, self.paid = True, False
        self.sent: list[tuple[str, str, str, int]] = []

    async def send(self, order: PayoutOrder) -> str:
        if self.down:
            self.down = False
            raise ConnectionError("provider down")
        d = order.destination
        self.sent.append((order.payout, d["network"], d["address"], order.amount_cents))
        return f"slow-{order.payout}"

    async def status(self, ref: str) -> PayoutStatus:
        return PayoutStatus("paid", {"tx": "t1"}) if self.paid else PayoutStatus("pending", {})


def test_sending_is_retried_and_paid_only_when_confirmed(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    offset = {"ms": 0}

    def clock() -> int:
        return now_ms() + offset["ms"]

    provider = SlowProvider()

    async def scenario() -> None:
        async with platform(
            database_url,
            valkey_url,
            pg_schema,
            instant_without_consistency(),
            funded=True,
            clock=clock,
            payout_provider=provider,
        ) as p:
            assert p.payouts is not None
            store = p.payouts.store
            _, po, ts = await ready_to_pay(p, offset, clock)

            async def status() -> str:
                record = await store.payout(po)
                assert record is not None
                return record.status

            await p.payouts.advance(ts)
            assert await status() == "debited"  # taken; the provider was down
            assert [e.kind for e in await store.history(po)][-2:] == ["debited", "send_failed"]
            await p.payouts.advance(ts + 1_000)
            assert (await status(), provider.sent) == ("debited", [])  # not yet again
            await p.payouts.advance(ts + 61_000)
            record = await store.payout(po)
            assert record is not None and record.status == "sent"
            assert provider.sent == [(po, "TRC20", USDT, record.trader_amount_cents)]
            assert record.provider_ref == f"slow-{po}"
            await p.payouts.advance(ts + 62_000)
            assert await status() == "sent"  # not confirmed yet
            provider.paid = True
            await p.payouts.advance(ts + 63_000)
            assert await status() == "paid"
            assert (await store.history(po))[-1].detail == {"tx": "t1"}
            assert len(provider.sent) == 1

    asyncio.run(scenario())


def test_an_engine_refusal_fails_the_payout_and_trading_may_continue(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    offset = {"ms": 0}

    def clock() -> int:
        return now_ms() + offset["ms"]

    base = instant_without_consistency()
    allowed = replace(base, payouts=replace(base.payouts, trading_during_payout="allowed"))

    async def scenario() -> None:
        async with platform(
            database_url,
            valkey_url,
            pg_schema,
            allowed,
            funded=True,
            clock=clock,
            wait_s=2.0,
        ) as p:
            assert p.payouts is not None
            acc, po, ts = await ready_to_pay(p, offset, clock)
            limit = {"symbol": "EUR/USD", "side": "buy", "type": "limit", "volume": 1}
            placed = await p.call(
                "POST", f"/accounts/{acc}/orders", body={**limit, "level": 109_000}
            )
            # not paused: the engine decides (15 days on, the price is stale and
            # the symbol frozen, so it refuses; nothing changes on the account)
            assert placed.status_code == 200 and placed.json()["status"] == "rejected"

            # the account is voided while the debit is on its way
            await p.stop_engine()
            await p.writer.publish(VoidAccount(acc, "CAS00000001", clock()))
            await p.payouts.advance(ts)  # the live state still shows it active
            await p.engine()

            async def voided() -> Any:
                live = await p.live.account(acc)
                return live if live and live["status"] == "voided" else None

            await eventually(voided)
            await p.payouts.advance(ts + 61_000)
            record = await p.payouts.store.payout(po)
            assert record is not None and record.status == "failed", record
            assert record.failure == "refused by the engine: account_not_active"
            live = await p.live.account(acc)
            assert live is not None and live["balance_cents"] > 500_000  # nothing taken

    asyncio.run(scenario())
