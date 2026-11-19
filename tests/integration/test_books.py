"""Operator ledger through real records (D-14.16): paid orders, a free
trial, a referral commission and an approved expense post once; the trial
balance sums to zero."""

import asyncio
from fractions import Fraction

import pytest

from tests.integration.test_customers import ADULT, LITE_10K, platform

pytestmark = pytest.mark.integration

ADMIN = ("admin",)


def test_the_books_follow_the_records(database_url: str, valkey_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            assert p.books is not None and p.referrals is not None
            await p.call("POST", "/me", "ref", ADULT)
            code = (await p.call("GET", "/me/referrals", "ref")).json()["code"]
            await p.call("POST", "/me", "u1", {**ADULT, "full_name": "New", "ref": code})
            await p.call("POST", "/me/trial", "u1")  # posts nothing
            await p.store.add_code("WELCOME10", Fraction(1, 10), 0)
            body = {**LITE_10K, "size_cents": 2_500_000, "code": "WELCOME10", "addons": ["swing"]}
            order = (await p.call("POST", "/orders", "u1", body)).json()
            await p.call("POST", order["pay_url"], "u1")
            unpaid = (await p.call("POST", "/orders", "u1", LITE_10K)).json()  # never paid
            assert unpaid["status"] == "pending"
            await p.referrals.accrue(p.clock(), every_ms=0)

            # an expense: proposed by one, approved by another
            expense = {
                "category": "hosting",
                "amount_cents": 2_999,
                "memo": "server, November",
                "paid": True,
                "incurred_at": p.clock(),
            }
            proposed = (
                await p.call("POST", "/staff/books/expenses", "admin-1", expense, ADMIN)
            ).json()
            url = f"/staff/books/expenses/{proposed['exp']}/decision"
            same = await p.call("POST", url, "admin-1", {"approve": True, "reason": "ok"}, ADMIN)
            assert same.status_code == 403
            approved = await p.call(
                "POST", url, "admin-2", {"approve": True, "reason": "ok"}, ADMIN
            )
            assert approved.json()["status"] == "approved"
            again = await p.call("POST", url, "admin-2", {"approve": True, "reason": "ok"}, ADMIN)
            assert again.status_code == 409

            assert await p.books.sync(p.clock(), every_ms=0) == 2  # the order, the commission
            assert await p.books.sync(p.clock(), every_ms=0) == 0  # once

            tb = (await p.call("GET", "/staff/books/trial-balance", "admin-1", None, ADMIN)).json()
            assert tb["total_cents"] == 0
            lines = {x["account"]: x["balance_cents"] for x in tb["lines"]}
            # Lite 25K $109 less 10 % plus Swing 15 %: $114.45 in; expense $29.99 out
            assert lines == {
                "asset:cash": 11_445 - 2_999,
                "income:fees:lite": -(10_900 + 1_635),
                "income:discounts:lite": 1_090,
                "expense:affiliate_commissions": 1_144,  # 10 % of $114.45, rounded down
                "liability:commissions_payable": -1_144,
                "expense:operating:hosting": 2_999,
            }
            entries = (await p.call("GET", "/staff/books/entries", "admin-1", None, ADMIN)).json()
            assert sorted(e["kind"] for e in entries) == [
                "commission_accrued",
                "operating_expense",
                "order_paid",
            ]
            trader = await p.call("GET", "/staff/books/trial-balance", "u1")
            assert trader.status_code == 403

    asyncio.run(scenario())
