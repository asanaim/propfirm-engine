"""Reconciliation (D-14.21) through real records: a statement's lines
are matched to orders, each gets one status, the paid order it leaves
out is listed as missing, and the same file twice is refused."""

import asyncio
from datetime import UTC, datetime

import pytest

from tests.integration.test_customers import ADULT, LITE_10K, platform

pytestmark = pytest.mark.integration

ADMIN = ("admin",)
DAY = 86_400_000


def test_a_statement_is_matched_to_the_orders(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            orders = {}
            for who in ("u1", "u2", "u3"):
                await p.call("POST", "/me", who, {**ADULT, "full_name": f"Trader {who}"})
                order = (await p.call("POST", "/orders", who, LITE_10K)).json()
                await p.call("POST", order["pay_url"], who)
                orders[who] = order
            pending = (await p.call("POST", "/orders", "u1", LITE_10K)).json()
            assert pending["status"] == "pending"
            price = int(orders["u1"]["price_cents"])
            now = p.clock()
            when = datetime.fromtimestamp(now / 1000, UTC).isoformat()
            dollars = f"{price // 100}.{price % 100:02d}"
            ref = {k: f"sandbox-{v['ord']}" for k, v in orders.items()}
            text = (
                "date,reference,direction,amount,currency,fee\n"
                f"{when},{ref['u1']},in,{dollars},USD,1.50\n"
                f"{when},sandbox-{pending['ord']},in,{dollars},USD,0\n"
                f"{when},{ref['u2']},in,1.00,USD,0\n"
                f"{when},unknown-7,in,10.00,USD,0\n"
                f"{when},{ref['u1']},in,{dollars},USD,0\n"
            )
            body = {
                "provider": "sandbox",
                "covers": "in",
                "period_from": now - DAY,
                "period_to": now + DAY,
                "filename": "november.csv",
                "csv": text,
            }
            refused = await p.call("POST", "/staff/reconciliations", "staff-1", body, ("staff",))
            assert refused.status_code == 403
            made = await p.call("POST", "/staff/reconciliations", "admin-1", body, ADMIN)
            assert made.status_code == 201, made.text
            run = made.json()
            assert run["run"] == "REC00000001"
            assert run["counts"] == {
                "matched": 1,
                "amount_differs": 1,
                "not_settled": 1,
                "unmatched": 1,
                "repeated": 1,
            }
            assert (run["lines"], run["in_cents"], run["fee_cents"]) == (
                5,
                3 * price + 100 + 1000,
                150,
            )

            detail = (
                await p.call("GET", "/staff/reconciliations/REC00000001", "admin-1", None, ADMIN)
            ).json()
            statuses = [(i["line"], i["status"], i["record"]) for i in detail["items"]]
            assert statuses == [
                (1, "matched", orders["u1"]["ord"]),
                (2, "not_settled", pending["ord"]),
                (3, "amount_differs", orders["u2"]["ord"]),
                (4, "unmatched", None),
                (5, "repeated", orders["u1"]["ord"]),
            ]
            assert detail["items"][2]["expected_cents"] == price
            assert [(m["kind"], m["record"]) for m in detail["missing"]] == [
                ("order", orders["u3"]["ord"])
            ]

            again = await p.call("POST", "/staff/reconciliations", "admin-1", body, ADMIN)
            assert (again.status_code, again.json()["detail"]["code"]) == (409, "duplicate")
            bad = await p.call(
                "POST",
                "/staff/reconciliations",
                "admin-1",
                {**body, "csv": text.replace("1.00,USD", "1.001,USD")},
                ADMIN,
            )
            assert (bad.status_code, bad.json()["detail"]["code"]) == (422, "bad_lines")
            assert bad.json()["detail"]["problems"][0].startswith("line 3: not an amount")
            listed = (await p.call("GET", "/staff/reconciliations", "admin-1", None, ADMIN)).json()
            assert [r["run"] for r in listed] == ["REC00000001"]  # nothing stored for the bad one
            unknown = await p.call(
                "GET", "/staff/reconciliations/REC00000009", "admin-1", None, ADMIN
            )
            assert unknown.status_code == 404

    asyncio.run(scenario())
