"""Referral ledger (D-14.15): a code entered at registration earns 10 % of
the referred customer's first paid account, once, after a 30-day holdback;
a clawback is a new entry; refused codes do not stop registration."""

import asyncio

import pytest

from tests.integration.test_customers import ADULT, LITE_10K, platform

pytestmark = pytest.mark.integration

DAY = 86_400_000


def test_a_referral_earns_once_on_the_first_paid_account(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            assert p.referrals is not None
            await p.call("POST", "/me", "ref", ADULT)
            mine = (await p.call("GET", "/me/referrals", "ref")).json()
            code = mine["code"]
            assert (mine["on"], mine["commission_percent"], mine["holdback_days"]) == (
                True,
                "10",
                30,
            )
            assert (
                len(code) == 8
                and (await p.call("GET", "/me/referrals", "ref")).json()["code"] == code
            )

            # refused codes: registration still succeeds
            bad = await p.call("POST", "/me", "u2", {**ADULT, "ref": "ZZZZZZZZ"})
            assert bad.status_code == 201 and bad.json()["referral"] == {
                "accepted": False,
                "code": "unknown_code",
            }
            india = await p.call(
                "POST", "/me", "u3", {**ADULT, "residence_country": "IN", "ref": code}
            )
            if india.status_code == 201:  # registration may be open to India by setting
                assert india.json()["referral"]["code"] == "country"

            good = await p.call(
                "POST", "/me", "new", {**ADULT, "full_name": "New Trader", "ref": code}
            )
            assert good.json()["referral"] == {"accepted": True, "code": None}

            # a free trial earns nothing; the first paid account does, once
            await p.call("POST", "/me/trial", "new")
            assert await p.referrals.accrue(p.clock(), every_ms=0) == 0
            first = (await p.call("POST", "/orders", "new", LITE_10K)).json()
            await p.call("POST", first["pay_url"], "new")
            second = (await p.call("POST", "/orders", "new", LITE_10K)).json()
            await p.call("POST", second["pay_url"], "new")
            assert await p.referrals.accrue(p.clock()) == 0  # within the minute: not run
            assert await p.referrals.accrue(p.clock(), every_ms=0) == 1
            assert await p.referrals.accrue(p.clock(), every_ms=0) == 0  # once; repeats earn none

            mine = (await p.call("GET", "/me/referrals", "ref")).json()
            assert (mine["referred"], mine["accrued_cents"], mine["available_cents"]) == (1, 550, 0)
            (entry,) = mine["entries"]
            assert (entry["ord"], entry["kind"], entry["amount_cents"]) == (
                first["ord"],
                "accrued",
                550,
            )
            referrer = await p.store.cif_by_subject("ref")
            assert referrer is not None
            later = await p.referrals.balance(referrer.cif, entry["available_at"])
            assert later.available_cents == 550  # after the holdback
            assert 29 * DAY < entry["available_at"] - entry["ts"] <= 30 * DAY

            # clawback: a new negative entry, once
            url = f"/staff/referrals/{first['ord']}/clawback"
            back = await p.call("POST", url, "staff-1", {"reason": "refunded"}, ("admin",))
            assert back.status_code == 200, back.text
            assert back.json()["amount_cents"] == -550
            again = await p.call("POST", url, "staff-1", {"reason": "again"}, ("admin",))
            assert again.status_code == 409
            assert (await p.call("GET", "/me/referrals", "ref")).json()["accrued_cents"] == 0
            ledger = (await p.call("GET", "/staff/referrals", "staff-1", None, ("admin",))).json()
            assert [e["kind"] for e in ledger] == ["clawback", "accrued"]

    asyncio.run(scenario())
