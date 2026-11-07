"""The audit trail in PostgreSQL and through the API [P2 step 7b]: the
database refuses edits and out-of-chain rows, concurrent appends stay one
chain, requests are recorded as specified, admins read it, and the auditor
catches an edit made with the triggers switched off."""

import asyncio
from pathlib import Path

import asyncpg
import pytest

from propfirm.audit.auditor import Auditor
from propfirm.audit.chain import GENESIS, AuditEvent, check
from propfirm.audit.store import AuditStore, migrate
from propfirm.journal.anchor import FileAnchorStore
from propfirm.service.feeder import now_ms
from propfirm.staff.directory import Owner
from tests.auth_rig import bearer
from tests.integration.test_customers import platform

pytestmark = pytest.mark.integration

OWNERS = (Owner("kc-owner-a", "Owner A"), Owner("kc-owner-b", "Owner B"))
NONE: tuple[str, ...] = ()


def ev(i: int) -> AuditEvent:
    return AuditEvent(1_794_000_000_000 + i, "kc-x", "POST /staff/members", "", 201, "", f"r{i}")


def test_database_keeps_one_append_only_chain(database_url: str, pg_schema: str) -> None:
    async def scenario() -> None:
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=8)
        try:
            async with pool.acquire() as conn:
                await migrate(conn, pg_schema)
                assert await migrate(conn, pg_schema) == []
            store = AuditStore(pool, pg_schema)
            assert await store.head() == (0, GENESIS)
            # concurrent appends serialise into one gap-free chain
            await asyncio.gather(*(store.append(ev(i)) for i in range(40)))
            records = await store.records(limit=100)
            assert [r.seq for r in records] == list(range(1, 41))
            assert check(records, 0, GENESIS) is None
            assert await store.head() == (40, records[-1].hash)
            s = pg_schema
            for sql in (
                f"UPDATE {s}.audit_log SET actor = 'kc-y' WHERE seq = 1",
                f"DELETE FROM {s}.audit_log WHERE seq = 40",
                f"TRUNCATE {s}.audit_log",
                f"DELETE FROM {s}.audit_head",
                f"UPDATE {s}.audit_head SET seq = 39, hash = '{records[38].hash}'",
                f"INSERT INTO {s}.audit_log VALUES (99, 1, 'a', 'b', '', 200, '', '', "
                f"'{records[-1].hash}', '{'a' * 64}')",
                f"INSERT INTO {s}.audit_log VALUES (41, 1, 'a', 'b', '', 200, '', '', "
                f"'{GENESIS}', '{'a' * 64}')",
            ):
                with pytest.raises(asyncpg.RaiseError):
                    await pool.execute(sql)
            assert len(await store.records(limit=100)) == 40
            newest = await store.records(limit=3, newest_first=True)
            assert [r.seq for r in newest] == [40, 39, 38]
            assert [r.seq for r in await store.records(after=38, limit=5, newest_first=True)] == [
                37,
                36,
                35,
                34,
                33,
            ]
        finally:
            await pool.close()

    asyncio.run(scenario())


def test_api_records_requests_and_auditor_catches_an_edit(
    database_url: str, valkey_url: str, pg_schema: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with platform(database_url, valkey_url, pg_schema, start_engine=False,
                            owners=OWNERS, audit=True) as p:  # fmt: skip
            assert p.audit is not None
            # a staff read: recorded, with the proxy's address and the request id
            headers = {**bearer("kc-owner-a", NONE), "X-Forwarded-For": "6.6.6.6, 203.0.113.9"}
            members = await p.api.get("/staff/members", headers=headers)
            assert members.status_code == 200
            # refusals: no token, and a trader at a staff route
            assert (await p.api.get("/me")).status_code == 401
            refused = await p.call("GET", "/staff/members", "kc-trader", None, ("trader",))
            assert refused.status_code == 403
            # not recorded: public and traders' reads
            assert (await p.api.get("/catalogue")).status_code == 200
            await p.call("GET", "/me", "kc-trader")
            records = await p.audit.records(limit=50)
            seen = [(r.event.actor, r.event.action, r.event.status) for r in records]
            assert seen == [
                ("kc-owner-a", "GET /staff/members", 200),
                ("anonymous", "GET /me", 401),
                ("kc-trader", "GET /staff/members", 403),
            ]
            assert records[0].event.ip == "203.0.113.9"
            assert records[0].event.request_id == members.headers["X-Request-Id"]
            # admins and owners read it, newest first, with names; others may not
            got = await p.call("GET", "/staff/audit?limit=2", "kc-owner-b", None, NONE)
            assert got.status_code == 200, got.text
            body = got.json()
            assert body["head"] == 3
            assert [r["seq"] for r in body["records"]] == [3, 2]
            older = await p.call("GET", "/staff/audit?before=2", "kc-owner-b", None, NONE)
            assert [r["seq"] for r in older.json()["records"]] == [1]
            assert older.json()["records"][0]["actor_name"].startswith("Owner A (STF")
            assert (await p.call("GET", "/staff/audit", "kc-trader")).status_code == 403
            # the auditor agrees, anchors, then catches an edit made with the
            # triggers off (what a database superuser could do)
            anchors = FileAnchorStore(tmp_path / "audit-anchors.jsonl")
            status = await Auditor(p.audit, anchors, [], now_ms).run_once()
            assert status.broken_seq is None and status.anchored_seq >= 6
            s = p.config.audit_schema
            conn = await asyncpg.connect(database_url)
            try:
                async with conn.transaction():
                    await conn.execute(f"ALTER TABLE {s}.audit_log DISABLE TRIGGER USER")
                    await conn.execute(f"UPDATE {s}.audit_log SET actor = 'kc-0' WHERE seq = 2")
                    await conn.execute(f"ALTER TABLE {s}.audit_log ENABLE TRIGGER USER")
            finally:
                await conn.close()
            status = await Auditor(p.audit, anchors, [], now_ms).run_once()
            assert status.broken_seq == 2
            shown = await p.call("GET", "/staff/audit", "kc-owner-a", None, NONE)
            assert shown.json()["status"]["broken_seq"] == 2

    asyncio.run(scenario())
