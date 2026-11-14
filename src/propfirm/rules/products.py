"""Product rule sets (Confirmed Configuration CC-2 and CC-3).

Each stage of each product is one immutable rule set; its `rsv_id` is the
rule-set version recorded on every rule outcome. Loss limits and targets are
fractions of the initial balance.

| Product | Stages | Targets | Max loss | Daily loss | Consistency |
| Lite    | S1 S2 FND | 6% then 6% | 6% static | 3% | none |
| Classic | S1 S2 FND | 10% then 5% | 8% static | 4% | none |
| 1-Step  | S1 FND | 10% | 10% end-of-day trail, stops at start | 3% | best day <= 50% of profit |
| Instant | FND | none | 5% trailing, stops at start | 3% | best day <= 50% of profit |

Product line version 1 (13 Nov 2026, D-13.17 and D-13.20): Lite and 1-Step
are sold from 10K (5K removed; Lite v3/funded v2), and 1-Step moves to
FTMO's end-of-day trailing loss (v3/funded v2). Earlier versions stay in
RETIRED for the accounts sold on them.

1-Step floor (14 Nov 2026, D-14.10, replaces D-13.20): the end-of-day
trailing floor stops at the starting balance and, on the funded stage, a
payout does not lower it again (s1 v4, funded v5).

Max loss kinds: static; trailing (intraday, on equity); trailing at end of
day (on the balance at each 17:00 New York reset). A trailing floor may lock
at the starting balance and may restart after a payout; both are fields.

Instant from 12 Nov 2026 (policy version 2): the trailing floor does not
restart after a payout (review D1); version 1 accounts keep the restart.

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

from dataclasses import dataclass, replace
from enum import StrEnum
from fractions import Fraction

from propfirm._checks import require_int, require_text
from propfirm.journal.codec import added_field


class Stage(StrEnum):
    S1 = "s1"
    S2 = "s2"
    FUNDED = "funded"


class MaxLossKind(StrEnum):
    """How the maximum loss floor moves (IB: initial balance, M: max loss).

    The trailing kinds give floor = high-water mark - M * IB, capped at IB
    when the rule set's `floor_locks_at_start` holds."""

    STATIC = "static"  # floor = IB * (1 - M)
    # intraday: the high-water mark follows equity on every check
    TRAILING = "trailing"
    # end of day: the high-water mark follows the balance at each 17:00 New
    # York reset only, as FTMO's 1-Step [decided 13 Nov 2026, D-13.17]
    TRAILING_EOD = "trailing_eod"

    @property
    def trails(self) -> bool:
        return self is not MaxLossKind.STATIC


class TargetBasis(StrEnum):
    CLOSED_BALANCE = "closed_balance"
    EQUITY = "equity"


class TieRule(StrEnum):
    BREACH_WINS = "breach_wins"
    # a rule name, not a password
    PASS_WINS = "pass_wins"  # nosec B105


# The kind as an adjective before "maximum loss" on public pages.
KIND_WORDS: dict[MaxLossKind, str] = {
    MaxLossKind.STATIC: "",
    MaxLossKind.TRAILING: "trailing ",
    MaxLossKind.TRAILING_EOD: "end-of-day trailing ",
}


def max_loss_words(rules: RuleSet) -> str:
    """The kind as the funded agreement states it: "static", "trailing",
    "trailing at end of day"; a floor that keeps rising past the starting
    balance says so."""
    words = {
        MaxLossKind.STATIC: "static",
        MaxLossKind.TRAILING: "trailing",
        MaxLossKind.TRAILING_EOD: "trailing at end of day",
    }[rules.max_loss_kind]
    if rules.max_loss_kind.trails and not rules.floor_locks_at_start:
        words += ", not capped at the starting balance"
    return words


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
    # Trailing max loss only: whether a payout lowers the high-water mark by
    # the amount paid, so the floor restarts below the new balance (True:
    # the rule sets before 12 Nov 2026). False [decided 12 Nov 2026, review
    # D1]: the floor stays where it was; a payout may not take the account
    # to or below it. Added after records existed: True for older rule sets.
    floor_restarts_after_payout: bool = added_field(True)
    # Trailing max loss only: the floor stops rising once it reaches the
    # initial balance (True: every rule set before 13 Nov 2026). False: it
    # keeps trailing above the initial balance [build plan step 1].
    floor_locks_at_start: bool = added_field(True)
    # The news rule as FTMO (D-13.17 news row, 13 Nov 2026), on news-
    # restricted accounts only: pending entry orders on an affected symbol
    # are cancelled when its window opens, and a fill inside a window (stop
    # loss, take profit, trailing stop) breaches the account (BreachReason.
    # NEWS). False: entries wait for the window to end and a fill raises an
    # integrity case for staff (every rule set before 14 Nov 2026).
    news_breaches: bool = added_field(False)
    # Market break rule (build plan step 1, FTMO's funded Standard account):
    # positions are closed and pending orders cancelled shortly before a
    # market break of 2 hours or more, and no new order opens in those last
    # minutes. False for evaluations and for rule sets before 13 Nov 2026;
    # the Swing add-on (step 2) will sell a funded stage without it.
    flat_before_breaks: bool = added_field(False)

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
        if not isinstance(self.news_breaches, bool):
            raise TypeError("news_breaches must be a bool")
        if self.news_breaches and not self.news_restricted:
            raise ValueError("news_breaches needs news_restricted")
        if not isinstance(self.news_restricted, bool):
            raise TypeError("news_restricted must be a bool")
        if not isinstance(self.floor_restarts_after_payout, bool):
            raise TypeError("floor_restarts_after_payout must be a bool")
        if not isinstance(self.floor_locks_at_start, bool):
            raise TypeError("floor_locks_at_start must be a bool")
        if not isinstance(self.flat_before_breaks, bool):
            raise TypeError("flat_before_breaks must be a bool")
        if not self.max_loss_kind.trails:
            if not self.floor_restarts_after_payout:
                raise ValueError("only a trailing max loss has a floor that could restart")
            if not self.floor_locks_at_start:
                raise ValueError("only a trailing max loss has a floor that could rise")
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
_LINE_V1_SIZES = tuple(_usd(k * 1_000) for k in (10, 25, 50, 100, 200))  # no 5K
_CLASSIC_SIZES = tuple(_usd(k * 1_000) for k in (10, 25, 50, 100, 200))
_INSTANT_SIZES = tuple(_usd(k * 1_000) for k in (5, 10, 25))


def _lite(stage: Stage, target: Fraction | None, line_v1: bool = True) -> RuleSet:
    version = (
        (2 if stage is Stage.FUNDED else 3) if line_v1 else (1 if stage is Stage.FUNDED else 2)
    )
    return RuleSet(
        rsv_id=f"lite-{stage}-v{version}",
        product="lite",
        stage=stage,
        profit_target=target,
        max_loss=_P(6, 100),
        max_loss_kind=MaxLossKind.STATIC,
        daily_loss=_P(3, 100),
        min_trading_days=0 if stage is Stage.FUNDED else 4,
        consistency=None,
        consistency_at_pass=False,
        sizes_cents=_LINE_V1_SIZES if line_v1 else _LITE_SIZES,
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


def _one_step(stage: Stage, target: Fraction | None, line_v1: bool = True) -> RuleSet:
    version = (
        (2 if stage is Stage.FUNDED else 3) if line_v1 else (1 if stage is Stage.FUNDED else 2)
    )
    return RuleSet(
        rsv_id=f"one-step-{stage}-v{version}",
        product="one-step",
        stage=stage,
        profit_target=target,
        # line v1: a 10 % end-of-day trailing loss; it kept rising (D-13.20)
        # until D-14.10 stopped it at the starting balance (_floor_stops)
        max_loss=_P(10, 100) if line_v1 else _P(6, 100),
        max_loss_kind=MaxLossKind.TRAILING_EOD if line_v1 else MaxLossKind.STATIC,
        daily_loss=_P(3, 100),
        min_trading_days=0 if stage is Stage.FUNDED else 4,
        consistency=_P(1, 2),
        consistency_at_pass=stage is not Stage.FUNDED,
        sizes_cents=_LINE_V1_SIZES if line_v1 else _LITE_SIZES,
        news_restricted=stage is Stage.FUNDED,
        floor_locks_at_start=not line_v1,
    )


_INSTANT_V1 = RuleSet(
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
)
# owner decision K7, 12 Nov 2026 (review D1): the floor does not restart
_INSTANT_V2 = replace(_INSTANT_V1, rsv_id="instant-funded-v2", floor_restarts_after_payout=False)

# market break rule (13 Nov 2026), as FTMO's funded Standard account: flat
# shortly before a closed period of 2 hours or more
_INSTANT_V3 = replace(_INSTANT_V2, rsv_id="instant-funded-v3", flat_before_breaks=True)


def _breaks(rules: RuleSet, version: int) -> RuleSet:
    """A funded rule set with the market break rule, as its next version."""
    base = rules.rsv_id.rsplit("-v", 1)[0]
    return replace(rules, rsv_id=f"{base}-v{version}", flat_before_breaks=True)


def _news(rules: RuleSet, version: int) -> RuleSet:
    """A funded rule set with the news rule as FTMO (14 Nov 2026), as its
    next version."""
    base = rules.rsv_id.rsplit("-v", 1)[0]
    return replace(rules, rsv_id=f"{base}-v{version}", news_breaches=True)


_LITE_FUNDED_V3 = _breaks(_lite(Stage.FUNDED, None), 3)
_CLASSIC_FUNDED_V2 = _breaks(_classic(Stage.FUNDED, None), 2)
_ONE_STEP_FUNDED_V3 = _breaks(_one_step(Stage.FUNDED, None), 3)
_ONE_STEP_S1_V3 = _one_step(Stage.S1, _P(10, 100))
_ONE_STEP_FUNDED_V4 = _news(_ONE_STEP_FUNDED_V3, 4)


def _floor_stops(rules: RuleSet, version: int) -> RuleSet:
    """A 1-Step rule set whose end-of-day trailing floor stops at the
    starting balance, as its next version (owner decision D-14.10, 14 Nov
    2026; replaces D-13.20). Funded: a payout does not lower the floor
    again, as on Instant (K7)."""
    base = rules.rsv_id.rsplit("-v", 1)[0]
    return replace(
        rules,
        rsv_id=f"{base}-v{version}",
        floor_locks_at_start=True,
        floor_restarts_after_payout=rules.stage is not Stage.FUNDED,
    )


# The rule sets on sale, per product, in stage order.
PRODUCTS: dict[str, tuple[RuleSet, ...]] = {
    "lite": (
        _lite(Stage.S1, _P(6, 100)),
        _lite(Stage.S2, _P(6, 100)),
        _news(_LITE_FUNDED_V3, 4),
    ),
    "classic": (
        _classic(Stage.S1, _P(10, 100)),
        _classic(Stage.S2, _P(5, 100)),
        _news(_CLASSIC_FUNDED_V2, 3),
    ),
    "one-step": (
        _floor_stops(_ONE_STEP_S1_V3, 4),
        _floor_stops(_ONE_STEP_FUNDED_V4, 5),
    ),
    "instant": (_news(_INSTANT_V3, 4),),
}

# Versions no longer sold; accounts opened on them keep them.
RETIRED: tuple[RuleSet, ...] = (
    # 1-Step before its floor stopped at the starting balance (D-14.10)
    _ONE_STEP_S1_V3,
    _ONE_STEP_FUNDED_V4,
    _INSTANT_V1,
    _INSTANT_V2,
    # funded stages before the news rule as FTMO (14 Nov 2026)
    _INSTANT_V3,
    _LITE_FUNDED_V3,
    _CLASSIC_FUNDED_V2,
    _ONE_STEP_FUNDED_V3,
    # funded stages before the market break rule (13 Nov 2026)
    _lite(Stage.FUNDED, None),
    _classic(Stage.FUNDED, None),
    _one_step(Stage.FUNDED, None),
    # before product line version 1 (13 Nov 2026)
    _lite(Stage.S1, _P(6, 100), line_v1=False),
    _lite(Stage.S2, _P(6, 100), line_v1=False),
    _lite(Stage.FUNDED, None, line_v1=False),
    _one_step(Stage.S1, _P(10, 100), line_v1=False),
    _one_step(Stage.FUNDED, None, line_v1=False),
)

# every version, current and retired, by rule-set id
RULESETS: dict[str, RuleSet] = {
    r.rsv_id: r for r in (*(r for stages in PRODUCTS.values() for r in stages), *RETIRED)
}
