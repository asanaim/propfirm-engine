"""Add-ons (owner decision D-13.18): bought with an account, they change its
rules for its whole life, through every stage.

An add-on does not change a published rule set; it is a derived rule set
with its own id, the base id followed by the add-on ids in a fixed order
(`lite-funded-v3-swing-swapfree`). Every rule outcome therefore names the
add-ons it used, an account keeps them from stage to stage (the next
stage's id takes the same suffix), and the engine needs nothing new to
know them.

- split90: the funded split is 90 % from the first payout (read by the
  payout service; the rule set itself is unchanged);
- swing: no news rule, no market break rule, leverage at most 1:30;
- swapfree: no swap on the first nights of each position (5).

The leverage and the nights are policy settings (`[addons]`).
"""

import re
from dataclasses import replace
from fractions import Fraction

from propfirm.rules.products import RuleSet

SPLIT90 = "split90"
SWING = "swing"
SWAPFREE = "swapfree"
ADDON_IDS = (SPLIT90, SWING, SWAPFREE)  # the order they take in an id

_SUFFIX = re.compile(r"(?:-(?:" + "|".join(ADDON_IDS) + r"))+$")


def addons_of(rsv_id: str) -> tuple[str, ...]:
    """The add-ons a rule-set id carries, in id order; () for a base id."""
    found = _SUFFIX.search(rsv_id)
    if found is None:
        return ()
    return tuple(found.group(0)[1:].split("-"))


def base_id(rsv_id: str) -> str:
    """The rule-set id without its add-ons."""
    found = _SUFFIX.search(rsv_id)
    return rsv_id if found is None else rsv_id[: found.start()]


def ordered(addons: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """`addons` in id order; ValueError for an unknown or repeated one."""
    if len(set(addons)) != len(addons):
        raise ValueError("an add-on is named twice")
    unknown = sorted(set(addons) - set(ADDON_IDS))
    if unknown:
        raise ValueError(f"unknown add-on {unknown[0]!r}")
    return tuple(a for a in ADDON_IDS if a in addons)


def with_addons(rsv_id: str, addons: tuple[str, ...] | list[str]) -> str:
    """The id of `rsv_id`'s base with `addons` (replacing any it had)."""
    chosen = ordered(addons)
    return base_id(rsv_id) + "".join(f"-{a}" for a in chosen)


def derive(
    rules: RuleSet,
    addons: tuple[str, ...] | list[str],
    swing_leverage: Fraction,
    swap_free_nights: int,
) -> RuleSet:
    """The rule set `rules` (a base) with `addons`; the Swing leverage cap
    and the swap-free nights come from the policy (`[addons]`)."""
    if addons_of(rules.rsv_id):
        raise ValueError(f"{rules.rsv_id} already carries add-ons")
    chosen = ordered(addons)
    derived = replace(rules, rsv_id=with_addons(rules.rsv_id, chosen))
    if SWING in chosen:
        derived = replace(
            derived,
            news_restricted=False,
            news_breaches=False,
            flat_before_breaks=False,
            leverage_cap=swing_leverage,
        )
    if SWAPFREE in chosen:
        derived = replace(derived, swap_free_nights=swap_free_nights)
    return derived
