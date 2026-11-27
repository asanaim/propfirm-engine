"""Request rate (build plan step 5, line 3; D-14.31; strategic review v3, D6).

Order requests (place, trailing entry, cancel, close, stops, trailing stop)
are counted per account per trading day (17:00 New York). Above the limit
the API refuses them (429 `request_rate`) for the rest of that day. The
first refusal of a day is recorded in `rate_days`; the `rate_repeats`-th
such day within `rate_repeat_days` raises a medium integrity alert, which
opens a case. One alert per throttled day.

The count lives in the API process: one process at launch. With several
API processes each counts its own share, so the limit is per process until
the count moves to Valkey.
"""

from collections.abc import Callable
from typing import Any

import asyncpg

from propfirm.customers.clusters import Alerts
from propfirm.rules.clock import NEW_YORK_17

DAY_MS = 86_400_000
SOURCE = "detector"


class RequestRateExceeded(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"more than {limit} order requests today; try again after 17:00 New York")
        self.limit = limit


class Counter:
    """Order requests per (account, trading day); only today's are kept."""

    def __init__(self, day_of: Callable[[int], int] = NEW_YORK_17.day_start) -> None:
        self._day_of = day_of
        self._day = -1
        self._counts: dict[str, int] = {}

    def add(self, acc: str, now: int) -> tuple[int, int]:
        """Count one request; return (trading day, requests so far today)."""
        day = self._day_of(now)
        if day != self._day:
            self._day, self._counts = day, {}
        n = self._counts.get(acc, 0) + 1
        self._counts[acc] = n
        return day, n


class RequestRate:
    def __init__(self, pool: asyncpg.Pool, schema: str, policy: Any, alerts: Alerts) -> None:
        self._pool = pool
        self._s = schema
        self._policy = policy
        self._alerts = alerts
        self._counter = Counter()

    async def check(self, acc: str, now: int) -> None:
        """Count an order request; raise RequestRateExceeded above the limit."""
        policy = (await self._policy.current()).detectors
        if not policy.rate:
            return
        day, n = self._counter.add(acc, now)
        if n <= policy.rate_orders_per_day:
            return
        if n == policy.rate_orders_per_day + 1:  # the day's first refusal
            await self._record(acc, day, n, now, policy)
        raise RequestRateExceeded(policy.rate_orders_per_day)

    async def _record(self, acc: str, day: int, n: int, now: int, policy: Any) -> None:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                f"INSERT INTO {s}.rate_days (acc, day, at, requests) VALUES ($1, $2, $3, $4) "
                f"ON CONFLICT (acc, day) DO NOTHING",
                acc,
                day,
                now,
                n,
            )
            days = await conn.fetch(
                f"SELECT day FROM {s}.rate_days WHERE acc = $1 AND day > $2 ORDER BY day",
                acc,
                day - policy.rate_repeat_days * DAY_MS,
            )
            cif = await conn.fetchval(f"SELECT cif FROM {s}.accounts WHERE acc = $1", acc)
        if len(days) < policy.rate_repeats or cif is None:
            return
        await self._alerts.alert(
            str(cif),
            acc,
            SOURCE,
            "integrity",
            "medium",
            {
                "key": f"rate:{day}",
                "detector": "request_rate",
                "limit": policy.rate_orders_per_day,
                "days": [int(d["day"]) for d in days],
            },
            now,
        )
