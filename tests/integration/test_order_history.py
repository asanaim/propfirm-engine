"""Order and customer histories in PostgreSQL: the record, the cache rebuilt
from it, the append-only triggers, and the backfill of older orders."""

import asyncio
import json
import re

import asyncpg
import pytest

from propfirm.customers.store import _LOCK_CLASS, _MIGRATIONS, CustomerStore
from propfirm.pgschema import migrate
from tests.integration.test_customers import ADULT, LITE_10K, platform

pytestmark = pytest.mark.integration


def test_every_step_of_an_order_is_on_record(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body=LITE_10K)).json()
            assert (await p.call("POST", order["pay_url"])).json()["status"] == "provisioned"
            await p.call("POST", order["pay_url"])  # a repeated payment notice
            events = await p.store.order_history(order["ord"])
            assert [e.kind for e in events] == [
                "created",
                "invoiced",
                "paid",
                "account_assigned",
                "provision_attempted",
                "provisioned",
                "payment_repeated",
            ]
            created, invoiced, *_, attempt, provisioned, _ = (e.detail for e in events)
            assert (created["price_cents"], created["rsv_id"]) == (4_400, "lite-s1-v1")
            stored = await p.store.order(order["ord"])
            assert stored is not None and invoiced["provider_ref"] == stored.provider_ref
            assert re.fullmatch(r"[0-9]+-[0-9]+", attempt["entry_id"])
            assert provisioned["acc"] == "ACC00000001" and provisioned["journal_seq"] > 0
            assert await p.store.rebuild_status() == []  # the cache is what the record says

    asyncio.run(scenario())


def test_records_refuse_edits_and_a_hand_edited_cache_is_rebuilt(
    database_url: str, valkey_url: str, pg_schema: str
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema) as p:
            await p.call("POST", "/me", body=ADULT)
            order = (await p.call("POST", "/orders", body=LITE_10K)).json()
            await p.call("POST", order["pay_url"])
            s = p.config.customer_schema
            conn = await asyncpg.connect(database_url)
            try:
                for sql in (
                    f"UPDATE {s}.order_events SET ts = 0",
                    f"DELETE FROM {s}.order_events",
                    f"UPDATE {s}.accounts SET cif = cif",
                    f"DELETE FROM {s}.accounts",
                    f"DELETE FROM {s}.orders",
                    f"UPDATE {s}.orders SET price_cents = 1",
                    f"UPDATE {s}.orders SET rsv_id = 'lite-s1-v9'",
                ):
                    with pytest.raises(asyncpg.RaiseError):
                        await conn.execute(sql)
                # the cache columns can be written (the store does), but the
                # record decides: a hand edit is undone by the rebuild
                await conn.execute(f"UPDATE {s}.orders SET status = 'pending', acc = NULL")
                await conn.execute(f"UPDATE {s}.cif SET status = 'blocked'")
            finally:
                await conn.close()
            assert await p.store.rebuild_status() == [order["ord"], "CIF00000001"]
            fixed = (await p.call("GET", f"/orders/{order['ord']}")).json()
            assert (fixed["status"], fixed["acc"]) == ("provisioned", "ACC00000001")
            assert (await p.call("GET", "/me")).json()["status"] == "registered"
            assert await p.store.rebuild_status() == []

    asyncio.run(scenario())


def test_orders_made_before_the_history_are_backfilled(database_url: str, pg_schema: str) -> None:
    schema = f"{pg_schema}_backfill"

    async def scenario() -> None:
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
        try:
            async with pool.acquire() as conn:
                assert await migrate(conn, schema, _MIGRATIONS[:1], _LOCK_CLASS) == [1]
                await conn.execute(
                    f"INSERT INTO {schema}.cif VALUES ('CIF00000001', 'u1', 'u1@example.com', "
                    f"'Ada Trader', '1990-05-17', 'DE', 'registered', 1)"
                )
                await conn.execute(
                    f"INSERT INTO {schema}.cif_events (cif, subject, ts, kind, detail) "
                    f"VALUES ('CIF00000001', 'u1', 1, 'registered', '{{}}')"
                )
                rows = [  # status, provider_ref, acc, failure, paid_at, provisioned_at
                    ("ORD00000001", "pending", "pending-ORD00000001", None, None, None, None),
                    ("ORD00000002", "pending", "inv-2", None, None, None, None),
                    ("ORD00000003", "paid", "inv-3", "ACC00000001", None, 30, None),
                    ("ORD00000004", "provisioned", "inv-4", "ACC00000002", None, 40, 41),
                    ("ORD00000005", "failed", "inv-5", "ACC00000003", "unknown_rule_set", 50, None),
                ]
                for ord_id, status, ref, acc, failure, paid_at, provisioned_at in rows:
                    await conn.execute(
                        f"INSERT INTO {schema}.orders (ord, cif, product_id, size_cents, "
                        f"list_price_cents, discount_kind, discount, discount_code, price_cents, "
                        f"rsv_id, status, provider, provider_ref, acc, failure, created_at, "
                        f"paid_at, provisioned_at) VALUES ($1, 'CIF00000001', 'lite', 1000000, "
                        f"4400, 'none', '0', NULL, 4400, 'lite-s1-v1', $2, 'sandbox', $3, $4, "
                        f"$5, 10, $6, $7)",
                        ord_id,
                        status,
                        ref,
                        acc,
                        failure,
                        paid_at,
                        provisioned_at,
                    )
                await conn.execute(
                    f"INSERT INTO {schema}.accounts VALUES ('ACC00000002', 'CIF00000001', "
                    f"'ORD00000004', 'lite', 'lite-s1-v1', 1000000, 41)"
                )
            store = await CustomerStore.open(pool, schema)  # applies migrations 2 and 3
            assert await store.rebuild_status() == []  # backfilled history matches every row
            kinds = {
                o: [e.kind for e in await store.order_history(o)]
                for o in ("ORD00000001", "ORD00000002", "ORD00000003", "ORD00000004", "ORD00000005")
            }
            assert kinds == {
                "ORD00000001": ["created"],
                "ORD00000002": ["created", "invoiced"],
                "ORD00000003": ["created", "invoiced", "paid", "account_assigned"],
                "ORD00000004": ["created", "invoiced", "paid", "account_assigned", "provisioned"],
                "ORD00000005": ["created", "invoiced", "paid", "account_assigned", "failed"],
            }
            owned = await store.account("ACC00000002")  # migration 3: the stage, from the rule set
            assert owned is not None and (owned.stage, owned.progression) == ("s1", None)
            created = (await store.order_history("ORD00000004"))[0].detail
            assert created["backfilled"] is True and created["price_cents"] == 4_400
            failed = (await store.order_history("ORD00000005"))[-1].detail
            assert json.dumps(failed, sort_keys=True) == json.dumps(
                {"backfilled": True, "journal_seq": None, "reason": "unknown_rule_set"},
                sort_keys=True,
            )
        finally:
            await pool.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            await pool.close()

    asyncio.run(scenario())
