"""Add-ons (D-13.18): derived rule sets, prices, and the engine rules they
change (Swing's leverage cap, swap-free nights). The split add-on is tested
with the payout split in test_payouts."""

from dataclasses import replace
from fractions import Fraction

import pytest

from propfirm.customers.pricing import DiscountKind, PricingError, price
from propfirm.journal.machine import CODEC
from propfirm.policy import PolicyError, load_policy, parse_policy, stage_of
from propfirm.rules.addons import addons_of, base_id, derive, ordered, with_addons
from propfirm.rules.products import PRODUCTS, RuleSet, Stage
from propfirm.trading.events import SwapCharged
from propfirm.trading.orders import Side
from tests.rules_rig import RulesRig, ny
from tests.test_policy import edited
from tests.test_swaps import EUR_LONG, MONDAY, SIZE

POLICY = load_policy()
LITE = POLICY.product("lite")
INSTANT = POLICY.product("instant")
TEN = Fraction(10, 100)


def offers(*ids: str) -> tuple[object, ...]:
    return tuple(o for o in POLICY.addons.offers if o.id in ids)


# --- ids --------------------------------------------------------------------------------


def test_add_ons_ride_on_the_rule_set_id_in_a_fixed_order() -> None:
    assert with_addons("lite-s1-v3", ["swapfree", "swing"]) == "lite-s1-v3-swing-swapfree"
    assert with_addons("lite-s2-v3", addons_of("lite-s1-v3-split90")) == "lite-s2-v3-split90"
    assert with_addons("one-step-s1-v4-a7", ["swing"]) == "one-step-s1-v4-a7-swing"
    assert addons_of("lite-funded-v4") == () and base_id("lite-funded-v4") == "lite-funded-v4"
    assert addons_of("one-step-s1-v4-a7-split90-swing") == ("split90", "swing")
    assert base_id("one-step-s1-v4-a7-split90-swing") == "one-step-s1-v4-a7"
    assert stage_of("lite-funded-v4-swing-swapfree") is Stage.FUNDED
    with pytest.raises(ValueError, match="twice"):
        ordered(["swing", "swing"])
    with pytest.raises(ValueError, match="unknown"):
        ordered(["weekly"])  # proposed and rejected (D-13.18)


def test_swing_lifts_the_news_and_break_rules_and_caps_leverage() -> None:
    funded = LITE.rule_sets[-1]
    assert funded.flat_before_breaks and funded.news_restricted
    swing = POLICY.with_addons(funded, ("swing",))
    assert (swing.rsv_id, swing.news_restricted, swing.news_breaches) == (
        f"{funded.rsv_id}-swing",
        False,
        False,
    )
    assert (swing.flat_before_breaks, swing.leverage_cap, swing.swap_free_nights) == (
        False,
        Fraction(30),
        0,
    )
    both = POLICY.with_addons(funded, ("swapfree", "split90"))
    assert (both.rsv_id, both.swap_free_nights, both.leverage_cap) == (
        f"{funded.rsv_id}-split90-swapfree",
        5,
        None,
    )
    # everything else is the stage's own
    assert replace(both, rsv_id=funded.rsv_id, swap_free_nights=0) == funded
    with pytest.raises(ValueError, match="already carries"):
        derive(swing, ("swapfree",), Fraction(30), 5)


def test_every_combination_is_published_and_round_trips() -> None:
    published = POLICY.published_rule_sets()
    ids = [r.rsv_id for r in published]
    assert len(ids) == len(set(ids))
    lite_funded = [i for i in ids if i.startswith(LITE.rule_sets[-1].rsv_id)]
    assert len(lite_funded) == 8  # 3 add-ons on Lite
    assert [i for i in ids if i.startswith("instant-")] == [
        INSTANT.rule_sets[0].rsv_id,
        f"{INSTANT.rule_sets[0].rsv_id}-swapfree",  # swap-free only on Instant
    ]
    for r in published:
        assert CODEC.decode(CODEC.encode(r)) == r
    # a base rule set encodes as before: the new fields keep their defaults
    assert "leverage_cap" not in CODEC.encode(LITE.rule_sets[0])


def test_the_shipped_add_ons() -> None:
    a = POLICY.addons
    assert [(o.id, o.price, sorted(o.products)) for o in a.offers] == [
        ("split90", Fraction(20, 100), ["lite", "one-step"]),
        ("swing", Fraction(15, 100), ["lite", "one-step"]),
        ("swapfree", TEN, ["classic", "instant", "lite", "one-step"]),
    ]
    assert (a.split, a.swing_leverage, a.swap_free_nights, a.off) == (
        Fraction(9, 10),
        Fraction(30),
        5,
        frozenset(),
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("split_percent", "101"),
        ("off", ["weekly"]),
        ("swing_leverage", 0),
    ],
)
def test_bad_add_on_settings_are_refused(key: str, value: object) -> None:
    def change(data: dict[str, object]) -> None:
        data["addons"][key] = value  # type: ignore[index]

    with pytest.raises(PolicyError):
        parse_policy(edited(change))


def test_an_add_on_for_an_unknown_product_is_refused() -> None:
    def change(data: dict[str, object]) -> None:
        data["addons"]["swing"]["products"] = ["lite", "gold"]  # type: ignore[index]

    with pytest.raises(PolicyError, match="unknown products"):
        parse_policy(edited(change))


# --- prices -----------------------------------------------------------------------------


def test_add_ons_add_their_share_of_the_list_price() -> None:
    quote = price(LITE, 1_000_000, 0, addons=offers("split90", "swing", "swapfree"))  # type: ignore[arg-type]
    # $55 + 20 % + 15 % + 10 %: $11.00 + $8.25 + $5.50
    assert (quote.addons, quote.addon_cents, quote.price_cents) == (
        ("split90", "swing", "swapfree"),
        2_475,
        7_975,
    )


def test_a_discount_comes_off_the_list_price_only() -> None:
    quote = price(LITE, 2_500_000, 0, TEN, addons=offers("swing"))  # type: ignore[arg-type]
    # $109 less 10 % ($10.90), plus Swing on the list price ($16.35)
    assert (quote.discount_kind, quote.discount_cents, quote.addon_cents) == (
        DiscountKind.CODE,
        1_090,
        1_635,
    )
    assert quote.price_cents == 10_900 - 1_090 + 1_635


def test_an_add_on_not_sold_with_the_product_is_refused() -> None:
    with pytest.raises(PricingError, match="not sold with"):
        price(INSTANT, 1_000_000, 0, addons=offers("swing"))  # type: ignore[arg-type]


# --- the engine -------------------------------------------------------------------------

BASE = replace(PRODUCTS["lite"][0], rsv_id="lite-addons-test")


def swaps(rig: RulesRig, until: int) -> list[tuple[int, int]]:
    return [
        (e.nights, e.swap_cents) for e in rig.price(110_000, until) if isinstance(e, SwapCharged)
    ]


def long_eur(rules: RuleSet) -> RulesRig:
    rig = RulesRig(rules, SIZE, MONDAY, swap=EUR_LONG)
    rig.price(110_000, MONDAY)
    rig.order(Side.BUY, 100, MONDAY)  # 1 lot: $110,000, $7.6389 a night
    return rig


def test_swap_free_skips_each_positions_first_nights() -> None:
    rig = long_eur(replace(BASE, swap_free_nights=5))
    # Mon 1, Tue 1, Wed 3: five nights, free; Thu and Fri charged
    assert swaps(rig, ny(2026, 11, 18, 17, 0, 1)) == []
    (position,) = rig.engine.positions("A")
    assert position.swap_nights == 5
    assert swaps(rig, ny(2026, 11, 20, 17, 0, 1)) == [(1, 764), (1, 764)]


def test_a_reset_past_the_free_nights_charges_only_the_rest() -> None:
    rig = long_eur(replace(BASE, swap_free_nights=3))
    # Mon and Tue free; Wednesday's 3 nights: 1 free, 2 charged
    assert swaps(rig, ny(2026, 11, 18, 17, 0, 1)) == [(2, 1_528)]


def test_without_the_add_on_nothing_changes() -> None:
    rig = long_eur(BASE)
    assert swaps(rig, ny(2026, 11, 18, 17, 0, 1)) == [(1, 764), (1, 764), (3, 2_292)]
    (position,) = rig.engine.positions("A")
    assert position.swap_nights == 0  # not counted, not recorded


def test_swing_caps_leverage_at_1_30() -> None:
    plain, swing = long_eur(BASE), long_eur(replace(BASE, leverage_cap=Fraction(30)))
    ((p,), (s,)) = plain.engine.positions("A"), swing.engine.positions("A")
    # 1 lot bought at the ask, 1.10005: $110,005 at 1:100 and at 1:30
    assert (p.margin_usd, s.margin_usd) == (Fraction(110_005, 100), Fraction(110_005, 30))
    # a cap above the instrument's own leverage changes nothing
    loose = long_eur(replace(BASE, leverage_cap=Fraction(500)))
    assert loose.engine.positions("A")[0].margin_usd == p.margin_usd
