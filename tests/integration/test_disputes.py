"""Refunds and chargebacks (D-14.22) through real records: a part refund
leaves the account, a full one ends it; a chargeback ends the account,
opens a case, takes back the commission and holds purchases until it is
won; every step needs a second person; the books post each once."""

import asyncio
from typing import Any

import pytest

from tests.integration.test_customers import ADULT, LITE_10K, eventually, platform

pytestmark = pytest.mark.integration

ADMIN = ("admin",)


def test_refunds_and_chargebacks(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            assert p.books is not None and p.referrals is not None

            async def staff(method: str, path: str, who: str, body: Any = None) -> Any:
                return await p.call(method, path, who, body, ADMIN)

            async def status(acc: str) -> Any:
                view = await p.live.account(acc)
                return None if view is None else view.get("status")

            # u1: a part refund, then the rest
            await p.call("POST", "/me", "u1", ADULT)
            first = (await p.call("POST", "/orders", "u1", LITE_10K)).json()
            await p.call("POST", first["pay_url"], "u1")
            acc = "ACC00000001"
            await eventually(lambda: p.live.account(acc))
            price = int(first["price_cents"])
            url = f"/staff/orders/{first['ord']}/refunds"
            part = await staff(
                "POST", url, "admin-1", {"amount_cents": 1_000, "reason": "goodwill"}
            )
            assert part.status_code == 201, part.text
            rfd = part.json()["rfd"]
            decide = f"/staff/refunds/{rfd}/decision"
            same = await staff("POST", decide, "admin-1", {"approve": True, "reason": "ok"})
            assert (same.status_code, same.json()["detail"]["code"]) == (403, "same_person")
            done = (
                await staff("POST", decide, "admin-2", {"approve": True, "reason": "ok"})
            ).json()
            assert (done["status"], done["ended_acc"]) == ("approved", None)
            assert await status(acc) == "active"  # a part refund leaves the account
            over = await staff("POST", url, "admin-1", {"amount_cents": price, "reason": "x"})
            assert (over.status_code, over.json()["detail"]["code"]) == (409, "too_much")
            rest = (
                await staff(
                    "POST", url, "admin-1", {"amount_cents": price - 1_000, "reason": "all"}
                )
            ).json()
            ended = (
                await staff(
                    "POST",
                    f"/staff/refunds/{rest['rfd']}/decision",
                    "admin-2",
                    {"approve": True, "reason": "ok"},
                )
            ).json()
            assert ended["ended_acc"] == acc
            assert await status(acc) == "expired"

            # u2, referred by u1: a chargeback
            code = (await p.call("GET", "/me/referrals", "u1")).json()["code"]
            await p.call("POST", "/me", "u2", {**ADULT, "full_name": "Second", "ref": code})
            order = (await p.call("POST", "/orders", "u2", LITE_10K)).json()
            await p.call("POST", order["pay_url"], "u2")
            acc2 = "ACC00000002"
            await eventually(lambda: p.live.account(acc2))
            assert await p.referrals.accrue(p.clock(), every_ms=0) == 1
            body = {
                "amount_cents": price,
                "fee_cents": 1_500,
                "provider_case": "dispute-42",
                "reason": "cardholder dispute",
                "received_at": p.clock(),
            }
            refused = await p.call(
                "POST", f"/staff/orders/{order['ord']}/chargebacks", "staff-1", body, ("staff",)
            )
            assert refused.status_code == 403
            made = await staff("POST", f"/staff/orders/{order['ord']}/chargebacks", "admin-1", body)
            assert made.status_code == 201, made.text
            cbk = made.json()
            assert (cbk["status"], cbk["ended_acc"]) == ("open", acc2)
            assert cbk["cas"] is not None and cbk["cas"].startswith("CAS")
            assert await status(acc2) == "expired"
            kinds = [e["kind"] for e in await p.referrals.entries()]
            assert sorted(kinds) == ["accrued", "clawback"]
            held = await p.call("POST", "/orders", "u2", LITE_10K)
            assert (held.status_code, held.json()["detail"]["code"]) == (403, "purchases_held")
            disputed = await staff(
                "POST",
                f"/staff/orders/{order['ord']}/refunds",
                "admin-2",
                {"amount_cents": 1, "reason": "x"},
            )
            assert (disputed.status_code, disputed.json()["detail"]["code"]) == (409, "disputed")
            outcome = f"/staff/chargebacks/{cbk['cbk']}/decision"
            mine = await staff("POST", outcome, "admin-1", {"outcome": "won", "reason": "evidence"})
            assert mine.status_code == 403
            won = (
                await staff("POST", outcome, "admin-2", {"outcome": "won", "reason": "evidence"})
            ).json()
            assert won["status"] == "won"
            again = await p.call("POST", "/orders", "u2", LITE_10K)
            assert again.status_code == 201, again.text

            # the books: each record once; the trial balance sums to zero
            await p.books.sync(p.clock(), every_ms=0)
            assert await p.books.sync(p.clock(), every_ms=0) == 0
            entries = (await staff("GET", "/staff/books/entries", "admin-1")).json()
            counted = sorted(e["kind"] for e in entries if e["kind"] != "reserve_sweep")
            assert counted == [
                "chargeback",
                "chargeback_won",
                "commission_accrued",
                "commission_clawback",
                "order_paid",
                "order_paid",
                "order_refunded",
                "order_refunded",
            ]
            tb = (await staff("GET", "/staff/books/trial-balance", "admin-1")).json()
            lines = {x["account"]: x["balance_cents"] for x in tb["lines"]}
            assert tb["total_cents"] == 0
            assert lines["income:refunds:lite"] == price
            assert lines["income:chargebacks:lite"] == 0  # won back
            assert lines["expense:processor_fees"] == 1_500
            listed = (await staff("GET", "/staff/refunds", "admin-1")).json()
            assert [r["status"] for r in listed] == ["approved", "approved"]
            assert [
                c["status"] for c in (await staff("GET", "/staff/chargebacks", "admin-1")).json()
            ] == ["won"]

    asyncio.run(scenario())
