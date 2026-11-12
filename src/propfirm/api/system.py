"""System information for admins (rule AC23, 12 Nov 2026).

What is running and whether its parts answer: the application version and
build, Python, PostgreSQL and Valkey versions, when this API process
started and the engine journal's head. Read only; nothing here feeds a
decision.

The spec's "cache clearing" and "updates" are not console actions here.
No cache holds a value staff could need to drop (settings, policies and
site text are read from their tables when used), and code is updated by a
reviewed pull request and a redeploy, never from the console: an update
button would let one admin change running code, which the two person
rule (AC00) and the change process forbid.
"""

import platform
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import asyncpg

import propfirm


class _Valkey(Protocol):
    def info(self, section: str) -> Awaitable[dict[str, Any]]: ...


def valkey_version(info: dict[str, Any]) -> str:
    """The server version from INFO server (Valkey reports both fields; a
    Redis server only the second)."""
    return str(info.get("valkey_version") or info.get("redis_version") or "unknown")


def postgres_version(full: str) -> str:
    """'PostgreSQL 18.6 on x86_64-pc-linux-musl, ...' -> 'PostgreSQL 18.6'."""
    return " ".join(full.split()[:2]) if full.startswith("PostgreSQL") else full


class SystemProbe:
    def __init__(
        self,
        pool: asyncpg.Pool,
        valkey: _Valkey,
        started_at: int,
        revision: str,
        head: Callable[[], Awaitable[dict[str, Any] | None]],
    ) -> None:
        self._pool, self._valkey, self._head = pool, valkey, head
        self._started_at, self._revision = started_at, revision or "unknown"

    async def _part(self, name: str, read: Callable[[], Awaitable[str]]) -> dict[str, str]:
        try:
            return {"name": name, "status": "ok", "version": await read()}
        except Exception as exc:  # a part that does not answer is reported, not raised
            return {"name": name, "status": "unavailable", "version": type(exc).__name__}

    async def info(self) -> dict[str, Any]:
        async def pg() -> str:
            return postgres_version(str(await self._pool.fetchval("SELECT version()")))

        async def vk() -> str:
            return valkey_version(await self._valkey.info("server"))

        head = await self._head()
        return {
            "application": {
                "name": "pipridge engine",
                "version": propfirm.__version__,
                "revision": self._revision,
                "python": platform.python_version(),
                "api_started_at": self._started_at,
            },
            "parts": [await self._part("PostgreSQL", pg), await self._part("Valkey", vk)],
            "journal": {
                "seq": head.get("seq") if head else None,
                "engine_ts": head.get("engine_ts") if head else None,
            },
        }
