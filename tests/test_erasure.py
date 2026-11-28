"""Erasure rules (rule AC27; D-15.5): the retention date and what blocks."""

import asyncio
import datetime as dt
from types import SimpleNamespace
from typing import Any

from propfirm.customers.erasure import DAY_MS, Erasures, retained_until


def ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC).timestamp() * 1000)


def test_retention_counts_calendar_years_and_rounds_up() -> None:
    assert retained_until(ms(2026, 11, 1, 9, 30), 5) == ms(2031, 11, 1, 9, 30)
    assert retained_until(ms(2028, 2, 29, 12), 5) == ms(2033, 3, 1, 12)  # never the 28th
    assert retained_until(ms(2028, 2, 29), 4) == ms(2032, 2, 29)  # leap year to leap year
    assert retained_until(ms(2026, 12, 31, 23, 59), 7) == ms(2033, 12, 31, 23, 59)


class FakeConn:
    """Answers the queries `blocks` asks, by a word in each."""

    def __init__(self, last: int, payout_open: bool = False, case_open: bool = False) -> None:
        self.last = last
        self.payout_open = payout_open
        self.case_open = case_open

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "erasure_runs" in sql:
            return None
        if ".payouts" in sql:
            return 1 if self.payout_open else None
        if ".cases" in sql:
            return 1 if self.case_open else None
        return 1  # the customer exists

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        return {"max": self.last if "cif WHERE" in sql and ".cif " in sql else None}

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, str]]:
        return [{"acc": "ACC00000001"}]


class FakeLive:
    def __init__(self, view: dict[str, Any] | None) -> None:
        self.view = view

    async def account(self, account_id: str) -> dict[str, Any] | None:
        return self.view


class FakeSettings:
    async def current(self) -> Any:
        return SimpleNamespace(retention=SimpleNamespace(aml_years=5))


def blocks(conn: FakeConn, view: dict[str, Any] | None, now: int) -> list[str]:
    erasures = Erasures(None, "c", FakeSettings(), FakeLive(view))  # type: ignore[arg-type]
    return asyncio.run(erasures.blocks(conn, "CIF00000001", now))


def test_an_account_holding_capital_or_trading_lately_keeps_the_data() -> None:
    t0 = ms(2020, 1, 1)
    later = ms(2030, 1, 1)
    ended = {"status": "breached", "trading_day_ids": [], "day_pnl": []}
    assert blocks(FakeConn(t0), ended, later) == []
    held = blocks(FakeConn(t0), {**ended, "status": "active"}, later)
    assert held == ["account ACC00000001 is active"]
    # the last trading day counts as activity, to its end
    day = ms(2025, 6, 1, 21)
    traded = blocks(FakeConn(t0), {**ended, "day_pnl": [[day, -100]]}, later)
    assert traded == [
        f"kept until {retained_until(day + DAY_MS, 5)} (5 years after the last activity)"
    ]
    assert blocks(FakeConn(t0), None, later) == []  # never provisioned


def test_open_money_or_a_case_keeps_the_data() -> None:
    t0, later = ms(2020, 1, 1), ms(2030, 1, 1)
    assert blocks(FakeConn(t0, payout_open=True), None, later) == ["a payout is not settled"]
    assert blocks(FakeConn(t0, case_open=True), None, later) == ["a case is open (legal hold)"]
