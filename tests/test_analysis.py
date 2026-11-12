"""Trader's analysis: figures from a trader's own closed trades."""

import asyncio
from typing import Any

import httpx

from propfirm.analysis import Trade, analyse, trades_from
from propfirm.api.app import Deps, create_app
from tests.auth_rig import FakeCustomers, bearer, verifier
from tests.test_service import NOW, FakeGateway, FakeLive

DAY = 86_400_000
T0 = 1_791_000_000_000  # 2026-10-03 UTC


def opened(pid: str, ts: int, volume: int = 100, acc: str = "ACC00000001") -> dict[str, Any]:
    return {"type": "PositionOpened", "position_id": pid, "ts": ts, "volume": volume,
            "account_id": acc}  # fmt: skip


def closed(pid: str, ts: int, pnl: int, *, volume: int = 100, remaining: int = 0,
           symbol: str = "EUR/USD", acc: str = "ACC00000001") -> dict[str, Any]:  # fmt: skip
    return {"type": "PositionClosed", "position_id": pid, "close_id": f"{pid}-{ts}", "ts": ts,
            "pnl_cents": pnl, "volume": volume, "remaining_volume": remaining,
            "symbol": symbol, "account_id": acc}  # fmt: skip


def fee(pid: str, cents: int) -> dict[str, Any]:
    return {"type": "CommissionCharged", "position_id": pid, "commission_cents": cents}


def test_a_trade_is_its_profit_less_its_share_of_the_commission() -> None:
    events = [
        opened("P1", T0, volume=100),
        fee("P1", 101),
        closed("P1", T0 + 60_000, 3_000, volume=30, remaining=70),
        closed("P1", T0 + 120_000, -500, volume=70, remaining=0),
    ]
    trades = trades_from(events)
    assert [t.result_cents for t in trades] == [3_000 - 30, -500 - 71]  # 101 split 30/71
    assert sum(t.result_cents for t in trades) == 3_000 - 500 - 101


def test_the_summary_follows_the_industry_definitions() -> None:
    trades = [
        Trade("A", "1", "EUR/USD", T0, T0 + 3_600_000, 100, 40_000),
        Trade("A", "2", "XAU/USD", T0, T0 + DAY, 100, -20_000),
        Trade("A", "3", "EUR/USD", T0, T0 + 2 * DAY, 100, 20_000),
        Trade("A", "4", "EUR/USD", T0, T0 + 40 * DAY, 100, -10_000),
    ]
    out = analyse(trades)
    s = out["summary"]
    assert s["trades"] == 4 and s["wins"] == 2 and s["losses"] == 2
    assert s["win_rate_percent"] == "50.00"
    assert s["net_cents"] == 30_000
    assert (s["avg_profit_cents"], s["avg_loss_cents"]) == (30_000, -15_000)
    assert s["reward_to_risk"] == "2.00"
    assert s["profit_factor"] == "2.00"  # 60,000 won / 30,000 lost
    assert s["expectancy_cents"] == 7_500
    assert s["sharpe_per_trade"] == "0.31"  # mean 7,500 / population sd 23,848.5
    assert [c["cents"] for c in out["cumulative"]] == [40_000, 20_000, 40_000, 30_000]
    assert out["symbols"] == [
        {"symbol": "EUR/USD", "trades": 3, "net_cents": 50_000},
        {"symbol": "XAU/USD", "trades": 1, "net_cents": -20_000},
    ]
    assert [(m["month"], m["trades"], m["net_cents"]) for m in out["months"]] == [
        ("2026-10", 3, 40_000),
        ("2026-11", 1, -10_000),
    ]
    assert out["months"][0]["avg_hold_minutes"] == 1_460  # (1 h + 24 h + 48 h) / 3 = 24 h 20 min


def test_no_trades_and_a_date_range() -> None:
    empty = analyse([])["summary"]
    assert empty["trades"] == 0 and empty["win_rate_percent"] is None
    assert empty["profit_factor"] is None and empty["sharpe_per_trade"] is None
    trades = [Trade("A", str(i), "EUR/USD", T0, T0 + i * DAY, 1, 100) for i in range(5)]
    assert analyse(trades, since=T0 + DAY, until=T0 + 3 * DAY)["summary"]["trades"] == 2
    assert analyse(trades, since=T0)["summary"]["profit_factor"] is None  # no losses


class History:
    def __init__(self, by_account: dict[str, list[dict[str, Any]]]) -> None:
        self.by_account = by_account

    async def account_events(
        self, account_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        rows = [{"seq": i + 1, **e} for i, e in enumerate(self.by_account.get(account_id, []))]
        return [r for r in rows if r["seq"] > after_seq][:limit]

    async def bars(
        self, symbol: str, interval: str, limit: int = 300, before: int | None = None
    ) -> list[dict[str, int]]:
        return []


def ask(url: str, user: str = "user-1") -> httpx.Response:
    history = History({
        "ACC00000001": [opened("P1", T0), closed("P1", T0 + DAY, 5_000)],
        "ACC00000002": [opened("P2", T0, acc="ACC00000002"),
                        closed("P2", T0 + DAY, -1_000, acc="ACC00000002")],
        "ACC00000003": [opened("P3", T0, acc="ACC00000003"),
                        closed("P3", T0 + DAY, 99_999, acc="ACC00000003")],
    })  # fmt: skip
    customers = FakeCustomers(
        cifs={"user-1": "CIF00000001", "user-2": "CIF00000002"},
        owners={"ACC00000001": "CIF00000001", "ACC00000002": "CIF00000001",
                "ACC00000003": "CIF00000002"},
    )  # fmt: skip
    deps = Deps(FakeLive(), history, FakeGateway(), verifier(), customers, clock=lambda: NOW)
    app = create_app(deps)

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api"
        ) as c:
            return await c.get(url, headers=bearer(user, ("trader",)))

    return asyncio.run(go())


def test_a_trader_sees_their_own_accounts_only() -> None:
    mine = ask("/me/analysis").json()
    assert mine["accounts"] == ["ACC00000001", "ACC00000002"]
    assert mine["summary"]["net_cents"] == 4_000  # never the other trader's 99,999
    one = ask("/me/analysis?account=ACC00000002").json()
    assert one["summary"]["net_cents"] == -1_000
    assert ask("/me/analysis?account=ACC00000003").status_code == 404
