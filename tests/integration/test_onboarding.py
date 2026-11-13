"""Funded onboarding against PostgreSQL, Valkey and a running engine, through
the API: identity check (sandbox), checks, contract, signature, the funded
account; Instant orders wait for it; reviews, declines, screening hits;
the Didit webhook; the record's protection."""

import asyncio
import json
from dataclasses import replace
from datetime import date
from typing import Any, cast

import asyncpg
import pytest

from propfirm.customers.funding import Onboarding
from propfirm.customers.kyc import KycDecision, KycSession, SandboxKyc, didit_signature
from propfirm.customers.providers import SandboxScreener
from propfirm.journal.inputs import VoidAccount
from propfirm.policy import Policy, load_policy
from propfirm.service.feeder import now_ms
from tests.integration.test_customers import ADULT, LITE_10K, Platform, eventually, platform
from tests.integration.test_progression import STAFF, Market, quick_lite

pytestmark = pytest.mark.integration

INSTANT_5K = {"product_id": "instant", "size_cents": 500_000}


async def funded_review_approved(p: Platform) -> None:
    """Lite 10K bought, S1 and S2 passed, the funded review approved."""
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
    decision = {"reason": "clean record"}
    await p.call("POST", "/staff/progressions/PRG00000002/approve", "staff-2", decision, STAFF)
    approved = await p.call(
        "POST", "/staff/progressions/PRG00000002/approve", "staff-1", decision, STAFF
    )
    assert approved.status_code == 200, approved.text


async def verify(
    p: Platform, onb: str, user: str = "u1", result: str = "approved", **identity: str
) -> dict[str, Any]:
    started = await p.call("POST", f"/me/onboardings/{onb}/verification", user)
    assert started.status_code == 200, started.text
    url = started.json()["verification_url"]
    assert url.startswith("/dev/kyc/sandbox-")
    done = await p.call("POST", url, user, {"result": result, **identity})
    assert done.status_code == 200, done.text
    mine: dict[str, Any] = (await p.call("GET", f"/me/onboardings/{onb}", user)).json()
    return mine


def test_a_funded_review_ends_in_a_signed_funded_account(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, quick_lite(), funded=True) as p:
            assert p.onboarding is not None
            await funded_review_approved(p)
            await p.onboarding.advance(now_ms())
            (onboarding,) = (await p.call("GET", "/me")).json()["onboardings"]
            assert (onboarding["onb"], onboarding["status"], onboarding["prg"]) == (
                "ONB00000001",
                "verification",
                "PRG00000002",
            )
            assert onboarding["rsv_id"] == "lite-funded-v3"
            await p.onboarding.advance(now_ms())  # nothing new
            assert len((await p.call("GET", "/me")).json()["onboardings"]) == 1

            other = await p.call("POST", "/me", "u2", ADULT)
            assert other.status_code == 201
            assert (
                await p.call("POST", "/me/onboardings/ONB00000001/verification", "u2")
            ).status_code == 404  # someone else's
            first = await p.call("POST", "/me/onboardings/ONB00000001/verification")
            second = await p.call("POST", "/me/onboardings/ONB00000001/verification")
            assert first.json()["verification_url"] == second.json()["verification_url"]
            assert first.json()["kyc_attempts"] == 1  # the open session is reused

            done = await p.call(
                "POST", first.json()["verification_url"], body={"result": "approved"}
            )
            assert done.status_code == 200, done.text
            mine = (await p.call("GET", "/me/onboardings/ONB00000001")).json()
            assert (mine["status"], mine["acc"], mine["contract"]) == (
                "signature",
                "ACC00000003",
                "CTR00000001",
            )
            contract = (await p.call("GET", "/me/onboardings/ONB00000001/contract")).json()
            assert contract["draft"] is True and contract["template"] == "funded-agreement-v2"
            assert "Account ACC00000003" in contract["body"] and "Ada Trader" in contract["body"]

            url = "/me/onboardings/ONB00000001/sign"
            good = {"full_name": "ada  TRADER", "sha256": contract["sha256"]}
            wrong_text = await p.call("POST", url, body={**good, "sha256": "0" * 64})
            assert wrong_text.status_code == 422
            assert wrong_text.json()["detail"]["code"] == "other_text"
            wrong_name = await p.call("POST", url, body={**good, "full_name": "Someone Else"})
            assert wrong_name.json()["detail"]["code"] == "name"
            assert (await p.call("POST", url, "u2", good)).status_code == 404
            signed = await p.call("POST", url, body=good)
            assert signed.status_code == 200, signed.text
            assert (signed.json()["status"], signed.json()["acc"]) == (
                "provisioned",
                "ACC00000003",
            )
            assert (await p.call("POST", url, body=good)).status_code == 409  # signed once

            me = (await p.call("GET", "/me")).json()
            funded = me["accounts"][-1]
            assert (funded["acc"], funded["stage"], funded["rsv_id"]) == (
                "ACC00000003",
                "funded",
                "lite-funded-v3",
            )
            assert (funded["progression"], funded["onboarding"]) == (
                "PRG00000002",
                "ONB00000001",
            )
            live = await eventually(lambda: p.live.account("ACC00000003"))
            assert live["balance_cents"] == 1_000_000

            store = p.onboarding.store
            kinds = [e.kind for e in await store.history("ONB00000001")]
            assert kinds == [
                "created",
                "kyc_requested",
                "kyc_approved",
                "screened",
                "cleared",
                "account_assigned",
                "contract_issued",
                "signed",
                "provision_attempted",
                "provisioned",
            ]
            trail = (await store.history("ONB00000001"))[7].detail
            assert trail["sha256"] == contract["sha256"] and trail["kyc"] == "KYC00000001"
            assert trail["subject"] == "u1" and trail["typed_name"] == "ada  TRADER"
            assert "ip" in trail and "user_agent" in trail

            s = p.config.customer_schema
            conn = await asyncpg.connect(database_url)
            try:
                for sql in (
                    f"UPDATE {s}.onboarding_events SET ts = 0",
                    f"DELETE FROM {s}.onboarding_events",
                    f"UPDATE {s}.onboardings SET rsv_id = 'lite-funded-v9'",
                    f"DELETE FROM {s}.onboardings",
                    f"UPDATE {s}.contracts SET body = 'other'",
                    f"DELETE FROM {s}.contracts",
                ):
                    with pytest.raises(asyncpg.RaiseError):
                        await conn.execute(sql)
                await conn.execute(f"UPDATE {s}.onboardings SET status = 'review', acc = NULL")
            finally:
                await conn.close()
            assert await store.rebuild_status() == ["ONB00000001"]
            assert (await store.onboarding("ONB00000001")).status == "provisioned"  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_an_instant_account_opens_only_after_the_agreement_is_signed(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            assert p.onboarding is not None
            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body=INSTANT_5K)).json()
            paid = (await p.call("POST", order["pay_url"])).json()
            assert (paid["status"], paid["acc"]) == ("paid", None)  # not open yet
            await p.call("POST", order["pay_url"])  # a repeated notice: still one onboarding
            (onboarding,) = (await p.call("GET", "/me")).json()["onboardings"]
            assert (onboarding["source"], onboarding["ord"], onboarding["rsv_id"]) == (
                "order",
                order["ord"],
                "instant-funded-v3",
            )
            mine = await verify(p, onboarding["onb"])
            assert (mine["status"], mine["acc"]) == ("signature", "ACC00000001")
            assert (await p.call("GET", f"/orders/{order['ord']}")).json()["status"] == "paid"
            sha = (await p.call("GET", f"/me/onboardings/{mine['onb']}/contract")).json()["sha256"]
            signed = await p.call(
                "POST",
                f"/me/onboardings/{mine['onb']}/sign",
                body={"full_name": "Ada Trader", "sha256": sha},
            )
            assert signed.json()["status"] == "provisioned", signed.text
            done = (await p.call("GET", f"/orders/{order['ord']}")).json()
            assert (done["status"], done["acc"]) == ("provisioned", "ACC00000001")
            accounts = (await p.call("GET", "/me")).json()["accounts"]
            assert [(a["acc"], a["stage"], a["onboarding"]) for a in accounts] == [
                ("ACC00000001", "funded", None)  # the order's account
            ]
            await eventually(lambda: p.live.account("ACC00000001"))

            # one Instant account at a time (review D4): a second is refused
            # while the first is open, and sold once it has ended
            refused = await p.call("POST", "/orders", body=INSTANT_5K)
            assert (refused.status_code, refused.json()["detail"]["code"]) == (422, "one_instant")
            await p.writer.publish(VoidAccount("ACC00000001", "CAS00000001", now_ms()))

            async def ended() -> Any:
                view = await p.live.account("ACC00000001")
                return view if view and view["status"] == "voided" else None

            await eventually(ended)
            again = (await p.call("POST", "/orders", body=INSTANT_5K)).json()
            await p.call("POST", again["pay_url"])
            second = (await p.call("GET", "/me")).json()["onboardings"][1]
            reused = await p.call("POST", f"/me/onboardings/{second['onb']}/verification")
            assert reused.json()["status"] == "signature"  # verified before: no new check
            assert reused.json()["kyc"] == "KYC00000001" and reused.json()["kyc_attempts"] == 0

    asyncio.run(scenario())


def several_instant() -> Policy:
    """The shipped policy, allowing several Instant accounts per trader (the
    test needs three onboardings at once)."""
    policy = load_policy()
    return replace(
        policy, catalogue=replace(policy.catalogue, one_instant_funding_per_trader=False)
    )


def test_a_different_identity_goes_to_an_employee(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(
            database_url, valkey_url, pg_schema, several_instant(), funded=True
        ) as p:
            await p.call("POST", "/me", body=ADULT)
            for _ in range(3):
                order = (await p.call("POST", "/orders", body=INSTANT_5K)).json()
                await p.call("POST", order["pay_url"])
            mine = await verify(p, "ONB00000001", full_name="Ada Q. Trader")
            assert mine["status"] == "review"
            url = "/staff/onboardings/ONB00000001"
            waiting = (await p.call("GET", "/staff/onboardings", "staff-1", roles=STAFF)).json()
            assert [w["onb"] for w in waiting] == ["ONB00000001"]
            detail = (await p.call("GET", url, "staff-1", roles=STAFF)).json()
            assert detail["verified"]["full_name"] == "Ada Q. Trader"
            review = [h for h in detail["history"] if h["kind"] == "review_required"]
            assert review[0]["detail"]["differences"] == ["full_name"]
            reason = {"reason": "middle initial on the passport"}
            assert (await p.call("POST", f"{url}/approve", body=reason)).status_code == 403
            own = await p.call("POST", f"{url}/approve", "u1", reason, ("trader", "staff"))
            assert own.json()["detail"]["code"] == "own_onboarding"

            # outside the sandbox, a contract without firm name and governing law waits
            assert p.onboarding is not None
            production = Onboarding(
                p.onboarding.store,
                p.store,
                p.settings,
                SandboxKyc(),
                SandboxScreener(),
                cast(Any, None),  # no account opens in this step
                None,
                sandbox=False,
            )
            await production.decide("ONB00000001", True, "staff-2", None, "ok", now_ms())
            held = await production.decide("ONB00000001", True, "staff-1", None, "ok", now_ms())
            assert (held.status, held.contract) == ("contract", None)
            await p.onboarding.advance(now_ms())  # the sandbox issues it, as a draft
            issued = (await p.call("GET", "/me/onboardings/ONB00000001")).json()
            assert (issued["status"], issued["contract"]) == ("signature", "CTR00000001")
            late = await p.call("POST", f"{url}/deny", "staff-1", reason, STAFF)
            assert late.status_code == 409

            # the other orders reuse the check: one review approved, one denied
            for onb in ("ONB00000002", "ONB00000003"):
                reused = await p.call("POST", f"/me/onboardings/{onb}/verification")
                assert reused.json()["status"] == "review"
            await p.call("POST", "/staff/onboardings/ONB00000002/approve", "staff-2", reason, STAFF)
            approved = await p.call(
                "POST", "/staff/onboardings/ONB00000002/approve", "staff-1", reason, STAFF
            )
            assert approved.json()["status"] == "signature", approved.text
            await p.call("POST", "/staff/onboardings/ONB00000003/deny", "staff-1", reason, STAFF)
            denied = await p.call(
                "POST", "/staff/onboardings/ONB00000003/deny", "staff-2", reason, STAFF
            )
            assert (denied.json()["status"], denied.json()["failure"]) == ("rejected", "denied")

    asyncio.run(scenario())


def test_declines_screening_and_age(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, funded=True) as p:
            for user in ("u1", "u2", "u3"):  # one customer and one Instant order each
                await p.call("POST", "/me", user, ADULT)
                order = (await p.call("POST", "/orders", user, INSTANT_5K)).json()
                await p.call("POST", order["pay_url"], user)

            for attempt in range(1, 4):
                mine = await verify(p, "ONB00000001", "u1", "declined")
                assert (mine["status"], mine["kyc_attempts"]) == ("verification_declined", attempt)
            last = await p.call("POST", "/me/onboardings/ONB00000001/verification")
            assert last.status_code == 409 and last.json()["detail"]["code"] == "attempts"
            gone = (await p.call("GET", "/me/onboardings/ONB00000001")).json()
            assert (gone["status"], gone["failure"]) == ("rejected", "verification_attempts")

            young = await verify(p, "ONB00000002", "u2", date_of_birth="2010-01-01")
            assert (young["status"], young["failure"]) == ("rejected", "age:16<18")

            hit = await verify(p, "ONB00000003", "u3", full_name="Ada SANCTIONED Trader")
            assert (hit["status"], hit["failure"]) == ("rejected", "screening_hit")
            me = (await p.call("GET", "/me", "u3")).json()
            assert me["status"] == "blocked" and me["orders"][0]["status"] == "paid"
            blocked = await p.call("POST", "/me/onboardings/ONB00000003/verification", "u3")
            assert blocked.status_code == 403

    asyncio.run(scenario())


class FakeDidit:
    """Didit as the webhook sees it: approves once told to."""

    name = "didit"

    def __init__(self) -> None:
        self.approved: set[str] = set()

    async def start(self, reference: str, callback_url: str | None) -> KycSession:
        return KycSession("didit", f"didit-{reference}", "https://verify.test/x")

    async def decision(self, session_id: str) -> KycDecision:
        if session_id not in self.approved:
            return KycDecision(None)
        return KycDecision("approved", "Ada Trader", date(1990, 5, 17), "DE")


def test_the_didit_webhook_is_checked_then_read_from_didit(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    didit = FakeDidit()

    async def scenario() -> None:
        async with platform(
            database_url,
            valkey_url,
            pg_schema,
            funded=True,
            kyc=didit,
            didit_webhook_secret="whsec",
        ) as p:
            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body=INSTANT_5K)).json()
            await p.call("POST", order["pay_url"])
            started = await p.call("POST", "/me/onboardings/ONB00000001/verification")
            assert started.json()["verification_url"] == "https://verify.test/x"

            body = json.dumps({"session_id": "didit-ONB00000001", "status": "Approved"}).encode()

            async def notify(signature: str, ts: int) -> int:
                headers = {"x-signature-v2": signature, "x-timestamp": str(ts)}
                answer = await p.api.post("/webhooks/didit", content=body, headers=headers)
                return answer.status_code

            now = now_ms() // 1000
            assert await notify("0" * 64, now) == 401
            assert await notify(didit_signature(body, "whsec"), now - 3_600) == 401
            good = didit_signature(body, "whsec")
            assert await notify(good, now) == 200  # valid, but Didit says not final yet
            mine = (await p.call("GET", "/me/onboardings/ONB00000001")).json()
            assert mine["status"] == "verification"  # the body's "Approved" is not trusted
            didit.approved.add("didit-ONB00000001")
            assert await notify(good, now) == 200
            mine = (await p.call("GET", "/me/onboardings/ONB00000001")).json()
            assert mine["status"] == "signature"
            sandbox = await p.call(
                "POST", "/dev/kyc/didit-ONB00000001/complete", body={"result": "approved"}
            )
            assert sandbox.status_code == 404  # the sandbox endpoint is only for the sandbox

    asyncio.run(scenario())
