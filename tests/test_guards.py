"""Acceptance test 5: a symbol freezes when an update is older than the
threshold or overdue, and resumes on the first fresh update."""

import pytest

from propfirm.market.guards import FeedGuards, FreezeReason, GuardChange, GuardConfig
from propfirm.market.types import PriceUpdate

T0 = 1_790_000_000_000
CFG = GuardConfig(delay_threshold_ms=2_000, gap_threshold_ms={"EUR/USD": 10_000})


def update(seq: int, source: int, lag: int = 0, symbol: str = "EUR/USD") -> PriceUpdate:
    return PriceUpdate(symbol, seq, source, source + lag, 110_000 + seq)


def guards() -> FeedGuards:
    return FeedGuards(CFG, ["EUR/USD", "XAU/USD"])


def test_every_symbol_starts_frozen_without_a_price() -> None:
    g = guards()
    assert g.reason("EUR/USD") is FreezeReason.NO_PRICE
    assert g.advance(T0) == []  # already frozen: no duplicate change
    apply, changes = g.on_update(update(1, T0))
    assert apply and changes == [GuardChange("EUR/USD", T0, None)]
    assert g.reason("XAU/USD") is FreezeReason.NO_PRICE


@pytest.mark.parametrize(("lag", "frozen"), [(2_000, False), (2_001, True)])
def test_delay_threshold_boundary(lag: int, frozen: bool) -> None:
    g = guards()
    g.on_update(update(1, T0))
    apply, _ = g.on_update(update(2, T0 + 100, lag))
    assert apply is not frozen
    assert g.is_frozen("EUR/USD") is frozen


def test_delayed_update_freezes_and_is_not_applied_until_a_fresh_one() -> None:
    g = guards()
    g.on_update(update(1, T0))
    apply, changes = g.on_update(update(2, T0 + 100, lag=5_000))
    assert not apply
    assert changes == [GuardChange("EUR/USD", T0 + 5_100, FreezeReason.DELAYED)]
    apply, changes = g.on_update(update(3, T0 + 200, lag=4_950))
    assert not apply and changes == []  # still delayed: no repeated change
    apply, changes = g.on_update(update(4, T0 + 5_200, lag=10))
    assert apply and changes == [GuardChange("EUR/USD", T0 + 5_210, None)]


@pytest.mark.parametrize(("elapsed", "frozen"), [(10_000, False), (10_001, True)])
def test_gap_threshold_boundary(elapsed: int, frozen: bool) -> None:
    g = guards()
    g.on_update(update(1, T0))
    changes = g.advance(T0 + elapsed)
    assert g.is_frozen("EUR/USD") is frozen
    assert changes == (
        [GuardChange("EUR/USD", T0 + elapsed, FreezeReason.OVERDUE)] if frozen else []
    )


def test_overdue_symbol_resumes_on_fresh_update() -> None:
    g = guards()
    g.on_update(update(1, T0))
    g.advance(T0 + 20_000)
    apply, changes = g.on_update(update(2, T0 + 20_500))
    assert apply and changes == [GuardChange("EUR/USD", T0 + 20_500, None)]


def test_delayed_symbol_resumes_on_a_later_fresh_update() -> None:
    g = guards()
    g.on_update(update(1, T0))
    g.on_update(update(2, T0 + 1_000, lag=9_000))  # arrives at T0+10_000, stale
    assert g.reason("EUR/USD") is FreezeReason.DELAYED
    apply, _ = g.on_update(update(3, T0 + 30_000))
    assert apply and not g.is_frozen("EUR/USD")


def test_default_gap_threshold_applies_to_unlisted_symbols() -> None:
    g = guards()
    g.on_update(update(1, T0, symbol="XAU/USD"))
    assert g.advance(T0 + 60_000) == []
    assert g.advance(T0 + 60_001) == [GuardChange("XAU/USD", T0 + 60_001, FreezeReason.OVERDUE)]


def test_outage_freezes_all_and_each_symbol_resumes_on_its_own_update() -> None:
    g = guards()
    g.on_update(update(1, T0))
    g.on_update(update(1, T0, symbol="XAU/USD"))
    changes = g.on_outage(T0 + 500)
    assert {c.symbol for c in changes} == {"EUR/USD", "XAU/USD"}
    assert all(c.reason is FreezeReason.OUTAGE for c in changes)
    assert g.on_outage(T0 + 600) == []
    g.on_update(update(2, T0 + 1_000))
    assert not g.is_frozen("EUR/USD")
    assert g.reason("XAU/USD") is FreezeReason.OUTAGE


def test_unknown_symbol_rejected() -> None:
    with pytest.raises(KeyError):
        guards().on_update(update(1, T0, symbol="BTC/USD"))


def test_invalid_config_rejected() -> None:
    with pytest.raises(ValueError):
        GuardConfig(default_gap_threshold_ms=0)
    with pytest.raises(TypeError):
        GuardConfig(delay_threshold_ms=1.5)  # type: ignore[arg-type]
