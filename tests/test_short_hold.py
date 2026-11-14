"""The short-hold measure (build plan step 1, line 8)."""

from propfirm.analysis import Trade, short_hold
from propfirm.policy import load_policy


def trade(opened: int, closed: int, result: int, reason: str = "client") -> Trade:
    return Trade("A", f"p{opened}", "EUR/USD", opened, closed, 100, result, reason)


def test_profit_from_quick_closes_by_the_trader_is_measured() -> None:
    trades = [
        trade(0, 59_999, 600),  # short, profit
        trade(0, 60_000, 300),  # 60 s exactly: not short
        trade(0, 5_000, 100, "stop_loss"),  # a stop: never short
        trade(0, 10_000, -500),  # short, a loss: profit only counts winners
    ]
    m = short_hold(trades, 60)
    assert (m.trades, m.short_trades, m.profit_cents, m.short_profit_cents) == (4, 2, 1_000, 600)
    assert m.share_basis_points == 6_000
    assert m.over(50) and not m.over(60) and not m.over(0)


def test_only_the_period_counts_and_no_profit_is_never_over() -> None:
    trades = [trade(0, 1_000, 900), trade(100_000, 300_000, 100)]
    m = short_hold(trades, 60, since=50_000)
    assert (m.trades, m.short_profit_cents, m.share_basis_points) == (1, 0, 0)
    assert not short_hold([trade(0, 1_000, -5)], 60).over(50)


def test_the_shipped_policy_holds_above_half() -> None:
    p = load_policy().payouts
    assert (p.short_hold_seconds, p.short_hold_share_percent) == (60, 50)
