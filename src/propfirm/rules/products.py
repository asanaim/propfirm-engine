"""Product rule sets (Confirmed Configuration CC-2 and CC-3).

Each stage of each product is one immutable rule set; its `rsv_id` is the
rule-set version recorded on every rule outcome. Loss limits and targets are
fractions of the initial balance.

| Product | Stages | Targets | Max loss | Daily loss | Consistency |
| Lite    | S1 S2 FND | 6% then 6% | 6% static | 3% | none |
| Classic | S1 S2 FND | 10% then 5% | 8% static | 4% | none |
| 1-Step  | S1 FND | 10% | 6% static | 3% | best day <= 50% of profit |
| Instant | FND | none | 5% trailing, stops at start | 3% | best day <= 50% of profit |

Decisions taken here:
- D10 [CONFIRMED 29 Oct 2026]: the target is measured on closed balance.
- D11 [CONFIRMED 29 Oct 2026]: a breach wins over a pass in the same check.
- [DEFAULT] The account must be flat (no positions, no pending orders) to
  pass: the second half of D10, not yet decided.
- [DEFAULT] A trading day counts when at least one position opens in it
  (D6 definition open).
- [DEFAULT] On 1-Step the consistency rule is also a pass condition for the
  evaluation, over the whole stage (D19 window open). On funded 1-Step and
  Instant it gates payouts, which come later.
"""

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from propfirm._checks import require_int, require_text
from propfirm.journal.codec import added_field


class Stage(StrEnum):
    S1 = "s1"
    S2 = "s2"
    FUNDED = "funded"


class MaxLossKind(StrEnum):
    STATIC = "static"  # floor = IB * (1 - M)
    TRAILING = "trailing"  # floor = min(equity high-water mark - M * IB, IB)


class TargetBasis(StrEnum):
    CLOSED_BALANCE = "closed_balance"
    EQUITY = "equity"


class TieRule(StrEnum):
    BREACH_WINS = "breach_wins"
    PASS_WINS = "pass_wins"


def _usd(dollars: int) -> int:
    return dollars * 100


@dataclass(frozen=True, slots=True)
class RuleSet:
    rsv_id: str
    product: str
    stage: Stage
    profit_target: Fraction | None
    max_loss: Fraction
    max_loss_kind: MaxLossKind
    daily_loss: Fraction
    min_trading_days: int
    consistency: Fraction | None  # best day <= this share of total profit
    consistency_at_pass: bool
    sizes_cents: tuple[int, ...]
    target_basis: TargetBasis = TargetBasis.CLOSED_BALANCE
    flat_to_pass: bool = True
    tie_rule: TieRule = TieRule.BREACH_WINS
    # Restricted news windows bind accounts on this rule set (M13, D15). As
    # FTMO: funded accounts only; evaluations are exempt [decided 2026-10-31].
    # Added after records existed: True (every phase) for older rule sets.
    news_restricted: bool = added_field(True)

    def __post_init__(self) -> None:
        require_text("rsv_id", self.rsv_id)
        require_text("product", self.product)
        require_int("min_trading_days", self.min_trading_days, minimum=0)
        for name in ("max_loss", "daily_loss"):
            value = getattr(self, name)
            if not isinstance(value, Fraction) or not 0 < value < 1:
                raise ValueError(f"{name} must be a Fraction in (0, 1)")
        if self.daily_loss > self.max_loss:
            raise ValueError("daily_loss must not exceed max_loss")
        for name in ("profit_target", "consistency"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, Fraction) or value <= 0):
                raise ValueError(f"{name} must be a positive Fraction or None")
        if (self.stage is Stage.FUNDED) != (self.profit_target is None):
            raise ValueError("only funded stages have no profit target")
        if self.consistency_at_pass and self.consistency is None:
            raise ValueError("consistency_at_pass needs a consistency share")
        if not isinstance(self.news_restricted, bool):
            raise TypeError("news_restricted must be a bool")
        if not self.sizes_cents:
            raise ValueError("sizes_cents must not be empty")
        for size in self.sizes_cents:
            require_int("size", size, minimum=1)
            for share in (self.max_loss, self.daily_loss, self.profit_target):
                if share is not None and (size * share).denominator != 1:
                    raise ValueError(f"{share} of {size} cents is not whole cents")

    def amount(self, share: Fraction, initial_balance: int) -> int:
        """`share` of the initial balance in cents; exact by construction."""
        value = initial_balance * share
        if value.denominator != 1:
            raise ValueError("limit is not a whole number of cents")
        return value.numerator


_P = Fraction
_LITE_SIZES = tuple(_usd(k * 1_000) for k in (5, 10, 25, 50, 100, 200))
_CLASSIC_SIZES = tuple(_usd(k * 1_000) for k in (10, 25, 50, 100, 200))
_INSTANT_SIZES = tuple(_usd(k * 1_000) for k in (5, 10, 25))


def _lite(stage: Stage, target: Fraction | None) -> RuleSet:
    return RuleSet(
        rsv_id=f"lite-{stage}-v{1 if stage is Stage.FUNDED else 2}",
        product="lite",
        stage=stage,
        profit_target=target,
        max_loss=_P(6, 100),
        max_loss_kind=MaxLossKind.STATIC,
        daily_loss=_P(3, 100),
        min_trading_days=0 if stage is Stage.FUNDED else 4,
        consistency=None,
        consistency_at_pass=False,
        sizes_cents=_LITE_SIZES,
        news_restricted=stage is Stage.FUNDED,
    )


def _classic(stage: Stage, target: Fraction | None) -> RuleSet:
    return RuleSet(
        rsv_id=f"classic-{stage}-v{1 if stage is Stage.FUNDED else 2}",
        product="classic",
        stage=stage,
        profit_target=target,
        max_loss=_P(8, 100),
        max_loss_kind=MaxLossKind.STATIC,
        daily_loss=_P(4, 100),
        min_trading_days=0 if stage is Stage.FUNDED else 4,
        consistency=None,
        consistency_at_pass=False,
        sizes_cents=_CLASSIC_SIZES,
        news_restricted=stage is Stage.FUNDED,
    )


def _one_step(stage: Stage, target: Fraction | None) -> RuleSet:
    return RuleSet(
        rsv_id=f"one-step-{stage}-v{1 if stage is Stage.FUNDED else 2}",
        product="one-step",
        stage=stage,
        profit_target=target,
        max_loss=_P(6, 100),
        max_loss_kind=MaxLossKind.STATIC,
        daily_loss=_P(3, 100),
        min_trading_days=0 if stage is Stage.FUNDED else 4,
        consistency=_P(1, 2),
        consistency_at_pass=stage is not Stage.FUNDED,
        sizes_cents=_LITE_SIZES,
        news_restricted=stage is Stage.FUNDED,
    )


PRODUCTS: dict[str, tuple[RuleSet, ...]] = {
    "lite": (
        _lite(Stage.S1, _P(6, 100)),
        _lite(Stage.S2, _P(6, 100)),
        _lite(Stage.FUNDED, None),
    ),
    "classic": (
        _classic(Stage.S1, _P(10, 100)),
        _classic(Stage.S2, _P(5, 100)),
        _classic(Stage.FUNDED, None),
    ),
    "one-step": (_one_step(Stage.S1, _P(10, 100)), _one_step(Stage.FUNDED, None)),
    "instant": (
        RuleSet(
            rsv_id="instant-funded-v1",
            product="instant",
            stage=Stage.FUNDED,
            profit_target=None,
            max_loss=_P(5, 100),
            max_loss_kind=MaxLossKind.TRAILING,
            daily_loss=_P(3, 100),
            min_trading_days=0,
            consistency=_P(1, 2),
            consistency_at_pass=False,
            sizes_cents=_INSTANT_SIZES,
        ),
    ),
}

RULESETS: dict[str, RuleSet] = {r.rsv_id: r for stages in PRODUCTS.values() for r in stages}
