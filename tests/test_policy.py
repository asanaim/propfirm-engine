"""The policy file: shipped values, strict reading, country gating, changes
applied as journaled inputs, and engine trading settings changed by input."""

import copy
import tomllib
from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest

from propfirm.access.gating import check_country
from propfirm.journal.inputs import (
    AddRuleSet,
    InputRejected,
    InputRejectReason,
    OpenAccount,
    PlaceOrder,
    UpdateTrading,
)
from propfirm.journal.machine import CODEC, Machine
from propfirm.market.guards import GuardConfig
from propfirm.market.spread import MarketSpreadConfig
from propfirm.market.types import PriceUpdate
from propfirm.money import cents_ceil
from propfirm.policy import (
    DEFAULT_POLICY,
    AccessPolicy,
    PolicyError,
    load_policy,
    parse_policy,
    percent_text,
)
from propfirm.rules.products import PRODUCTS
from propfirm.service.setup import policy_changes, setup_from_policy
from propfirm.trading.events import OrderRejected, PositionOpened, TradingConfigChanged
from propfirm.trading.instruments import SpreadAndCommission, default_instruments
from propfirm.trading.orders import OrderRequest, OrderType, Side

RAW: dict[str, Any] = tomllib.loads(DEFAULT_POLICY.read_text("utf-8"))
SHIPPED = load_policy()
SIZES_V1 = (1_000_000, 2_500_000, 5_000_000, 10_000_000, 20_000_000)
T0 = 1_790_000_000_000


def edited(change: Callable[[dict[str, Any]], object]) -> dict[str, Any]:
    data = copy.deepcopy(RAW)
    change(data)
    return data


def lite(data: dict[str, Any]) -> dict[str, Any]:
    product: dict[str, Any] = data["products"][0]
    return product


# --- shipped values -------------------------------------------------------------------


def test_shipped_policy_is_the_confirmed_configuration() -> None:
    assert SHIPPED.rule_sets() == tuple(r for stages in PRODUCTS.values() for r in stages)
    defaults = tuple(default_instruments().values())
    assert SHIPPED.trading.config_version == "trading-v8"  # v8: market break rule
    shipped = SHIPPED.trading.instruments
    assert tuple(
        replace(s, spread=d.spread, swap=None, hours=None)
        for s, d in zip(shipped, defaults, strict=True)
    ) == (defaults)  # only the spread, swap and hours differ from the engine defaults
    normal = {s.name: s.spread_model.min_points for s in shipped}
    assert (normal["EUR/USD"], normal["GBP/USD"], normal["USD/JPY"]) == (8, 11, 14)  # pips x 10
    assert (normal["XAU/USD"], normal["WTI"], normal["BTC/USD"]) == (47, 6, 1_089)  # cents
    commission = {s.name: s.commission for s in shipped}
    crypto = {"BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "LTC/USD"}
    assert {n for n, c in commission.items() if c} == crypto
    assert commission["BTC/USD"] == Fraction(525, 1_000_000)  # 0.0525 %
    btc = next(s.spread for s in shipped if s.name == "BTC/USD")
    assert isinstance(btc, SpreadAndCommission) and btc.spread.max_points == 10_890
    eurusd = next(s.spread for s in shipped if s.name == "EUR/USD")
    assert eurusd == MarketSpreadConfig(
        min_points=8,
        max_points=80,
        rollover_multiplier=3,
        rollover_before_ms=300_000,
        rollover_after_ms=900_000,
    )
    assert SHIPPED.trading.guard == GuardConfig(
        default_quote_age_ms=3_000, sanity_multiple=8, sanity_min_bp=50, sanity_min_changes=5
    )  # review D2 and D9 (12 Nov 2026); delay and gap thresholds as before
    assert SHIPPED.access == AccessPolicy(False, frozenset({"BD", "IN"}))  # gating off
    prices = {p.id: dict(p.prices_cents) for p in SHIPPED.products}
    # product line version 1 (13 Nov 2026, D-13.17): no 5K Lite or 1-Step
    assert prices["lite"] == dict(
        zip(SIZES_V1, (5_500, 10_900, 21_500, 38_900, 77_900), strict=True)
    )
    assert prices["one-step"] == dict(
        zip(SIZES_V1, (9_500, 19_900, 29_900, 49_900, 99_900), strict=True)
    )
    assert prices["instant"] == {500_000: 10_900, 1_000_000: 19_900, 2_500_000: 47_900}
    assert prices["classic"][2_500_000] == 11_900
    assert 500_000 not in prices["classic"]  # no Classic 5K
    assert SHIPPED.product("one-step").fee_refund_with_payout == 3
    assert SHIPPED.product("instant").code_discount_max == 0
    assert SHIPPED.payouts.minimum_cents == 5_000 and SHIPPED.payouts.approvals_required == 2
    assert SHIPPED.scaling.max_allocation_cents == 40_000_000
    assert SHIPPED.news.before_ms == SHIPPED.news.after_ms == 120_000
    assert SHIPPED.payouts.approval == "pass_authorise"  # decided 31 Oct 2026
    staff = SHIPPED.staff
    assert (staff.approvals_need_mfa, staff.step_up_minutes) == (True, 5)
    assert (staff.login_needs_mfa, staff.staff_may_trade) == (True, False)  # 6 Nov 2026
    assert (staff.limits.apprentice_min_months, staff.limits.apprentice_max_months) == (3, 6)
    assert (staff.limits.power_max_days, staff.limits.acting_max_days) == (90, 30)


def test_numbers_are_editable() -> None:
    def change(data: dict[str, Any]) -> None:
        stage = lite(data)["stages"][0]
        stage.update(version=4, profit_target_percent="8", max_loss_percent="5.5")
        stage.update(daily_loss_percent="2.5", min_trading_days=3)
        lite(data)["prices_cents"][0] = 4_900
        data["trading"]["leverage"]["forex"] = "50"
        data["access"]["country_gating"] = True

    policy = parse_policy(edited(change))
    s1 = policy.product("lite").rule_sets[0]
    assert s1.rsv_id == "lite-s1-v4"
    assert (s1.profit_target, s1.max_loss, s1.daily_loss, s1.min_trading_days) == (
        Fraction(8, 100),
        Fraction(55, 1000),
        Fraction(25, 1000),
        3,
    )
    assert dict(policy.product("lite").prices_cents)[1_000_000] == 4_900
    eur = next(i for i in policy.trading.instruments if i.name == "EUR/USD")
    assert eur.leverage == 50
    assert policy.access.country_gating


def _set(path: list[Any], value: object) -> Callable[[dict[str, Any]], None]:
    def change(data: dict[str, Any]) -> None:
        node: Any = data
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return change


def _drop(path: list[Any]) -> Callable[[dict[str, Any]], None]:
    def change(data: dict[str, Any]) -> None:
        node: Any = data
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return change


@pytest.mark.parametrize(
    "change",
    [
        _set(["products", 0, "stages", 0, "max_loss_percent"], 6.0),  # float
        _set(["products", 0, "stages", 0, "max_loss_percent"], "6%"),
        _set(["products", 0, "stages", 0, "max_loss_percent"], "100"),  # not below 100%
        _set(["products", 0, "stages", 0, "profit_target_percent"], ""),  # s1 needs a target
        _set(["products", 0, "stages", 2, "profit_target_percent"], "5"),  # funded has none
        _set(["products", 0, "stages", 0, "min_trading_days"], True),
        _set(["products", 0, "stages", 0, "stage"], "s3"),
        _set(["products", 0, "stages", 0, "maxloss_percent"], "6"),  # unknown key
        _drop(["products", 0, "stages", 0, "daily_loss_percent"]),
        _set(["products", 0, "stages", 0, "daily_loss_percent"], "7"),  # above the 6 % drawdown
        _set(["products", 0, "prices_cents"], [2400, 4400]),  # one price per size
        _set(
            ["products", 0, "sizes_cents"], [1000000, 500000, 2500000, 5000000, 10000000, 20000000]
        ),
        _set(["products", 0, "stages"], []),
        _set(["products", 1, "id"], "lite"),  # duplicate product
        _set(["products", 0, "profit_split_percent"], "120"),
        _set(["access", "country_gating"], "yes"),
        _set(["access", "blocked_countries"], ["Bangladesh"]),
        _set(["trading", "leverage", "forex"], 100),  # number, not text
        _drop(["trading", "spread", "min_points", "EUR/USD"]),
        _drop(["trading", "spread", "rollover_after_minutes"]),
        _set(["trading", "spread", "rollover_multiplier"], 0),
        _set(["trading", "spread", "rollover_before_minutes"], 1_440),  # window a day or more
        _drop(["trading", "commission", "crypto"]),
        _set(["trading", "commission", "crypto"], 0.05),  # number, not text
        _set(["trading", "commission", "crypto"], "1"),  # 1 % or more
        _set(["trading", "gap_threshold_ms_by_symbol"], {"EURUSD": 5000}),
        _set(["payouts", "interval_days"], 0),
        _drop(["payouts", "interval_days"]),
        _set(["payouts", "approval"], "anyone"),
        _set(["payouts", "approvals_required"], 3),  # pass_authorise is two people
        _drop(["staff"]),
        _set(["staff", "apprentice_min_months"], 7),  # above the maximum
        _set(["staff", "power_max_days"], 0),
        _set(["staff", "approvals_need_mfa"], 1),
        _drop(["staff", "login_needs_mfa"]),
        _set(["staff", "staff_may_trade"], "no"),
        _set(["unexpected"], 1),
    ],
)
def test_invalid_policies_are_refused(change: Callable[[dict[str, Any]], None]) -> None:
    with pytest.raises(PolicyError):
        parse_policy(edited(change))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("retake_discount_percent", "100"),
        ("retake_discount_percent", "-1"),
        ("code_free_sizes_cents", [123]),  # not a size of the product
    ],
)
def test_bad_retake_and_code_free_sizes_are_refused(key: str, value: object) -> None:
    def change(data: dict[str, Any]) -> None:
        lite(data)[key] = value

    with pytest.raises(PolicyError):
        parse_policy(edited(change))


def test_reversed_stage_order_is_refused() -> None:
    def change(data: dict[str, Any]) -> None:
        lite(data)["stages"].reverse()

    with pytest.raises(PolicyError):
        parse_policy(edited(change))


def test_percent_text_is_exact() -> None:
    assert [percent_text(Fraction(n, d)) for n, d in ((6, 100), (7, 200), (1, 2), (333, 100))] == [
        "6",
        "3.5",
        "50",
        "333",
    ]
    assert percent_text(None) is None
    assert percent_text(Fraction(1, 300)) == "1/3"  # not a finite decimal: kept exact


# --- country gating -------------------------------------------------------------------


def test_country_gating_switch() -> None:
    off = AccessPolicy(False, frozenset({"BD", "IN"}))
    on = AccessPolicy(True, frozenset({"BD", "IN"}))
    assert check_country(off, document="BD", ip="IN").allowed
    decision = check_country(on, document="DE", ip="BD", device="IN")
    assert not decision.allowed and decision.reasons == ("ip:BD", "device:IN")
    assert check_country(on, document="DE", ip="FR").allowed
    assert check_country(on).allowed  # no signal yet: verification decides later
    with pytest.raises(ValueError):
        check_country(on, document="bd")


# --- policy changes as inputs ---------------------------------------------------------


def machine(policy: Any = SHIPPED) -> Machine:
    m = Machine(CODEC.encode(setup_from_policy(policy)), {1: bytes(32)})
    m.use_price_key(1)
    return m


def test_no_changes_for_the_policy_a_journal_started_with() -> None:
    assert policy_changes(SHIPPED, machine(), T0) == []


def test_a_new_rule_set_version_is_published() -> None:
    def change(data: dict[str, Any]) -> None:
        lite(data)["stages"][0].update(version=4, daily_loss_percent="4")

    policy = parse_policy(edited(change))
    items = policy_changes(policy, machine(), T0)
    assert all(isinstance(i, AddRuleSet) for i in items)
    assert [i.rule_set.rsv_id for i in items if isinstance(i, AddRuleSet)] == [
        "lite-s1-v4",  # and one per combination of Lite's three add-ons (D-13.18)
        "lite-s1-v4-split90",
        "lite-s1-v4-swing",
        "lite-s1-v4-split90-swing",
        "lite-s1-v4-swapfree",
        "lite-s1-v4-split90-swapfree",
        "lite-s1-v4-swing-swapfree",
        "lite-s1-v4-split90-swing-swapfree",
    ]


def test_changed_numbers_without_a_new_version_are_refused() -> None:
    def change(data: dict[str, Any]) -> None:
        lite(data)["stages"][0]["daily_loss_percent"] = "4"

    with pytest.raises(PolicyError, match="raise the stage's version"):
        policy_changes(parse_policy(edited(change)), machine(), T0)


def test_trading_changes_need_a_new_config_version() -> None:
    def leverage(data: dict[str, Any]) -> None:
        data["trading"]["leverage"]["metal"] = "20"

    with pytest.raises(PolicyError, match="config_version"):
        policy_changes(parse_policy(edited(leverage)), machine(), T0)

    def versioned(data: dict[str, Any]) -> None:
        leverage(data)
        data["trading"]["config_version"] = "trading-v9"
        data["trading"]["delay_threshold_ms"] = 3_000

    (item,) = policy_changes(parse_policy(edited(versioned)), machine(), T0)
    assert isinstance(item, UpdateTrading) and item.config_version == "trading-v9"
    assert {s.name for s in item.instruments} == {"XAU/USD", "XAG/USD"}  # only what changed
    assert item.guard is not None and item.guard.delay_threshold_ms == 3_000


# --- engine trading settings changed by input -----------------------------------------


class Driver:
    def __init__(self) -> None:
        self.machine = machine()
        self.n = 0

    def send(self, item: Any) -> list[object]:
        self.n += 1
        events = CODEC.decode(self.machine.apply(f"{self.n}-0", item).record.events)
        assert isinstance(events, tuple)
        return list(events)

    def buy(self, order_id: str, volume: int, at: int) -> list[object]:
        request = OrderRequest(order_id, "A", "XAU/USD", Side.BUY, OrderType.MARKET, volume)
        return self.send(PlaceOrder(request, at))


def opened() -> Driver:
    d = Driver()
    d.send(OpenAccount("A", "lite-s1-v3", 10_000_000, T0))
    d.send(PriceUpdate("XAU/USD", 1, T0, T0, 330_000))
    return d


def gold(**changes: Any) -> Any:
    spec = next(i for i in SHIPPED.trading.instruments if i.name == "XAU/USD")
    return replace(spec, **changes)


def test_new_leverage_applies_to_new_positions_only() -> None:
    d = opened()
    first = next(e for e in d.buy("o1", 10, T0 + 1) if isinstance(e, PositionOpened))
    events = d.send(UpdateTrading("trading-v9", (gold(leverage=Fraction(25)),), None, T0 + 2))
    assert events == [TradingConfigChanged(T0 + 2, "trading-v9", ("XAU/USD",), False)]
    second = next(e for e in d.buy("o2", 10, T0 + 3) if isinstance(e, PositionOpened))
    assert second.config_version == "trading-v9" and first.config_version == "trading-v8"
    held = {p.position_id: p.margin_usd for p in d.machine.supervisor.engine.positions("A")}
    assert held["o2"] == 2 * held["o1"]  # 1:25 instead of 1:50, same price
    assert cents_ceil(held["o1"]) == first.margin_cents  # the open position kept its margin


def test_new_lot_caps_and_guards_apply() -> None:
    d = opened()
    d.send(
        UpdateTrading(
            "trading-v9",
            (gold(max_order_volume_per_100k=50, max_symbol_volume_per_100k=50),),
            GuardConfig(delay_threshold_ms=500),
            T0 + 1,
        )
    )
    refused = [e for e in d.buy("o1", 60, T0 + 2) if isinstance(e, OrderRejected)]
    assert refused and refused[0].reason == "order_volume_cap"
    d.send(PriceUpdate("XAU/USD", 2, T0 + 3, T0 + 1_003, 330_100))  # 1 s late: now too late
    assert d.machine.supervisor.engine.is_frozen("XAU/USD")


@pytest.mark.parametrize(
    "update",
    [
        UpdateTrading("trading-v8", (), None, T0),  # version already in force
        UpdateTrading("trading-v9", (gold(contract_size=1),), None, T0),  # fixed field
        UpdateTrading("trading-v9", (gold(), gold()), None, T0),  # listed twice
    ],
)
def test_trading_changes_that_are_refused(update: UpdateTrading) -> None:
    d = opened()
    before = d.machine.state()
    (event,) = d.send(update)
    assert isinstance(event, InputRejected)
    assert event.reason is InputRejectReason.INVALID_TRADING_CONFIG
    assert d.machine.state() == before


def test_changed_settings_survive_a_snapshot() -> None:
    d = opened()
    d.send(UpdateTrading("trading-v9", (gold(leverage=Fraction(25)),), GuardConfig(500), T0 + 1))
    state = CODEC.decode(CODEC.encode(d.machine.state()))
    assert state == d.machine.state()
    engine = machine().supervisor.engine
    engine.restore(d.machine.state().engine)
    assert engine.config_version == "trading-v9"
    assert engine.instruments["XAU/USD"].leverage == 25 and engine.guard_config == GuardConfig(500)
