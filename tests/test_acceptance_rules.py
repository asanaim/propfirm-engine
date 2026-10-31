"""Acceptance test 3 (Plan v4): rule edge cases.

- gap through a limit
- spread spike breaches on equity at bid/ask
- daylight-saving change at the 17:00 New York reset
- daily and max limits breached on one update
- news window on each affected symbol

Lite 100K: daily floor = day base - $3,000; max floor = $94,000.
EUR/USD 10 lots (volume 1,000) = $10 per point; spread 10 points.
"""

from datetime import UTC, datetime

import pytest

from propfirm.market.spread import NewsWindow
from propfirm.market.symbols import LAUNCH_SYMBOLS
from propfirm.rules.clock import NEW_YORK_17
from propfirm.rules.events import (
    AccountBreached,
    AccountStatus,
    BreachReason,
    DayStarted,
    NewsViolation,
)
from propfirm.rules.news import CURRENT_MAP, EventScope, RestrictedEvent, affected_symbols
from propfirm.rules.products import PRODUCTS, RuleSet
from propfirm.trading.events import (
    CloseReason,
    OrderCancelled,
    OrderPlaced,
    PositionClosed,
    PositionOpened,
    RejectReason,
)
from propfirm.trading.orders import OrderType, Side
from tests.engine_rig import USD_100K, one, rejected
from tests.rules_rig import RulesRig, ny

LITE_S1 = PRODUCTS["lite"][0]
DAY = ny(2026, 10, 28, 10)  # Wednesday 10:00 New York


def utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp()) * 1000


# --- gap through a limit; daily and max on one update ------------------------------


def test_gap_through_the_daily_floor_breaches_and_closes_at_the_actual_price() -> None:
    rig = RulesRig(LITE_S1, USD_100K, DAY)
    rig.price(110_000, DAY)
    rig.order(Side.BUY, 1_000, DAY)  # 10 lots at 1.10005
    rig.order(Side.BUY, 100, DAY, order_type=OrderType.LIMIT, level=100_000)  # pending
    assert rig.sup.floors("A") == (9_700_000, 9_400_000)

    events = rig.price(109_600, DAY + 1_000)  # bid 1.09595: -410 points, -$4,100
    breach = one(events, AccountBreached)
    assert breach.reasons == (BreachReason.DAILY_LOSS,)
    assert breach.equity_cents == 9_590_000  # below the $97,000 floor: not capped
    assert breach.rsv_id == "lite-s1-v2"
    closed = one(events, PositionClosed)
    assert (closed.reason, closed.close_price) == (CloseReason.BREACH, 109_595)
    assert one(events, OrderCancelled).reason is RejectReason.ACCOUNT_LOCKED
    kinds = [type(e) for e in events]
    assert kinds.index(AccountBreached) < kinds.index(PositionClosed)
    assert rig.engine.balance("A") == 9_590_000
    assert rig.sup.status("A") is AccountStatus.BREACHED
    assert rejected(rig.order(Side.BUY, 100, DAY + 2_000)) is RejectReason.ACCOUNT_LOCKED
    rig.ledger.verify()


def test_daily_and_max_breached_on_one_update_give_one_breach_with_both_reasons() -> None:
    rig = RulesRig(LITE_S1, USD_100K, DAY)
    rig.price(110_000, DAY)
    rig.order(Side.BUY, 1_000, DAY)
    events = rig.price(109_000, DAY + 1_000)  # bid 1.08995: -$10,100
    breach = one(events, AccountBreached)
    assert breach.reasons == (BreachReason.DAILY_LOSS, BreachReason.MAX_LOSS)
    assert (breach.daily_floor_cents, breach.max_floor_cents) == (9_700_000, 9_400_000)
    assert breach.equity_cents == 8_990_000


def test_equity_at_floor_boundary() -> None:
    rig = RulesRig(LITE_S1, USD_100K, DAY)
    rig.price(110_000, DAY)
    rig.order(Side.BUY, 1_000, DAY)
    above = rig.price(109_711, DAY + 1)  # bid 1.09706: -$2,990
    assert not [e for e in above if isinstance(e, AccountBreached)]
    at = rig.price(109_710, DAY + 2)  # bid 1.09705: -$3,000 -> exactly $97,000
    assert one(at, AccountBreached).equity_cents == 9_700_000


# --- spread spike -------------------------------------------------------------------


def test_spread_spike_alone_breaches_because_equity_is_at_bid_and_ask() -> None:
    spike = NewsWindow(frozenset({"EUR/USD"}), DAY + 10_000, DAY + 20_000)
    rig = RulesRig(LITE_S1, USD_100K, DAY, spread_windows=[spike])
    rig.price(110_000, DAY)
    rig.order(Side.BUY, 1_000, DAY)
    calm = rig.price(109_715, DAY + 5_000)  # bid 1.09710: -$2,950
    assert not [e for e in calm if isinstance(e, AccountBreached)]
    # Same feed price, spread 10 -> 30 points: bid 1.09700, -$3,050
    spiked = rig.price(109_715, DAY + 15_000)
    assert one(spiked, AccountBreached).reasons == (BreachReason.DAILY_LOSS,)


# --- daylight saving at the 17:00 New York reset -------------------------------------


def test_reset_instants_follow_new_york_across_the_november_change() -> None:
    c = NEW_YORK_17
    boundaries = [utc(2026, 10, 30, 21)]
    while len(boundaries) < 4:
        boundaries.append(c.next_boundary(boundaries[-1]))
    assert boundaries == [
        utc(2026, 10, 30, 21),  # Fri 17:00 EDT
        utc(2026, 10, 31, 21),  # Sat 17:00 EDT
        utc(2026, 11, 1, 22),  # Sun 17:00 EST: clocks went back at 02:00
        utc(2026, 11, 2, 22),  # Mon 17:00 EST
    ]
    sunday = utc(2026, 11, 1, 22)
    assert c.day_start(sunday - 1) == utc(2026, 10, 31, 21)
    assert c.day_start(sunday) == sunday
    assert c.day_start(utc(2026, 11, 1, 21, 30)) == utc(2026, 10, 31, 21)


def test_reset_instants_follow_new_york_across_the_march_change() -> None:
    c = NEW_YORK_17
    assert c.next_boundary(utc(2026, 3, 7, 22)) == utc(2026, 3, 8, 21)  # EST -> EDT
    assert c.day_start(utc(2026, 3, 8, 21) - 1) == utc(2026, 3, 7, 22)


def test_daily_base_resets_at_22_utc_after_the_change_not_21() -> None:
    start = utc(2026, 11, 1, 20)  # Sunday 15:00 EST
    rig = RulesRig(LITE_S1, USD_100K, start)
    rig.price(110_000, start)
    rig.order(Side.BUY, 1_000, start)
    rig.price(110_105, start + 1_000)  # bid 1.10100: +$950 floating
    at_21 = rig.sup.advance(utc(2026, 11, 1, 21, 30))
    assert not [e for e in at_21 if isinstance(e, DayStarted)]
    at_22 = rig.sup.advance(utc(2026, 11, 1, 22))
    started = one(at_22, DayStarted)
    assert (started.ts, started.base_cents) == (utc(2026, 11, 1, 22), 10_095_000)
    assert started.daily_floor_cents == 9_795_000  # base - $3,000


def test_floating_loss_carried_across_the_reset_breaches_at_the_reset() -> None:
    day = ny(2026, 10, 28, 16, 50)
    rig = RulesRig(LITE_S1, USD_100K, day)
    rig.price(110_000, day)
    rig.order(Side.BUY, 2_000, day)  # 20 lots at 1.10005
    rig.price(110_305, day + 1_000)  # bid 1.10300
    position = rig.engine.positions("A")[0].position_id
    rig.sup.close_position("A", position, day + 1_000, volume=1_000)  # +$2,950 closed
    rig.price(109_700, day + 2_000)  # bid 1.09695: -$3,100 floating, equity $99,850
    assert rig.sup.status("A") is AccountStatus.ACTIVE  # above today's $97,000 floor

    reset = ny(2026, 10, 28, 17)
    events = rig.sup.advance(reset + 5_000)
    started = one(events, DayStarted)
    assert started.base_cents == 10_295_000  # balance, the higher of the two
    breach = one(events, AccountBreached)
    assert (breach.ts, breach.reasons) == (reset, (BreachReason.DAILY_LOSS,))
    assert breach.equity_cents == 9_985_000 and breach.daily_floor_cents == 9_995_000


# --- news windows on each affected symbol --------------------------------------------


USD_PAIRS = {"EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"}


def test_the_original_map_still_blocks_silver_and_oil_on_usd_events() -> None:
    """Map "v1": events recorded before the FTMO map replay as they were."""
    event = RestrictedEvent("e", DAY, EventScope.CURRENCY, "USD")
    assert event.instrument_map == "v1"
    assert affected_symbols(event, LAUNCH_SYMBOLS) == USD_PAIRS | {
        "XAU/USD",
        "XAG/USD",
        "WTI",
        "Brent",
    }


@pytest.mark.parametrize(
    ("scope", "currency", "expected"),
    [
        (EventScope.CURRENCY, "USD", USD_PAIRS | {"XAU/USD"}),  # FTMO: gold, not silver or oil
        (
            EventScope.CURRENCY,
            "EUR",
            {"EUR/USD", "EUR/GBP", "EUR/JPY", "EUR/CHF", "EUR/AUD", "EUR/CAD", "EUR/NZD"},
        ),
        (
            EventScope.CURRENCY,
            "GBP",
            {"GBP/USD", "EUR/GBP", "GBP/JPY", "GBP/CHF", "GBP/AUD", "GBP/CAD"},
        ),
        (
            EventScope.CURRENCY,
            "JPY",
            {"USD/JPY", "EUR/JPY", "GBP/JPY", "AUD/JPY", "CAD/JPY", "CHF/JPY"},
        ),
        (
            EventScope.CURRENCY,
            "CAD",
            {s.name for s in LAUNCH_SYMBOLS if "CAD" in s.name.split("/")},
        ),
        (EventScope.OIL_INVENTORY, None, {"WTI", "Brent"}),
    ],
)
def test_affected_symbols_per_event(
    scope: EventScope, currency: str | None, expected: set[str]
) -> None:
    event = RestrictedEvent("e", DAY, scope, currency, instrument_map=CURRENT_MAP)
    assert affected_symbols(event, LAUNCH_SYMBOLS) == expected
    assert not {"BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "LTC/USD"} & expected


NFP = ny(2026, 11, 6, 8, 30)


PRICES = {
    "EUR/USD": 110_000,
    "EUR/GBP": 85_000,
    "GBP/USD": 130_000,
    "BTC/USD": 10_000_000,
    "XAU/USD": 330_000,
}


LITE_FUNDED = PRODUCTS["lite"][2]  # news windows bind funded accounts only (as FTMO)


def nfp_rig(at: int, rules: RuleSet = LITE_FUNDED) -> RulesRig:
    """US jobs report at NFP; every test symbol freshly priced at `at`."""
    event = RestrictedEvent(
        "nfp-2026-11", NFP, EventScope.CURRENCY, "USD", instrument_map=CURRENT_MAP
    )
    rig = RulesRig(rules, USD_100K, at, news=[event])
    for symbol, price in PRICES.items():
        rig.price(price, at, symbol)
    return rig


@pytest.mark.parametrize(
    ("offset", "blocked"),
    [
        (-120_001, False),
        (-120_000, True),
        (0, True),
        (120_000, True),
        (120_001, False),
    ],
)
def test_usd_event_window_edges(offset: int, blocked: bool) -> None:
    at = NFP + offset
    rig = nfp_rig(at)
    events = rig.order(Side.BUY, 100, at)
    if blocked:
        assert rejected(events) is RejectReason.NEWS_WINDOW
    else:
        one(events, PositionOpened)


@pytest.mark.parametrize(
    ("symbol", "blocked"),
    [("EUR/USD", True), ("XAU/USD", True), ("EUR/GBP", False), ("BTC/USD", False)],
)
def test_usd_event_blocks_only_affected_symbols(symbol: str, blocked: bool) -> None:
    rig = nfp_rig(NFP)
    events = rig.order(Side.BUY, 10, NFP, symbol=symbol)
    if blocked:
        assert rejected(events) is RejectReason.NEWS_WINDOW
    else:
        one(events, PositionOpened)


def test_in_window_no_close_no_pending_fill_but_stops_execute() -> None:
    before = NFP - 300_000
    rig = nfp_rig(before)
    rig.order(Side.BUY, 100, before, sl=109_800)  # o1: stop-loss 1.09800
    rig.order(Side.BUY, 100, before)  # o2
    one(rig.order(Side.BUY, 100, before, order_type=OrderType.LIMIT, level=109_900), OrderPlaced)

    inside = NFP + 10_000
    events = rig.price(109_890, inside)  # ask 1.09895 crosses the limit: held
    assert not [e for e in events if isinstance(e, PositionOpened)]
    assert rejected(rig.sup.close_position("A", "o2", inside)) is RejectReason.NEWS_WINDOW
    events = rig.price(109_700, inside + 1_000)  # bid 1.09695 crosses the stop-loss
    closed = one(events, PositionClosed)
    assert closed.reason is CloseReason.STOP_LOSS
    violation = one(events, NewsViolation)  # it executes, and is a violation (as FTMO)
    assert (violation.position_id, violation.reason, violation.event_ids) == (
        "o1",
        "stop_loss",
        ("setup",),
    )
    assert (violation.pnl_cents, violation.rsv_id) == (closed.pnl_cents, "lite-funded-v1")

    after = NFP + 120_001
    events = rig.price(109_700, after)  # first price after the window: limit fills
    assert one(events, PositionOpened).price == 109_705
    one(rig.sup.close_position("A", "o2", after), PositionClosed)


def test_bank_of_japan_window_opens_at_the_marked_publication() -> None:
    published = ny(2026, 10, 29, 23, 40)
    boj = RestrictedEvent("boj", published, EventScope.CURRENCY, "JPY", before_ms=0)
    rig = RulesRig(LITE_FUNDED, USD_100K, published - 60_000, news=[boj])
    rig.price(150_000, published - 1, "USD/JPY")
    one(rig.order(Side.BUY, 10, published - 1, symbol="USD/JPY"), PositionOpened)
    rig.price(150_000, published, "USD/JPY")
    assert rejected(rig.order(Side.BUY, 10, published, symbol="USD/JPY")) is (
        RejectReason.NEWS_WINDOW
    )
    rig.price(150_000, published + 120_001, "USD/JPY")
    one(rig.order(Side.BUY, 10, published + 120_001, symbol="USD/JPY"), PositionOpened)


def test_evaluation_accounts_are_not_bound_by_news_windows() -> None:
    """As FTMO: Challenge and Verification are exempt [decided 31 Oct 2026]."""
    assert not LITE_S1.news_restricted and LITE_FUNDED.news_restricted
    rig = nfp_rig(NFP - 300_000, LITE_S1)
    rig.order(Side.BUY, 100, NFP - 300_000, sl=109_800)
    rig.price(110_000, NFP)
    one(rig.order(Side.BUY, 10, NFP), PositionOpened)  # inside the window: allowed
    one(rig.sup.close_position("A", "o2", NFP), PositionClosed)
    events = rig.price(109_700, NFP + 1_000)
    assert one(events, PositionClosed).reason is CloseReason.STOP_LOSS
    assert not [e for e in events if isinstance(e, NewsViolation)]


def test_a_breach_close_out_inside_a_window_is_not_a_violation() -> None:
    rig = nfp_rig(NFP - 300_000)
    rig.order(Side.BUY, 1_000, NFP - 300_000)  # 10 lots: $10 a point
    events = rig.price(109_600, NFP)  # bid 1.09595: -$4,100, past the $3,000 daily limit
    assert one(events, PositionClosed).reason is CloseReason.BREACH
    assert not [e for e in events if isinstance(e, NewsViolation)]
