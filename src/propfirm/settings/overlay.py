"""Admin settings layered over the policy file.

Admins (only) change two kinds of setting at run time; the policy file
holds their starting values:

- country gating: the switch and the list of blocked countries;
- stage limits: a product stage's profit target, daily loss limit and
  drawdown (maximum loss) limit with its kind, static or trailing.

Each change is a numbered, append-only record with its author and reason;
the latest change of a setting wins. A stage-limits change makes a new
rule-set version, `<file rsv_id>-a<change number>` (one-step-s1-v2-a7). The
stage's other numbers (trading days, consistency, sizes) still come from
the file, so a later file version of that stage gives one-step-s1-v3-a7.
Published versions never change: open accounts keep theirs, and a new
account gets the version in force when it is bought.

A change naming a product or stage the file no longer has is kept in the
history and has no effect.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any

from propfirm.policy import (
    Policy,
    PolicyError,
    parse_countries,
    parse_percent,
    percent_text,
)
from propfirm.rules.products import MaxLossKind, RuleSet, Stage

COUNTRY_GATING = "country_gating"
STAGE_LIMITS = "stage_limits"


@dataclass(frozen=True, slots=True)
class CountryGating:
    country_gating: bool
    blocked_countries: frozenset[str]


@dataclass(frozen=True, slots=True)
class StageLimits:
    product_id: str
    stage: Stage
    profit_target: Fraction | None  # None: no target (funded stages)
    daily_loss: Fraction
    max_loss: Fraction
    max_loss_kind: MaxLossKind


type Setting = CountryGating | StageLimits


@dataclass(frozen=True, slots=True)
class SettingChange:
    id: int  # change number, from the database
    setting: Setting
    author: str  # Keycloak subject of the admin
    author_email: str | None
    reason: str
    ts: int  # UTC milliseconds


def limits_rule_set(base: RuleSet, limits: StageLimits, change_id: int) -> RuleSet:
    """The file's rule set with the admin's limits: a new version. Raises
    ValueError when the limits are invalid for this stage (a profit target
    on a funded stage, a limit that is not whole cents of every size)."""
    return replace(
        base,
        rsv_id=f"{base.rsv_id}-a{change_id}",
        profit_target=limits.profit_target,
        daily_loss=limits.daily_loss,
        max_loss=limits.max_loss,
        max_loss_kind=limits.max_loss_kind,
    )


def apply_changes(base: Policy, changes: Iterable[SettingChange]) -> Policy:
    """The policy in force: the file with the latest change of each setting."""
    gating: CountryGating | None = None
    limits: dict[tuple[str, Stage], SettingChange] = {}
    for change in sorted(changes, key=lambda c: c.id):
        match change.setting:
            case CountryGating() as value:
                gating = value
            case StageLimits() as value:
                limits[(value.product_id, value.stage)] = change
    access = base.access
    if gating is not None:
        access = replace(
            access,
            country_gating=gating.country_gating,
            blocked_countries=gating.blocked_countries,
        )
    products = []
    for product in base.products:
        rule_sets = []
        for rule_set in product.rule_sets:
            latest = limits.get((product.id, rule_set.stage))
            if latest is not None:
                assert isinstance(latest.setting, StageLimits)
                rule_set = limits_rule_set(rule_set, latest.setting, latest.id)
            rule_sets.append(rule_set)
        products.append(replace(product, rule_sets=tuple(rule_sets)))
    return replace(base, access=access, products=tuple(products))


# --- stored form (JSON; percentages as exact text, as in the policy file) -------------


def to_json(setting: Setting) -> tuple[str, dict[str, Any]]:
    """(kind, value) for the database."""
    match setting:
        case CountryGating():
            return COUNTRY_GATING, {
                "country_gating": setting.country_gating,
                "blocked_countries": sorted(setting.blocked_countries),
            }
        case StageLimits():
            return STAGE_LIMITS, {
                "product_id": setting.product_id,
                "stage": setting.stage.value,
                "profit_target_percent": percent_text(setting.profit_target),
                "daily_loss_percent": percent_text(setting.daily_loss),
                "max_loss_percent": percent_text(setting.max_loss),
                "max_loss_kind": setting.max_loss_kind.value,
            }


def from_json(kind: str, value: Mapping[str, Any]) -> Setting:
    if kind == COUNTRY_GATING:
        switch = value["country_gating"]
        if not isinstance(switch, bool):
            raise PolicyError("country_gating must be true or false")
        return CountryGating(
            switch, parse_countries(value["blocked_countries"], "blocked_countries")
        )
    if kind == STAGE_LIMITS:
        return stage_limits(
            value["product_id"],
            value["stage"],
            value["profit_target_percent"],
            value["daily_loss_percent"],
            value["max_loss_percent"],
            value["max_loss_kind"],
        )
    raise PolicyError(f"unknown setting kind {kind!r}")


def stage_limits(
    product_id: str,
    stage: str,
    profit_target_percent: str | None,
    daily_loss_percent: str,
    max_loss_percent: str,
    max_loss_kind: str,
) -> StageLimits:
    """Limits from their text form; PolicyError for a malformed value."""
    try:
        stage_value = Stage(stage)
    except ValueError:
        raise PolicyError("stage must be s1, s2 or funded") from None
    try:
        kind = MaxLossKind(max_loss_kind)
    except ValueError:
        raise PolicyError("max_loss_kind must be static or trailing") from None
    target = (
        None
        if profit_target_percent is None
        else parse_percent(profit_target_percent, "profit_target_percent")
    )
    daily = parse_percent(daily_loss_percent, "daily_loss_percent")
    maximum = parse_percent(max_loss_percent, "max_loss_percent")
    assert daily is not None and maximum is not None
    return StageLimits(product_id, stage_value, target, daily, maximum, kind)
