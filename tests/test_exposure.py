"""The exposure desk (D-14.32): exact revaluation, scenarios, limits and
the cut state machine."""

from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest

from propfirm.customers.exposure import (
    BP,
    HALVE,
    NONE,
    QUARTER,
    Book,
    Cut,
    Position,
    Result,
    Scenario,
    bloc_scenarios,
    groups,
    level_of,
    percent_text,
    pnl_usd,
    pressure,
    replay,
    scenario_equity,
    scenario_loss,
    sigma_bp,
    step,
    symbol_scenarios,
)
from propfirm.policy import PolicyError, load_policy, parse_policy
from tests.test_policy import edited

POLICY = load_policy()
SPECS = {i.symbol.name: i for i in POLICY.trading.instruments}
EX = POLICY.exposure
RATES = {"EUR/USD": Fraction(11, 10), "USD/JPY": Fraction(150)}
H = 3_600_000


def buy(symbol: str, lots: int, open_price: int, exit_price: int) -> Position:
    return Position(symbol, 1, lots * 100, open_price, exit_price)


def book(*positions: Position, equity: int = 10_000_000, floor: int = 9_000_000) -> Book:
    # 100K account: profit above 100,000 USD owed at 80 %
    return Book("ACC1", Fraction(4, 5), 10_000_000, equity, floor, positions)


def test_pnl_is_exact_and_converted_like_the_engine() -> None:
    eur = buy("EUR/USD", 1, 110_000, 110_100)  # 100 points of 0.00001 on 100,000
    assert pnl_usd(eur, Fraction(110_100), SPECS, RATES) == 100
    jpy = buy("USD/JPY", 1, 150_000, 150_150)  # 0.150 JPY on 100,000 = 15,000 JPY
    assert pnl_usd(jpy, Fraction(150_150), SPECS, RATES) == 100
    short = Position("EUR/USD", -1, 100, 110_000, 110_100)
    assert pnl_usd(short, Fraction(110_100), SPECS, RATES) == -100


def test_scenario_equity_is_floored_at_the_loss_limit() -> None:
    b = book(buy("EUR/USD", 10, 110_000, 110_000))  # 10 lots, flat
    up = scenario_equity(b, {"EUR/USD": Fraction(1, 100)}, SPECS, RATES)
    assert up == 10_000_000 + 10 * 1_100 * 100  # +1 %: 1,100 USD a lot
    down = scenario_equity(b, {"EUR/USD": Fraction(-1, 10)}, SPECS, RATES)
    assert down == 9_000_000  # would lose 110,000 USD; closed at the floor


def test_scenario_loss_counts_only_profit_owed() -> None:
    b = book(buy("EUR/USD", 10, 110_000, 110_000))
    # +1 %: equity +1,100,000 cents above the threshold; 80 % owed
    assert scenario_loss([b], {"EUR/USD": Fraction(1, 100)}, SPECS, RATES) == 880_000
    # a fall owes nothing more (it was owed nothing)
    assert scenario_loss([b], {"EUR/USD": Fraction(-1, 100)}, SPECS, RATES) == 0
    # already in profit: a fall reduces what is owed (a negative loss)
    rich = replace(b, equity_cents=10_500_000)
    assert scenario_loss([rich], {"EUR/USD": Fraction(-1, 100)}, SPECS, RATES) == -400_000


def test_conversion_moves_with_the_scenario() -> None:
    jpy = book(buy("USD/JPY", 1, 150_000, 150_000))
    # +1 % on USD/JPY: 1,500 JPY a point... 1.5 JPY on 100,000 = 150,000 JPY,
    # at the moved rate 151.5: 990.099... USD
    after = scenario_equity(jpy, {"USD/JPY": Fraction(1, 100)}, SPECS, RATES)
    assert after == 10_000_000 + Fraction(150_000, Fraction(1515, 10)) * 100


def test_sigma_is_exact_and_rounded_up() -> None:
    assert sigma_bp([100]) is None
    assert sigma_bp([100, 101]) is None  # one return
    # returns +1 %, -1 %: mean 0, sample variance 0.0002, sd 141.42... bp
    assert sigma_bp([10_000, 10_100, 9_999]) == 142
    assert sigma_bp([100, 100, 100]) == 0


def test_symbol_scenarios() -> None:
    names = [s.name for s in symbol_scenarios("EUR/USD", 50, 150)]
    assert names == ["EUR/USD +1 sd", "EUR/USD -1 sd", "EUR/USD +3 sd", "EUR/USD -3 sd"]
    assert symbol_scenarios("EUR/USD", 50, 150)[2].moves == {"EUR/USD": 150 * BP}
    short = symbol_scenarios("EUR/USD", None, 150)
    assert [(s.name, s.moves["EUR/USD"]) for s in short] == [
        ("EUR/USD +stress", 150 * BP),
        ("EUR/USD -stress", -150 * BP),
    ]


def test_bloc_scenarios_move_usd_and_gold_together() -> None:
    blocs = {s.name: s for s in bloc_scenarios(SPECS, EX)}
    up = blocs["USD up"].moves
    assert up["EUR/USD"] == -150 * BP and up["USD/JPY"] == 150 * BP
    assert up["XAU/USD"] == -300 * BP
    assert "EUR/GBP" not in up
    assert blocs["USD down"].moves["EUR/USD"] == 150 * BP
    assert set(blocs) >= {"crypto up", "crypto down", "oil up", "oil down"}


def scenario(name: str, kind: str, group: str, moves: dict[str, Fraction]) -> Scenario:
    return Scenario(name, kind, group, moves)


def test_groups_take_the_worst_against_the_limit() -> None:
    up = scenario("EUR/USD +1 sd", "symbol", "EUR/USD", {"EUR/USD": 50 * BP})
    down = scenario("EUR/USD -1 sd", "symbol", "EUR/USD", {"EUR/USD": -50 * BP})
    usd = scenario("USD up", "bloc", "USD", {"EUR/USD": -150 * BP, "USD/JPY": 150 * BP})
    results = [Result(up, 2_000_000), Result(down, -100), Result(usd, 3_000_000)]
    found = groups(results, 10_000_000, EX)  # free reserve 100,000 USD
    (bloc, sym) = found
    assert (sym.name, sym.worst.scenario, sym.limit_cents) == ("EUR/USD", up, 2_500_000)
    assert sym.ratio == Fraction(4, 5)
    assert (bloc.name, bloc.limit_cents, bloc.ratio) == ("USD", 5_000_000, Fraction(3, 5))
    assert groups(results, None, EX)[0].ratio is None
    empty = groups(results, 0, EX)[1]
    assert empty.ratio is not None and empty.ratio > 1
    # the winning side of each moved symbol bears the ratio
    assert pressure(found) == {
        ("EUR/USD", "buy"): Fraction(4, 5),
        ("EUR/USD", "sell"): Fraction(3, 5),
        ("USD/JPY", "buy"): Fraction(3, 5),
    }


def test_levels() -> None:
    assert level_of(None, EX) == NONE
    assert level_of(Fraction(99, 100), EX) == NONE
    assert level_of(Fraction(1), EX) == HALVE
    assert level_of(Fraction(3, 2), EX) == QUARTER


def test_the_cut_state_machine() -> None:
    t = 1_000_000
    cut, event = step(None, Fraction(1), t, EX)
    assert (cut, event) == (Cut(HALVE, t), "raise")
    assert step(cut, Fraction(9, 10), t + 1, EX) == (cut, None)  # stays, above 60 %
    cut2, event = step(cut, Fraction(3, 2), t + 2, EX)
    assert (cut2, event) == (Cut(QUARTER, t + 2), "raise")
    under, event = step(cut2, Fraction(59, 100), t + 3, EX)
    assert (under, event) == (Cut(QUARTER, t + 2, t + 3), "under")
    assert step(under, Fraction(1, 2), t + 3 + 24 * H - 1, EX) == (under, None)
    assert step(under, Fraction(1, 2), t + 3 + 24 * H, EX) == (None, "restore")
    # back over 60 % restarts the 24 hours
    assert step(under, Fraction(3, 5), t + 4, EX) == (Cut(QUARTER, t + 2), "over")
    assert step(None, Fraction(1, 2), t, EX) == (None, None)


def test_replay_rebuilds_the_cuts() -> None:
    moves: list[dict[str, Any]] = [
        {"at": 1, "symbol": "EUR/USD", "side": "buy", "event": "raise", "level": HALVE},
        {"at": 2, "symbol": "EUR/USD", "side": "buy", "event": "under", "level": HALVE},
        {"at": 3, "symbol": "XAU/USD", "side": "sell", "event": "raise", "level": QUARTER},
        {"at": 4, "symbol": "EUR/USD", "side": "buy", "event": "over", "level": HALVE},
        {"at": 5, "symbol": "XAU/USD", "side": "sell", "event": "restore", "level": QUARTER},
    ]
    assert replay(moves) == {("EUR/USD", "buy"): Cut(HALVE, 1)}
    assert replay(moves[:2]) == {("EUR/USD", "buy"): Cut(HALVE, 1, 2)}


def test_percent_text_rounds_down() -> None:
    assert percent_text(Fraction(8049, 10_000)) == "80.4 %"
    assert percent_text(Fraction(3, 2)) == "150.0 %"


def test_the_policy_file_turns_the_desk_on() -> None:
    assert EX.on and (EX.symbol_limit_percent, EX.bloc_limit_percent) == (25, 50)
    assert EX.stress_bp == {"forex": 150, "metal": 300, "oil": 600, "crypto": 1_000}

    def change(data: dict[str, Any]) -> None:
        data["exposure"]["restore_percent"] = 90

    with pytest.raises(PolicyError, match="restore_percent < alert_percent"):
        parse_policy(edited(change))
