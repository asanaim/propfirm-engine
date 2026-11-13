"""The live state the API serves (published by the engine): what the
portal's dashboard shows, from a real machine."""

from propfirm.journal.inputs import Input, OpenAccount, PlaceOrder, ScheduleNews
from propfirm.journal.machine import CODEC, Machine
from propfirm.market.types import PriceUpdate
from propfirm.policy import load_policy
from propfirm.rules.clock import NEW_YORK_17
from propfirm.rules.news import EventScope, RestrictedEvent
from propfirm.service.live import account_view, news_view
from propfirm.service.setup import setup_from_policy
from propfirm.trading.orders import OrderRequest, OrderType, Side

T0 = 1_790_000_000_000


def machine() -> Machine:
    m = Machine(CODEC.encode(setup_from_policy(load_policy())), {1: bytes(32)})
    m.use_price_key(1)
    return m


def test_the_account_view_has_limits_the_next_reset_and_position_pnl() -> None:
    m = machine()
    inputs: list[Input] = [
        OpenAccount("A", "lite-s1-v3", 1_000_000, T0),
        PriceUpdate("EUR/USD", 1, T0, T0, 110_000),
        PlaceOrder(OrderRequest("o1", "A", "EUR/USD", Side.BUY, OrderType.MARKET, 100), T0 + 1),
        PriceUpdate("EUR/USD", 2, T0 + 2, T0 + 2, 110_100),
    ]
    for n, item in enumerate(inputs, start=1):
        m.apply(f"{n}-0", item)
    view = account_view(m, "A")
    assert (view["initial_balance_cents"], view["daily_loss_limit_cents"]) == (1_000_000, 30_000)
    assert view["max_loss_limit_cents"] == 60_000  # Lite: 3 % daily, 6 % maximum of $10,000
    assert view["next_reset_ts"] == NEW_YORK_17.next_boundary(T0 + 2)
    assert (
        view["next_daily_floor_cents"] == max(view["balance_cents"], view["equity_cents"]) - 30_000
    )
    (position,) = view["positions"]
    assert position["position_id"] == "o1"
    quote = m.supervisor.engine.quote("EUR/USD")
    assert quote is not None and position["current_price"] == quote.bid  # a buy closes at the bid
    assert position["pnl_cents"] == view["equity_cents"] - view["balance_cents"]
    assert view["consistency_ok"] is True


def test_the_news_view_lists_events_not_over_yet() -> None:
    m = machine()
    release = T0 + 10 * 60_000
    event = RestrictedEvent("us-nfp", release, EventScope.CURRENCY, "USD")
    m.apply("1-0", ScheduleNews(event, T0))
    (listed,) = news_view(m)
    assert listed["event_id"] == "us-nfp"
    # two minutes either side, the last millisecond included: [start, end)
    assert (listed["start_ts"], listed["end_ts"]) == (release - 120_000, release + 120_001)
    assert "EUR/USD" in listed["symbols"] and listed["symbols"] == sorted(listed["symbols"])
    m.apply("2-0", PriceUpdate("EUR/USD", 1, release + 120_000, release + 120_000, 110_000))
    assert len(news_view(m)) == 1  # still inside
    m.apply("3-0", PriceUpdate("EUR/USD", 2, release + 120_001, release + 120_001, 110_000))
    assert news_view(m) == []  # over


def test_the_account_view_lists_trailing_orders_with_their_last_move() -> None:
    from propfirm.journal.inputs import PlaceTrailingEntry, SetTrailingStop

    m = machine()
    inputs: list[Input] = [
        OpenAccount("A", "lite-s1-v3", 1_000_000, T0),
        PriceUpdate("EUR/USD", 1, T0, T0, 110_000),
        PlaceOrder(OrderRequest("o1", "A", "EUR/USD", Side.BUY, OrderType.MARKET, 10), T0 + 1),
        SetTrailingStop("t1", "A", "o1", 200, 10, T0 + 2),
        PlaceTrailingEntry("e1", "A", "EUR/USD", Side.SELL, 10, 300, 1, T0 + 3),
        PriceUpdate("EUR/USD", 2, T0 + 4, T0 + 4, 110_100),
    ]
    for n, item in enumerate(inputs, start=1):
        m.apply(f"{n}-0", item)
    view = account_view(m, "A")
    assert view["pending_orders"] == []  # limit and stop orders only
    stop, entry = view["trailing_orders"]
    bid = m.supervisor.engine.quote("EUR/USD").bid  # type: ignore[union-attr]
    assert (stop["order_id"], stop["kind"], stop["position_id"], stop["volume"]) == (
        "t1",
        "stop",
        "o1",
        None,
    )
    assert (stop["level"], stop["moved_at"], stop["moved_seq"], stop["placed_at"]) == (
        bid - 200,
        T0 + 4,
        2,
        T0 + 2,
    )
    assert (entry["kind"], entry["side"], entry["volume"], entry["level"]) == (
        "entry",
        "sell",
        10,
        bid - 300,
    )
    assert (entry["distance"], entry["step"], entry["moved_at"]) == (300, 1, T0 + 4)
