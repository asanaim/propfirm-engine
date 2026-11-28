"""PostgreSQL schemas of the platform stores: names and numbered migrations.

Each store keeps its tables in its own schema. Schema changes are numbered
migrations applied once, in order, under an advisory lock, so two processes
starting together do not race; a new change is a new number, never an edit.
"""

import re
from collections.abc import Sequence

import asyncpg
from asyncpg.pool import PoolConnectionProxy

type Conn = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def check_schema(name: str) -> str:
    """`name` if it is a safe schema name (it is spliced into SQL)."""
    if not _NAME.fullmatch(name):
        raise ValueError(f"schema must match {_NAME.pattern}")
    return name


async def migrate(
    conn: Conn, schema: str, migrations: Sequence[tuple[int, str]], lock_class: int
) -> list[int]:
    """Apply the migrations not yet applied; return their numbers. Each
    migration's SQL names the schema as `{s}`."""
    s = check_schema(schema)
    applied: list[int] = []
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1, hashtext($2))", lock_class, s)
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
        await conn.execute(
            f"CREATE TABLE IF NOT EXISTS {s}.schema_migrations ("
            f"version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {
            r["version"] for r in await conn.fetch(f"SELECT version FROM {s}.schema_migrations")
        }
        for version, sql in migrations:
            if version in done:
                continue
            await conn.execute(sql.format(s=s))
            await conn.execute(f"INSERT INTO {s}.schema_migrations (version) VALUES ($1)", version)
            applied.append(version)
    return applied


# Rule AC27 (D-15.4): records are never deleted. Run at the end of a
# migration, this guards every table of the schema that has no guard yet
# against DELETE (each row) and TRUNCATE, except a table of licensed prices
# (its comment starts "licensed:"), which the data licence requires to be
# purgeable within 30 days of cancelling. A new table added later gets its
# guard from a later migration ending in this; tests/integration/
# test_deletion.py fails while any table lacks one.
GUARD_DELETES = """
CREATE OR REPLACE FUNCTION {s}.no_delete() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION '% keeps its rows: records are never deleted (rule AC27)', TG_TABLE_NAME;
END $fn$;
DO $guard$
DECLARE
    t text;
BEGIN
    FOR t IN
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = '{s}' AND c.relkind = 'r'
            AND coalesce(obj_description(c.oid, 'pg_class'), '') NOT LIKE 'licensed:%'
        ORDER BY c.relname
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger g JOIN pg_class r ON r.oid = g.tgrelid
            JOIN pg_namespace q ON q.oid = r.relnamespace
            WHERE q.nspname = '{s}' AND r.relname = t AND NOT g.tgisinternal
                AND (g.tgtype & 2) > 0 AND (g.tgtype & 8) > 0
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE DELETE ON %I.%I '
                    || 'FOR EACH ROW EXECUTE FUNCTION %I.no_delete()',
                left(t, 50) || '_no_delete', '{s}', t, '{s}');
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger g JOIN pg_class r ON r.oid = g.tgrelid
            JOIN pg_namespace q ON q.oid = r.relnamespace
            WHERE q.nspname = '{s}' AND r.relname = t AND NOT g.tgisinternal
                AND (g.tgtype & 2) > 0 AND (g.tgtype & 32) > 0
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE TRUNCATE ON %I.%I '
                    || 'FOR EACH STATEMENT EXECUTE FUNCTION %I.no_delete()',
                left(t, 48) || '_no_truncate', '{s}', t, '{s}');
        END IF;
    END LOOP;
END $guard$;
"""
