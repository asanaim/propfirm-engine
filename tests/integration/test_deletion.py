"""Deletion (rule AC27; D-15.4): records are never deleted. Every table of
every store refuses DELETE and TRUNCATE; a remove action is a new record.
A table added later without its guard fails this test."""

import asyncio

import asyncpg
import pytest

from propfirm.audit.store import migrate as audit_migrate
from propfirm.customers.store import _LOCK_CLASS, _MIGRATIONS
from propfirm.customers.store import migrate as customers_migrate
from propfirm.pgschema import migrate as _migrate
from propfirm.settings.store import migrate as settings_migrate
from propfirm.staff.store import migrate as staff_migrate

pytestmark = pytest.mark.integration

STORES = {
    "c": customers_migrate,
    "s": settings_migrate,
    "a": audit_migrate,
    "st": staff_migrate,
}

# a guard is a BEFORE trigger: bit 2; DELETE bit 8; TRUNCATE bit 32
UNGUARDED = """
SELECT c.relname,
    bool_or((t.tgtype & 2) > 0 AND (t.tgtype & 8) > 0) AS no_delete,
    bool_or((t.tgtype & 2) > 0 AND (t.tgtype & 32) > 0) AS no_truncate
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_trigger t ON t.tgrelid = c.oid AND NOT t.tgisinternal
WHERE n.nspname = $1 AND c.relkind = 'r'
    AND coalesce(obj_description(c.oid, 'pg_class'), '') NOT LIKE 'licensed:%'
GROUP BY c.relname
HAVING NOT coalesce(bool_or((t.tgtype & 2) > 0 AND (t.tgtype & 8) > 0), false)
    OR NOT coalesce(bool_or((t.tgtype & 2) > 0 AND (t.tgtype & 32) > 0), false)
ORDER BY 1
"""

# the only exemptions: licensed prices, purgeable by the data licence
LICENSED = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1 AND c.relkind = 'r'
    AND obj_description(c.oid, 'pg_class') LIKE 'licensed:%'
ORDER BY 1
"""
LICENSED_TABLES = {"c": ["stale_fills"], "s": [], "a": [], "st": []}


def test_every_table_refuses_delete_and_truncate(database_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        conn = await asyncpg.connect(database_url)
        try:
            for tag, migrate in STORES.items():
                schema = f"{pg_schema}_{tag}"
                try:
                    await migrate(conn, schema)
                    tables = await conn.fetchval(
                        "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid = c.relnamespace WHERE n.nspname = $1 AND c.relkind = 'r'",
                        schema,
                    )
                    assert tables > 0
                    assert [r["relname"] for r in await conn.fetch(UNGUARDED, schema)] == []
                    licensed = await conn.fetch(LICENSED, schema)
                    assert [r["relname"] for r in licensed] == LICENSED_TABLES[tag]
                    for sql in (
                        f"DELETE FROM {schema}.schema_migrations",
                        f"TRUNCATE {schema}.schema_migrations CASCADE",
                    ):
                        with pytest.raises(asyncpg.RaiseError):
                            await conn.execute(sql)
                finally:
                    await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_existing_opt_outs_carry_over(database_url: str, pg_schema: str) -> None:
    """Migration 37 turns each leaderboard opt-out row into a record."""

    async def scenario() -> None:
        conn = await asyncpg.connect(database_url)
        s = f"{pg_schema}_c"
        try:
            await _migrate(conn, s, _MIGRATIONS[:36], _LOCK_CLASS)
            for cif in ("C1", "C2"):
                await conn.execute(
                    f"INSERT INTO {s}.cif (cif, subject, email, full_name, date_of_birth, "
                    f"residence_country, status, created_at) VALUES ($1, $1, $1, 'A', "
                    f"'1990-01-01', 'DE', 'registered', 1)",
                    cif,
                )
            await conn.execute(f"INSERT INTO {s}.leaderboard_hidden VALUES ('C1', 7)")
            assert await customers_migrate(conn, s) == [37]
            rows = await conn.fetch(f"SELECT cif, visible, ts FROM {s}.leaderboard_choices")
            assert [tuple(r) for r in rows] == [("C1", False, 7)]
            shown = f"SELECT {s}.leaderboard_shown($1)"
            assert await conn.fetchval(shown, "C1") is False
            assert await conn.fetchval(shown, "C2") is True
        finally:
            await conn.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
            await conn.close()

    asyncio.run(scenario())
