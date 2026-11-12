"""System information for admins (rule AC23)."""

import asyncio
from typing import Any

import propfirm
from propfirm.api.system import SystemProbe, postgres_version, valkey_version


def test_versions_are_read_from_the_servers_own_words() -> None:
    assert postgres_version("PostgreSQL 18.6 on x86_64-pc-linux-musl, compiled by gcc") == (
        "PostgreSQL 18.6"
    )
    assert postgres_version("odd") == "odd"
    assert valkey_version({"valkey_version": "9.1.3", "redis_version": "7.2.4"}) == "9.1.3"
    assert valkey_version({"redis_version": "7.2.4"}) == "7.2.4"
    assert valkey_version({}) == "unknown"


class _Pool:
    async def fetchval(self, sql: str) -> str:
        assert sql == "SELECT version()"
        return "PostgreSQL 18.6 on x86_64"


class _DownValkey:
    async def info(self, section: str) -> dict[str, Any]:
        raise ConnectionRefusedError(section)


def test_a_part_that_does_not_answer_is_reported_not_raised() -> None:
    async def head() -> dict[str, Any] | None:
        return {"seq": 42, "hash": "h", "engine_ts": 7}

    probe = SystemProbe(_Pool(), _DownValkey(), 1_000, "", head)  # type: ignore[arg-type]
    info = asyncio.run(probe.info())
    assert info["application"]["version"] == propfirm.__version__
    assert (info["application"]["revision"], info["application"]["api_started_at"]) == (
        "unknown",
        1_000,
    )
    assert info["parts"] == [
        {"name": "PostgreSQL", "status": "ok", "version": "PostgreSQL 18.6"},
        {"name": "Valkey", "status": "unavailable", "version": "ConnectionRefusedError"},
    ]
    assert info["journal"] == {"seq": 42, "engine_ts": 7}
