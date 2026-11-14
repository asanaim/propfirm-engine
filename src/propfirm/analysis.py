"""Trader's analysis: statistics from a trader's own closed trades, as FTMO's
"Trader's Analysis" shows (10 Nov 2026).

A trade is one close (PositionClosed): a position closed in parts counts
once per part, each with its own result. A trade's result is its realized
profit less the commission charged when its position opened, the commission
split over the position's closes by volume (so a position's closes add up to
its net result exactly; the last close takes any remainder of the split).

Money stays in integer cents. Ratios (win rate, reward to risk, profit
factor, Sharpe) are Decimals rounded half-even to two places, returned as
strings. Months are UTC calendar months. Same events in, same figures out.
"""

import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

TWO = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Trade:
    account_id: str
    position_id: str
    symbol: str
    opened: int  # ms UTC
    closed: int
    volume: int
    result_cents: int  # realized profit less its share of the commission
    reason: str = "client"  # why it closed (CloseReason value)


def _ratio(num: int, den: int) -> str | None:
    """num/den rounded half-even to two places; None when den is zero."""
    if den == 0:
        return None
    return str((Decimal(num) / Decimal(den)).quantize(TWO, ROUND_HALF_EVEN))


def _avg(values: list[int]) -> int:
    """The mean in whole cents, rounded half-even; 0 for none."""
    if not values:
        return 0
    return int((Decimal(sum(values)) / len(values)).quantize(Decimal(1), ROUND_HALF_EVEN))


def trades_from(events: Iterable[Mapping[str, Any]]) -> list[Trade]:
    """The trades in one or more accounts' journal events (any order)."""
    opened: dict[str, int] = {}
    volume: dict[str, int] = {}
    commission: dict[str, int] = {}
    closes: list[Mapping[str, Any]] = []
    for e in events:
        kind = e.get("type")
        if kind == "PositionOpened":
            opened[str(e["position_id"])] = int(e["ts"])
            volume[str(e["position_id"])] = int(e["volume"])
        elif kind == "CommissionCharged":
            pid = str(e["position_id"])
            commission[pid] = commission.get(pid, 0) + int(e["commission_cents"])
        elif kind == "PositionClosed":
            closes.append(e)
    closes.sort(key=lambda e: (int(e["ts"]), str(e.get("close_id", ""))))
    charged: dict[str, int] = defaultdict(int)
    out = []
    for e in closes:
        pid = str(e["position_id"])
        fee = commission.get(pid, 0)
        total = volume.get(pid, int(e["volume"]))
        if int(e["remaining_volume"]) == 0:
            share = fee - charged[pid]  # the last close takes the remainder
        else:
            share = fee * int(e["volume"]) // total if total else 0
        charged[pid] += share
        out.append(
            Trade(
                account_id=str(e["account_id"]),
                position_id=pid,
                symbol=str(e["symbol"]),
                opened=opened.get(pid, int(e["ts"])),
                closed=int(e["ts"]),
                volume=int(e["volume"]),
                result_cents=int(e["pnl_cents"]) - share,
                reason=str(e.get("reason", "client")),
            )
        )
    return out


def _month(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, UTC).strftime("%Y-%m")


def _summary(trades: list[Trade]) -> dict[str, Any]:
    results = [t.result_cents for t in trades]
    wins = [r for r in results if r > 0]
    losses = [r for r in results if r < 0]
    avg_win, avg_loss = _avg(wins), _avg(losses)
    sharpe = None
    if len(results) >= 2:
        sd = statistics.pstdev(Decimal(r) for r in results)
        if sd:
            mean = Decimal(sum(results)) / len(results)
            sharpe = str((mean / sd).quantize(TWO, ROUND_HALF_EVEN))
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_percent": _ratio(100 * len(wins), len(trades)),
        "net_cents": sum(results),
        "avg_profit_cents": avg_win,
        "avg_loss_cents": avg_loss,
        "reward_to_risk": _ratio(avg_win, -avg_loss),
        "profit_factor": _ratio(sum(wins), -sum(losses)),
        "expectancy_cents": _avg(results),
        "sharpe_per_trade": sharpe,
    }


def analyse(trades: Iterable[Trade], since: int = 0, until: int | None = None) -> dict[str, Any]:
    """The figures for the trades closed in [since, until)."""
    chosen = sorted(
        (t for t in trades if t.closed >= since and (until is None or t.closed < until)),
        key=lambda t: (t.closed, t.position_id),
    )
    cumulative, running = [], 0
    for t in chosen:
        running += t.result_cents
        cumulative.append({"ts": t.closed, "cents": running})
    by_symbol: dict[str, list[Trade]] = defaultdict(list)
    by_month: dict[str, list[Trade]] = defaultdict(list)
    for t in chosen:
        by_symbol[t.symbol].append(t)
        by_month[_month(t.closed)].append(t)
    symbols = [
        {"symbol": s, "trades": len(ts), "net_cents": sum(t.result_cents for t in ts)}
        for s, ts in sorted(by_symbol.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    months = []
    for m in sorted(by_month):
        ts = by_month[m]
        held = [t.closed - t.opened for t in ts]
        s = _summary(ts)
        months.append(
            {
                "month": m,
                "trades": s["trades"],
                "net_cents": s["net_cents"],
                "win_rate_percent": s["win_rate_percent"],
                "reward_to_risk": s["reward_to_risk"],
                "expectancy_cents": s["expectancy_cents"],
                "avg_hold_minutes": int(
                    (Decimal(sum(held)) / len(held) / 60_000).quantize(Decimal(1), ROUND_HALF_EVEN)
                )
                if held
                else 0,
            }
        )
    return {
        "summary": _summary(chosen),
        "cumulative": cumulative,
        "symbols": symbols,
        "months": months,
    }


@dataclass(frozen=True, slots=True)
class ShortHold:
    """Profit from trades the trader closed within `seconds` of opening,
    against all profit, from `since` (ms UTC). Profit counts winning
    trades only (results after commission); losses do not offset it."""

    seconds: int
    since: int
    trades: int
    short_trades: int
    profit_cents: int
    short_profit_cents: int

    @property
    def share_basis_points(self) -> int:
        """The short share in hundredths of a percent, rounded down."""
        if self.profit_cents <= 0:
            return 0
        return self.short_profit_cents * 10_000 // self.profit_cents

    def over(self, percent: int) -> bool:
        """Above `percent` of profit (0: never)."""
        return percent > 0 and self.short_profit_cents * 100 > percent * self.profit_cents

    def view(self) -> dict[str, int]:
        return {
            "seconds": self.seconds,
            "since": self.since,
            "trades": self.trades,
            "short_trades": self.short_trades,
            "profit_cents": self.profit_cents,
            "short_profit_cents": self.short_profit_cents,
            "share_basis_points": self.share_basis_points,
        }


def short_hold(trades: Iterable[Trade], seconds: int, since: int = 0) -> ShortHold:
    """The short-hold measure of trades closed at or after `since`. Only
    closes by the trader count as short (a stop loss or take profit can
    close a fair trade in seconds); a partial close is its own trade."""
    count = short = profit = short_profit = 0
    for t in trades:
        if t.closed < since:
            continue
        count += 1
        quick = t.reason == "client" and t.closed - t.opened < seconds * 1000
        short += quick
        if t.result_cents > 0:
            profit += t.result_cents
            if quick:
                short_profit += t.result_cents
    return ShortHold(seconds, since, count, short, profit, short_profit)
