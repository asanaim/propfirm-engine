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
