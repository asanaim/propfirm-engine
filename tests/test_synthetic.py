import itertools
from collections import defaultdict
from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from propfirm.market.symbols import LAUNCH_SYMBOLS, SYMBOLS
from propfirm.market.synthetic import (
    DEFAULT_START_POINTS,
    Delay,
    Silence,
    SyntheticConfig,
    generate,
)
from propfirm.market.types import PriceUpdate

T0 = 1_790_000_000_000  # a fixed epoch-ms start; any value works
MINUTE = 60_000


def run(config: SyntheticConfig, duration_ms: int = MINUTE) -> list[PriceUpdate]:
    return list(generate(config, T0, T0 + duration_ms))


def by_symbol(updates: list[PriceUpdate]) -> dict[str, list[PriceUpdate]]:
    out: dict[str, list[PriceUpdate]] = defaultdict(list)
    for u in updates:
        out[u.symbol].append(u)
    return out


def test_thirty_launch_symbols_all_have_start_prices() -> None:
    assert len(LAUNCH_SYMBOLS) == 30
    assert set(DEFAULT_START_POINTS) == set(SYMBOLS)


def test_same_seed_same_feed() -> None:
    config = SyntheticConfig(seed="alpha")
    assert run(config) == run(config)


def test_different_seed_different_feed() -> None:
    assert run(SyntheticConfig(seed="alpha")) != run(SyntheticConfig(seed="beta"))


def test_adding_a_symbol_does_not_change_the_others() -> None:
    one = run(SyntheticConfig(seed="s", symbols=("EUR/USD",)))
    two = run(SyntheticConfig(seed="s", symbols=("EUR/USD", "XAU/USD")))
    assert by_symbol(two)["EUR/USD"] == one


@settings(max_examples=25, deadline=None)
@given(seed=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=12))
def test_per_symbol_invariants(seed: str) -> None:
    config = SyntheticConfig(seed=seed, symbols=("EUR/USD", "USD/JPY", "BTC/USD"))
    updates = run(config)
    keys = [(u.receive_ts, u.symbol, u.seq) for u in updates]
    assert keys == sorted(keys)  # arrival order
    for symbol, series in by_symbol(updates).items():
        assert [u.seq for u in series] == list(range(1, len(series) + 1))
        assert all(u.price >= 1 for u in series)
        assert all(u.receive_ts >= u.source_ts for u in series)
        receives = [u.receive_ts for u in series]
        assert receives == sorted(receives), symbol
        sources = [u.source_ts for u in series]
        assert sources == sorted(sources) and len(set(sources)) == len(sources)
        assert all(T0 <= t < T0 + MINUTE for t in sources)


def test_intervals_stay_within_configured_range() -> None:
    config = SyntheticConfig(seed="i", symbols=("EUR/USD",), interval_ms=(100, 200))
    sources = [u.source_ts for u in run(config)]
    gaps = [b - a for a, b in itertools.pairwise(sources)]
    assert gaps and min(gaps) >= 100 and max(gaps) <= 200


def test_silence_produces_no_updates_inside_the_window() -> None:
    silence = Silence("EUR/USD", T0 + 10_000, T0 + 40_000)
    config = SyntheticConfig(seed="g", symbols=("EUR/USD",), silences=(silence,))
    sources = [u.source_ts for u in run(config)]
    assert not [t for t in sources if silence.start_ts <= t < silence.end_ts]
    assert any(t < silence.start_ts for t in sources)
    assert any(t >= silence.end_ts for t in sources)


def test_delay_window_lags_receive_time() -> None:
    delay = Delay("EUR/USD", T0 + 10_000, T0 + 20_000, delay_ms=5_000)
    config = SyntheticConfig(seed="d", symbols=("EUR/USD",), base_delay_ms=(0, 0), delays=(delay,))
    for u in run(config):
        lag = u.receive_ts - u.source_ts
        if delay.start_ts <= u.source_ts < delay.end_ts:
            assert lag >= 5_000
        elif u.source_ts >= delay.end_ts + 5_000:
            assert lag == 0


def test_delay_never_reorders_a_symbol() -> None:
    delay = Delay("EUR/USD", T0 + 10_000, T0 + 20_000, delay_ms=8_000)
    config = SyntheticConfig(seed="d", symbols=("EUR/USD",), delays=(delay,))
    receives = [u.receive_ts for u in run(config)]
    assert receives == sorted(receives)


def test_price_floor_is_one_point() -> None:
    config = SyntheticConfig(
        seed="floor",
        symbols=("XRP/USD",),
        start_points={"XRP/USD": 1},
        jump_per_mille=1000,
    )
    assert min(u.price for u in run(config)) >= 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbols": ("NOPE",)},
        {"symbols": ("EUR/USD", "EUR/USD")},
        {"symbols": ()},
        {"interval_ms": (0, 10)},
        {"interval_ms": (10, 5)},
        {"base_delay_ms": (-1, 5)},
        {"jump_per_mille": 1001},
        {"start_points": {"EUR/USD": 0}, "symbols": ("EUR/USD",)},
    ],
)
def test_invalid_config_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError)):
        SyntheticConfig(seed="x", **kwargs)  # type: ignore[arg-type]


def test_invalid_time_range_rejected() -> None:
    with pytest.raises(ValueError):
        list(generate(SyntheticConfig(seed="x"), T0, T0 - 1))


def test_price_update_rejects_bad_values() -> None:
    good = PriceUpdate("EUR/USD", 1, T0, T0, 110_000)
    for bad in (
        {"seq": 0},
        {"price": 0},
        {"price": 1.5},
        {"seq": True},
        {"source_ts": -1},
        {"symbol": ""},
        {"symbol": " EUR/USD"},
    ):
        with pytest.raises((TypeError, ValueError)):
            replace(good, **bad)
