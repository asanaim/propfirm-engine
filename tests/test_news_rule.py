"""The news rule as FTMO (build plan step 1, line 6; D-14.2).

On funded accounts whose rule set has `news_breaches`: when a restricted
window opens on a symbol, the account's pending and trailing entries on it
are cancelled (`news_window`); new orders and closes are refused there, as
before; a fill inside the window (stop loss, take profit, trailing stop)
breaches the account (BreachReason.NEWS), which closes its other positions
and locks it. Unaffected symbols, evaluations and older rule sets keep the
earlier behaviour (entries wait, a fill raises an integrity case).
"""

from typing import Any

from propfirm.rules.events import AccountBreached, AccountStatus, BreachReason, NewsViolation
from propfirm.rules.news import CURRENT_MAP, EventScope, RestrictedEvent
from propfirm.rules.products import PRODUCTS, RULESETS
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

NFP = ny(2026, 11, 6, 8, 30)
BEFORE = NFP - 300_000
INSIDE = NFP - 60_000  # the window runs from 2 minutes before to 2 minutes after
AFTER = NFP + 120_001
FUNDED = PRODUCTS["lite"][-1]  # lite-funded-v4: news rule as FTMO
OLDER = RULESETS["lite-funded-v3"]  # entries wait, a fill raises a case
EVALUATION = PRODUCTS["lite"][0]


def rig(rules: Any = FUNDED) -> RulesRig:
    event = RestrictedEvent("nfp", NFP, EventScope.CURRENCY, "USD", instrument_map=CURRENT_MAP)
    r = RulesRig(rules, USD_100K, BEFORE, news=[event])
    r.price(110_000, BEFORE)
    r.price(85_000, BEFORE, "EUR/GBP")
    r.price(130_000, BEFORE, "GBP/USD")  # the conversion rate of EUR/GBP
    return r


def of(kind: type, events: list[Any]) -> list[Any]:
    return [e for e in events if isinstance(e, kind)]


def test_the_shipped_funded_stages_follow_ftmo() -> None:
    assert FUNDED.rsv_id == "lite-funded-v4" and FUNDED.news_breaches
    assert not EVALUATION.news_breaches and not EVALUATION.news_restricted
    assert not OLDER.news_breaches
    funded = [r for stages in PRODUCTS.values() for r in stages if r.news_restricted]
    assert funded and all(r.news_breaches for r in funded)


def test_entries_on_affected_symbols_are_cancelled_when_the_window_opens() -> None:
    r = rig()
    one(r.order(Side.BUY, 100, BEFORE, order_type=OrderType.LIMIT, level=109_000), OrderPlaced)
    one(
        r.order(Side.BUY, 100, BEFORE, "EUR/GBP", order_type=OrderType.LIMIT, level=84_000),
        OrderPlaced,
    )
    r.sup.place_trailing_entry("t1", "A", "EUR/USD", Side.BUY, 100, 50, 1, BEFORE)
    events = r.sup.advance(INSIDE)
    cancelled = of(OrderCancelled, events)
    assert [(c.order_id, c.reason, c.ts) for c in cancelled] == [
        ("o1", RejectReason.NEWS_WINDOW, INSIDE),
        ("t1", RejectReason.NEWS_WINDOW, INSIDE),
    ]
    assert [o.request.order_id for o in r.engine.pending_orders("A")] == ["o2"]  # EUR/GBP


def test_a_stop_inside_the_window_breaches_the_account_and_closes_the_rest() -> None:
    r = rig()
    one(r.order(Side.BUY, 100, BEFORE, sl=109_800), PositionOpened)  # o1, stop 1.09800
    one(r.order(Side.BUY, 10, BEFORE, "EUR/GBP"), PositionOpened)  # o2, unaffected symbol
    r.price(85_000, INSIDE, "EUR/GBP")
    r.price(130_000, INSIDE, "GBP/USD")
    events = r.price(109_700, INSIDE)  # bid 1.09695 crosses the stop
    stop, *rest = of(PositionClosed, events)
    assert (stop.position_id, stop.reason) == ("o1", CloseReason.STOP_LOSS)
    one(events, NewsViolation)
    breached = one(events, AccountBreached)
    assert breached.reasons == (BreachReason.NEWS,) and breached.rsv_id == "lite-funded-v4"
    assert [(c.position_id, c.reason) for c in rest] == [("o2", CloseReason.BREACH)]
    assert r.sup.status("A") is AccountStatus.BREACHED
    assert rejected(r.order(Side.BUY, 10, AFTER, "EUR/GBP")) is RejectReason.ACCOUNT_LOCKED
    order = [type(e).__name__ for e in events if type(e).__name__ != "PriceApplied"]
    assert order.index("NewsViolation") < order.index("AccountBreached")


def test_an_older_funded_version_keeps_waiting_entries_and_a_case_only() -> None:
    r = rig(OLDER)
    one(r.order(Side.BUY, 100, BEFORE, sl=109_800), PositionOpened)
    one(r.order(Side.BUY, 100, BEFORE, order_type=OrderType.LIMIT, level=109_900), OrderPlaced)
    events = r.sup.advance(INSIDE)
    assert not of(OrderCancelled, events)
    events = r.price(109_700, INSIDE)
    one(events, NewsViolation)
    assert not of(AccountBreached, events)
    assert r.sup.status("A") is AccountStatus.ACTIVE
    opened = of(PositionOpened, r.price(109_700, AFTER))  # the limit fills after the window
    assert [p.position_id for p in opened] == ["o2"]


def test_an_evaluation_is_not_bound() -> None:
    r = rig(EVALUATION)
    one(r.order(Side.BUY, 100, BEFORE, order_type=OrderType.LIMIT, level=109_000), OrderPlaced)
    assert not of(OrderCancelled, r.sup.advance(INSIDE))
    r.price(110_000, INSIDE)
    one(r.order(Side.BUY, 100, INSIDE), PositionOpened)


def test_a_window_skipped_by_one_long_advance_cancels_nothing() -> None:
    """Nothing can fill inside a window the engine never stands in, so
    entries survive a step over it."""
    r = rig()
    one(r.order(Side.BUY, 100, BEFORE, order_type=OrderType.LIMIT, level=109_000), OrderPlaced)
    assert not of(OrderCancelled, r.sup.advance(AFTER))
    assert len(r.engine.pending_orders("A")) == 1
