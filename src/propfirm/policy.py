"""The firm policy file: every number and switch the firm can change.

`load_policy(path)` reads `config/policy.toml` strictly: unknown or missing
keys, floats (percentages are text, money is whole cents) and inconsistent
values are errors, so a typo cannot silently change a rule.

What reads which part:
- `products`: the rule sets the engine enforces, and the catalogue
  (sizes, prices, split, discounts) the API shows and checkout uses.
- `trading`: leverage, lot caps, spreads and feed-guard thresholds.
- `access`: the country-gating switch and the minimum age (registration).
- `news`, `payouts`, `scaling`: read by later P2 steps.

Admin-owned settings: the country-gating switch with its country list, and
each stage's profit target, daily loss limit and drawdown limit, are
changed by admins through the API (`propfirm.settings`); the file holds
their starting values. `PolicySource.current()` gives the policy in force.

The engine never reads this file directly: the engine service turns
differences into journaled inputs (`policy_changes`), so every decision
records the settings it was made under.
"""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from propfirm.market.guards import GuardConfig
from propfirm.market.spread import MarketSpreadConfig
from propfirm.market.symbols import LAUNCH_SYMBOLS, AssetClass
from propfirm.rules.addons import ADDON_IDS, addons_of, derive, trial_id
from propfirm.rules.products import MaxLossKind, RuleSet, Stage
from propfirm.staff.records import SECTIONS_APPROVALS, Limits
from propfirm.trading.hours import ALWAYS, TradingHours, day_minute, new_york_ms, week_minute
from propfirm.trading.instruments import (
    InstrumentSpec,
    Pricing,
    SpreadAndCommission,
    default_instrument,
)
from propfirm.trading.swaps import CURRENCIES, SwapSettings, swap_terms

DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "config" / "policy.toml"
_PERCENT = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]+)?")
_COUNTRY = re.compile(r"[A-Z]{2}")
_PRODUCT_ID = re.compile(r"[a-z][a-z0-9-]{0,30}")
_STAGE_ORDER = {Stage.S1: 0, Stage.S2: 1, Stage.FUNDED: 2}


class PolicyError(ValueError):
    """The policy file is invalid, or a change to it cannot be applied."""


# --- parsed policy ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    country_gating: bool
    blocked_countries: frozenset[str]
    min_age: int = 18


@dataclass(frozen=True, slots=True)
class NewsPolicy:
    before_ms: int
    after_ms: int


@dataclass(frozen=True, slots=True)
class PayoutPolicy:
    minimum_cents: int
    first_after_days: int
    interval_days: int  # a later payout no sooner than this after the last one
    approvals_required: int
    basis: str = "closed_balance"  # D22/D23: "closed_balance" or "equity" at request
    flat_required: bool = True  # D23: no open position or pending order at request
    min_trading_days: int = 0  # D22: trading days in the period
    maximum_cents: int = 0  # D22: largest trader amount per payout; 0 = none
    trading_during_payout: str = "paused"  # D23: "paused" or "allowed", from request to debit
    wallet_cooling_off_hours: int = 0  # a new payout wallet is used only after this
    require_mfa: bool = True  # M04: MFA for wallet changes and payout requests
    step_up_minutes: int = 5  # M06: that MFA no older than this (0: any MFA login)
    # "pass_authorise": a T2 or above passes, then an admin authorises
    # [decided 31 Oct 2026]; "count": `approvals_required` employees (before)
    approval: str = "pass_authorise"
    # Short holds (build plan step 1, line 8; section 1, 13 Nov 2026): when
    # more than `short_hold_share_percent` of the period's profit comes from
    # trades the trader closed within `short_hold_seconds` of opening, a
    # payout request raises an integrity case and waits for review; staff
    # decide, a void needs two people. 0 percent: off.
    short_hold_seconds: int = 60
    short_hold_share_percent: int = 0  # 50 in the shipped policy
    # D-14.26: the payout networks traders may add a wallet on; the gate for
    # each further network (customers.networks lists what is built)
    networks: tuple[str, ...] = ("TRC20",)


PAYOUT_APPROVALS = ("pass_authorise", "count")
# the payout networks built, each with its address check in customers.networks
PAYOUT_NETWORKS = ("TRC20", "ERC20", "BEP20", "POLYGON")


@dataclass(frozen=True, slots=True)
class StaffPolicy:
    """The back office (staff spec, 5a): step-up sign-in on approvals and
    the grant limits."""

    approvals_need_mfa: bool = True  # every approval: a second factor
    step_up_minutes: int = 5  # entered at most this long ago (0: any MFA login)
    limits: Limits = field(default_factory=Limits)
    login_needs_mfa: bool = True  # every staff and admin login: a second factor
    staff_may_trade: bool = False  # staff and apprentices never buy or trade
    # maker-checker on settings: a second person approves each change; the
    # policy file cannot switch it off (rule AC00, 8 Nov 2026)
    settings_need_second_admin: bool = True
    proposal_expiry_hours: int = 72
    # the owners' stand-in approver: the longest one appointment lasts
    stand_in_max_days: int = 30
    # a leaver's login disabled at the identity provider: alert after, hard limit
    leaver_cutoff_alert_minutes: int = 60
    leaver_cutoff_max_hours: int = 24
    # the training gate (5c): a section's course before a level in it
    training_gate: bool = True
    training_valid_months: int = 12
    # pre-employment screening [7 Nov 2026]: no staff access without it
    screening_required: bool = True
    screening_valid_months: int = 12
    screening_due_days: int = 30  # rescreening on the to-do list this long before
    # oversight (5d)
    escalate_hours: int = 4
    dormant_days: int = 30
    review_days_lead: int = 90
    review_days_admin: int = 30
    office_timezone: str = "Asia/Dhaka"
    office_days: tuple[int, ...] = (6, 0, 1, 2, 3)  # Monday = 0
    office_start_hour: int = 9
    office_end_hour: int = 18
    pair_threshold: int = 3


@dataclass(frozen=True, slots=True)
class ScalingPolicy:
    # section 1 (owner, 13 Nov 2026): one step when all hold: the review
    # period since funding or the last step, the net profit and the paid
    # payouts within it, and class A or B
    review_days: int
    min_net_profit: Fraction
    min_paid_payouts: int
    step: Fraction
    split_after_step: int
    split_after: Fraction
    max_allocation_cents: int


@dataclass(frozen=True, slots=True)
class TradingPolicy:
    config_version: str
    instruments: tuple[InstrumentSpec, ...]  # launch-symbol order
    guard: GuardConfig


@dataclass(frozen=True, slots=True)
class ProductPolicy:
    id: str
    name: str
    prices_cents: tuple[tuple[int, int], ...]  # (size, price), in size order
    profit_split: Fraction
    code_discount_max: Fraction
    repeat_discounts: tuple[Fraction, ...]
    fee_refund_with_payout: int  # 0: none
    rule_sets: tuple[RuleSet, ...]  # stage order
    # the split ladder (section 1, 13 Nov 2026): from the payout after
    # `split_after_payouts` paid payouts with no confirmed case, this split;
    # 0 payouts: no rung. Scaling (`[scaling] split_after_step`) is the next.
    split_after_payouts: int = 0
    split_after_payouts_value: Fraction | None = None
    # D-13.6 retake: this share off the list price of one new account of the
    # same product and size after a breach (0: none); D-13.17: sizes on which
    # no discount code is valid
    retake_discount: Fraction = Fraction(0)
    code_free_sizes: tuple[int, ...] = ()

    def rule_set(self, stage: Stage) -> RuleSet | None:
        return next((r for r in self.rule_sets if r.stage is stage), None)


@dataclass(frozen=True, slots=True)
class AddonOffer:
    """One add-on as sold (D-13.18): its price as a share of the account's
    list price, and the products it may be bought with."""

    id: str
    price: Fraction
    products: frozenset[str]


@dataclass(frozen=True, slots=True)
class AddonsPolicy:
    """The add-ons (D-13.18) and the numbers their rule sets use. `off`:
    switched off (not sold); accounts that have one keep it. The defaults
    (no add-ons) are for policy files written before them."""

    offers: tuple[AddonOffer, ...] = ()
    split: Fraction = Fraction(9, 10)  # split90: the funded split
    swing_leverage: Fraction = Fraction(30)  # swing: at most 1:30
    swap_free_nights: int = 5  # swapfree: first nights of each position
    off: frozenset[str] = frozenset()

    def for_product(self, product_id: str) -> tuple[AddonOffer, ...]:
        """The add-ons offered with `product_id`, in id order (on or off)."""
        return tuple(o for o in self.offers if product_id in o.products)

    def offer(self, addon_id: str) -> AddonOffer | None:
        return next((o for o in self.offers if o.id == addon_id), None)


@dataclass(frozen=True, slots=True)
class ReferralsPolicy:
    """Referrals (D-14.15): a trader's code, entered by a new customer at
    registration, earns `commission` of what that customer pays for their
    first paid account, available after `holdback_days`. Nobody resident
    in `refused_countries` refers or is referred (C8). Off when the policy
    file has no [referrals]."""

    on: bool = False
    commission: Fraction = Fraction(1, 10)
    holdback_days: int = 30
    refused_countries: frozenset[str] = frozenset({"BD", "IN"})


@dataclass(frozen=True, slots=True)
class ClassesPolicy:
    """Daily trader classes (D-14.28; strategic review v3, D5). Off when
    the policy file has no [classes]. A: proven (`a_*` all met); C: watch
    (any `c_*` met, or a chargeback, or a trades link in an identity
    cluster); D: restricted (a case actioned within `d_case_days`); B:
    everyone else."""

    on: bool = False
    a_min_trading_days: int = 60
    a_min_trades: int = 200
    a_min_t_stat: Fraction = Fraction(2)
    a_min_profit_factor: Fraction = Fraction(13, 10)
    a_min_paid_payouts: int = 3
    a_max_short_percent: int = 20
    c_short_percent: int = 50
    short_seconds: int = 60
    d_case_days: int = 90


@dataclass(frozen=True, slots=True)
class DetectorsPolicy:
    """Behaviour detectors (D-14.29; strategic review v3, D6). Off when
    the policy file has no [detectors]. Hedging across accounts: opposite
    positions on one symbol on two accounts of one customer or one identity
    cluster, opened within `hedging_window_seconds` of each other, open
    together for at least `hedging_overlap_percent` of the shorter
    position's holding time, `hedging_matches` times in `hedging_days`.
    Gap trading (`gap_*`): a profitable position opened shortly before a
    long break and held through it, or opened near a release at an
    outsized volume. Consistency gaming (`reentry_*`): the same trade
    closed before a trading day boundary and opened again just after it;
    the payout's consistency check merges the two days whatever the
    count."""

    hedging: bool = False
    hedging_window_seconds: int = 120
    hedging_overlap_percent: int = 50
    hedging_matches: int = 3
    hedging_days: int = 30
    gap: bool = False
    gap_before_break_minutes: int = 120
    gap_release_minutes: int = 5
    gap_size_multiple: Fraction = Fraction(2)
    gap_days: int = 30
    reentry: bool = False
    reentry_seconds: int = 60
    reentry_matches: int = 3
    reentry_days: int = 30


@dataclass(frozen=True, slots=True)
class ReservePolicy:
    """Payout reserve (D-13.7; D-14.20): at each paid sale `sweep` of the
    price, by product, moves from operating cash to the reserve, and
    payouts are drawn from it. Coverage K = reserve / R*, where R* =
    certain + `open_weight` x open profit + `expected_weight` x expected
    on evaluations + the largest claim of one customer; the expected
    payout of an evaluation is its price x sweep / `sweep_factor` (the
    sweep is `sweep_factor` times the expected payout share). K at or
    above `amber_below` is normal, then amber, red below `red_below`,
    breach below `breach_below`. Off when the policy file has no
    [reserve]."""

    on: bool = False
    sweep: Mapping[str, Fraction] = field(default_factory=dict)  # by product id
    sweep_factor: Fraction = Fraction(5, 4)
    open_weight: Fraction = Fraction(1, 2)
    expected_weight: Fraction = Fraction(3, 2)
    amber_below: Fraction = Fraction(13, 10)
    red_below: Fraction = Fraction(11, 10)
    breach_below: Fraction = Fraction(1)
    release_above: Fraction = Fraction(13, 10)


@dataclass(frozen=True, slots=True)
class TrialPolicy:
    """The free trial (D-13.17): the product's first stage rules on one
    size, for `days` days, one per customer, never funded and never paid.
    Off when the policy file has no [trial]."""

    on: bool = False
    product_id: str = "lite"
    size_cents: int = 1_000_000
    days: int = 14


@dataclass(frozen=True, slots=True)
class CataloguePolicy:
    """What is on sale, and the limits on buying it (review D4, owner
    decisions K2 of 12 Nov 2026). Products and sizes switched off are not
    sold; accounts already bought keep their product and rules.

    - products_off, sizes_off: switched off (owners or admins change them
      under the two-person rule; the file holds the starting values);
    - one_instant_funding_per_trader: a product sold straight to a funded
      account (no evaluation stage) may be held once per trader at a time;
    - unpaid_hold_hours: an order not yet paid counts against the
      allocation cap for this long, so two invoices cannot both pass it.
    The allocation cap itself is `scaling.max_allocation_cents`, the figure
    the contract and the website state."""

    products_off: frozenset[str] = frozenset()
    sizes_off: frozenset[int] = frozenset()
    one_instant_funding_per_trader: bool = False
    unpaid_hold_hours: int = 24

    def on_sale(self, product: ProductPolicy) -> bool:
        return product.id not in self.products_off and bool(self.sizes(product))

    def sizes(self, product: ProductPolicy) -> tuple[tuple[int, int], ...]:
        """(size, price) of the product's sizes on sale."""
        return tuple((s, p) for s, p in product.prices_cents if s not in self.sizes_off)


def instant_funding(product: ProductPolicy) -> bool:
    """A product sold straight to a funded account (no evaluation)."""
    return product.rule_sets[0].stage is Stage.FUNDED


PAYOUT_BASES = ("closed_balance", "equity")
_TEMPLATE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
DISCLOSURES = ("simulated", "live")


@dataclass(frozen=True, slots=True)
class ContractPolicy:
    """The funded-trader agreement (outline M18). Empty text = not decided
    yet: the contract is then issued only as a sandbox draft."""

    template: str  # config/contracts/<template>.txt
    firm_name: str  # the contracting company (C14)
    governing_law: str  # counsel
    account_disclosure: str  # D1: "simulated" or "live"

    @property
    def complete(self) -> bool:
        return bool(self.firm_name and self.governing_law)


@dataclass(frozen=True, slots=True)
class CasePolicy:
    """Service levels of integrity, fraud and compliance cases: hours from an
    alert to its case's due time, by severity."""

    sla_hours: Mapping[str, int]  # low, medium, high

    def due(self, severity: str, ts: int) -> int:
        return ts + self.sla_hours[severity] * 3_600_000


@dataclass(frozen=True, slots=True)
class Policy:
    access: AccessPolicy
    news: NewsPolicy
    payouts: PayoutPolicy
    scaling: ScalingPolicy
    trading: TradingPolicy
    products: tuple[ProductPolicy, ...]
    contract: ContractPolicy
    cases: CasePolicy
    staff: StaffPolicy = field(default_factory=StaffPolicy)
    catalogue: CataloguePolicy = field(default_factory=CataloguePolicy)
    addons: AddonsPolicy = field(default_factory=AddonsPolicy)
    trial: TrialPolicy = field(default_factory=TrialPolicy)
    referrals: ReferralsPolicy = field(default_factory=ReferralsPolicy)
    reserve: ReservePolicy = field(default_factory=ReservePolicy)
    classes: ClassesPolicy = field(default_factory=ClassesPolicy)
    detectors: DetectorsPolicy = field(default_factory=DetectorsPolicy)

    def rule_sets(self) -> tuple[RuleSet, ...]:
        """Each product's stages, in order."""
        return tuple(r for p in self.products for r in p.rule_sets)

    def published_rule_sets(self) -> tuple[RuleSet, ...]:
        """Every rule set the engine knows: each stage's, then, for each,
        one derived rule set per combination of the product's add-ons."""
        out = []
        for p in self.products:
            offered = [o.id for o in self.addons.for_product(p.id)]
            combos = [
                tuple(a for i, a in enumerate(offered) if mask >> i & 1)
                for mask in range(1, 1 << len(offered))
            ]
            for r in p.rule_sets:
                out.append(r)
                out.extend(self.with_addons(r, c) for c in combos)
        if self.trial.on:
            out.append(self.trial_rule_set())
        return tuple(out)

    def trial_rule_set(self) -> RuleSet:
        """The free trial's rule set: the trial product's first stage."""
        first = self.product(self.trial.product_id).rule_sets[0]
        return replace(first, rsv_id=trial_id(first.rsv_id))

    def with_addons(self, rules: RuleSet, addons: tuple[str, ...]) -> RuleSet:
        """`rules` with `addons`, by this policy's add-on numbers."""
        a = self.addons
        return derive(rules, addons, a.swing_leverage, a.swap_free_nights)

    def product(self, product_id: str) -> ProductPolicy:
        for p in self.products:
            if p.id == product_id:
                return p
        raise KeyError(f"unknown product {product_id!r}")


class PolicySource(Protocol):
    async def current(self) -> Policy: ...


class FixedPolicy:
    """A policy that does not change (tests; the file without admin settings)."""

    def __init__(self, policy: Policy) -> None:
        self._policy = policy

    async def current(self) -> Policy:
        return self._policy


# --- exact percentages and country codes -----------------------------------------


def parse_percent(value: object, where: str, optional: bool = False) -> Fraction | None:
    """A percentage written as text ("6", "3.5") as an exact share (3/50,
    7/200); "" is no value when `optional`. Anything else is a PolicyError."""
    if optional and value == "":
        return None
    if not isinstance(value, str) or not _PERCENT.fullmatch(value):
        raise PolicyError(f'{where} must be a percentage written as text, e.g. "6" or "3.5"')
    return Fraction(value) / 100


def percent_text(share: Fraction | None) -> str | None:
    """A share as exact percentage text: 7/200 -> "3.5"; never a float. A
    share that is not a finite decimal stays a fraction ("1/3")."""
    if share is None:
        return None
    value = share * 100
    for digits in range(13):
        scaled = value * 10**digits
        if scaled.denominator == 1:
            if digits == 0:
                return str(scaled.numerator)
            sign = "-" if scaled < 0 else ""
            text = str(abs(scaled.numerator)).rjust(digits + 1, "0")
            return f"{sign}{text[:-digits]}.{text[-digits:]}"
    return f"{value.numerator}/{value.denominator}"


_RSV_STAGE = re.compile(
    r"-(s1|s2|funded)-v[0-9]+(?:-a[0-9]+)?(?:-(?:split90|swing|swapfree))*(?:-trial)?$"
)


def stage_of(rsv_id: str) -> Stage:
    """The stage a rule-set id names: every id is `<product>-<stage>-v<n>`,
    with `-a<change>` for an admin change (`one-step-s1-v2-a7` -> s1)."""
    found = _RSV_STAGE.search(rsv_id)
    if found is None:
        raise ValueError(f"{rsv_id!r} is not a rule-set id")
    return Stage(found.group(1))


def rule_set_view(r: RuleSet) -> dict[str, Any]:
    """A stage's numbers as shown to everyone: percentages as exact text."""
    return {
        "stage": r.stage.value,
        "rsv_id": r.rsv_id,
        "profit_target_percent": percent_text(r.profit_target),
        "max_loss_percent": percent_text(r.max_loss),
        "max_loss_kind": r.max_loss_kind.value,
        "daily_loss_percent": percent_text(r.daily_loss),
        "min_trading_days": r.min_trading_days,
        "consistency_percent": percent_text(r.consistency),
        "consistency_at_pass": r.consistency_at_pass,
        "target_basis": r.target_basis.value,  # "closed_balance" or "equity"
        "flat_to_pass": r.flat_to_pass,  # no open position or pending order to pass
        "tie_rule": r.tie_rule.value,  # a breach and a pass on the same check
        "news_restricted": r.news_restricted,  # restricted news windows bind this stage
        "addons": list(addons_of(r.rsv_id)),  # D-13.18
        "leverage_cap": None if r.leverage_cap is None else str(r.leverage_cap),  # 30: 1:30
        "swap_free_nights": r.swap_free_nights,
        # trailing loss: a payout restarts the floor below the new balance
        "floor_restarts_after_payout": r.floor_restarts_after_payout,
        # trailing loss: the floor stops rising at the starting balance
        "floor_locks_at_start": r.floor_locks_at_start,
        # funded Standard: flat shortly before a market break of 2 hours or more
        "flat_before_breaks": r.flat_before_breaks,
        "news_breaches": r.news_breaches,  # a fill in a news window ends the account
    }


def parse_countries(values: object, where: str) -> frozenset[str]:
    """ISO 3166-1 alpha-2 codes ("BD")."""
    if not isinstance(values, (list, tuple)) or not all(isinstance(v, str) for v in values):
        raise PolicyError(f"{where} must be a list of text values")
    for code in values:
        if not _COUNTRY.fullmatch(code):
            raise PolicyError(f"{where}: {code!r} is not ISO alpha-2")
    return frozenset(values)


# --- reading ---------------------------------------------------------------------


class _Table:
    """One TOML table, read strictly: every key must be used exactly once."""

    def __init__(self, data: object, where: str) -> None:
        if not isinstance(data, dict):
            raise PolicyError(f"{where} must be a table")
        self._data: dict[str, Any] = data
        self._where = where
        self._used: set[str] = set()

    def _get(self, key: str) -> Any:
        if key not in self._data:
            raise PolicyError(f"{self._where}.{key} is missing")
        self._used.add(key)
        return self._data[key]

    def name(self, key: str) -> str:
        return f"{self._where}.{key}"

    def has(self, key: str) -> bool:
        """Whether an optional key (added after policy files existed) is set."""
        return key in self._data

    def table(self, key: str) -> _Table:
        return _Table(self._get(key), self.name(key))

    def tables(self, key: str) -> list[_Table]:
        value = self._get(key)
        if not isinstance(value, list) or not value:
            raise PolicyError(f"{self.name(key)} must be a non-empty list of tables")
        return [_Table(v, f"{self.name(key)}[{i}]") for i, v in enumerate(value)]

    def optional_boolean(self, key: str, default: bool) -> bool:
        """A switch that may be left out (`default` then); added after
        policy files existed."""
        return self.boolean(key) if key in self._data else default

    def boolean(self, key: str) -> bool:
        value = self._get(key)
        if not isinstance(value, bool):
            raise PolicyError(f"{self.name(key)} must be true or false")
        return value

    def integer(self, key: str, minimum: int = 0) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise PolicyError(f"{self.name(key)} must be a whole number >= {minimum}")
        return value

    def text(self, key: str, *, allow_empty: bool = False) -> str:
        value = self._get(key)
        if not isinstance(value, str) or value != value.strip() or not (value or allow_empty):
            raise PolicyError(f"{self.name(key)} must be {'' if allow_empty else 'non-empty '}text")
        return value

    def percent(self, key: str, *, optional: bool = False) -> Fraction | None:
        value = self._get(key)
        return parse_percent(value, self.name(key), optional)

    def required_percent(self, key: str) -> Fraction:
        value = self.percent(key)
        assert value is not None
        return value

    def ratio(self, key: str) -> Fraction:
        """A plain decimal written as text, e.g. "3.33" -> 333/100."""
        value = self._get(key)
        if not isinstance(value, str) or not _PERCENT.fullmatch(value):
            raise PolicyError(f'{self.name(key)} must be a decimal written as text, e.g. "1.5"')
        return Fraction(value)

    def integers(self, key: str, minimum: int = 0) -> tuple[int, ...]:
        value = self._get(key)
        if not isinstance(value, list) or not all(
            isinstance(v, int) and not isinstance(v, bool) and v >= minimum for v in value
        ):
            raise PolicyError(f"{self.name(key)} must be a list of whole numbers >= {minimum}")
        return tuple(value)

    def texts(self, key: str) -> tuple[str, ...]:
        value = self._get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise PolicyError(f"{self.name(key)} must be a list of text values")
        return tuple(value)

    def mapping(self, key: str) -> dict[str, Any]:
        value = self._get(key)
        if not isinstance(value, dict):
            raise PolicyError(f"{self.name(key)} must be a table")
        return dict(value)

    def done(self) -> None:
        extra = set(self._data) - self._used
        if extra:
            raise PolicyError(f"{self._where}: unknown keys {sorted(extra)}")


def load_policy(path: Path = DEFAULT_POLICY) -> Policy:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"{path}: {exc}") from exc
    return parse_policy(data)


def parse_policy(data: Mapping[str, Any]) -> Policy:
    root = _Table(dict(data), "policy")
    try:
        policy = Policy(
            access=_access(root.table("access")),
            news=_news(root.table("news")),
            payouts=_payouts(root.table("payouts")),
            scaling=_scaling(root.table("scaling")),
            trading=_trading(root.table("trading")),
            products=tuple(_product(t) for t in root.tables("products")),
            contract=_contract(root.table("contract")),
            cases=_cases(root.table("cases")),
            staff=_staff(root.table("staff")),
            catalogue=_catalogue(root.table("catalogue")),
            addons=_addons(root.table("addons")) if root.has("addons") else AddonsPolicy(),
            trial=_trial(root.table("trial")) if root.has("trial") else TrialPolicy(),
            referrals=(
                _referrals(root.table("referrals")) if root.has("referrals") else ReferralsPolicy()
            ),
            reserve=_reserve(root.table("reserve")) if root.has("reserve") else ReservePolicy(),
            classes=_classes(root.table("classes")) if root.has("classes") else ClassesPolicy(),
            detectors=(
                _detectors(root.table("detectors")) if root.has("detectors") else DetectorsPolicy()
            ),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, PolicyError):
            raise
        raise PolicyError(str(exc)) from exc
    root.done()
    _check_locked(policy)
    ids = [p.id for p in policy.products]
    if len(set(ids)) != len(ids):
        raise PolicyError("product ids must be unique")
    check_catalogue(policy, policy.catalogue.products_off, policy.catalogue.sizes_off)
    trial = policy.trial
    if trial.on and (
        trial.product_id not in ids
        or trial.size_cents not in dict(policy.product(trial.product_id).prices_cents)
    ):
        raise PolicyError("trial: product and size_cents must be one of the product's sizes")
    for offer in policy.addons.offers:
        if unknown := sorted(offer.products - set(ids)):
            raise PolicyError(f"addons.{offer.id}: unknown products {unknown}")
    if policy.reserve.on:
        on_sale = {p.id for p in policy.products if policy.catalogue.on_sale(p)}
        if missing := sorted(on_sale - set(policy.reserve.sweep)):
            raise PolicyError(f"reserve.sweep_percent: no share for products on sale {missing}")
        if unknown := sorted(set(policy.reserve.sweep) - set(ids)):
            raise PolicyError(f"reserve.sweep_percent: unknown products {unknown}")
    refunds = [c for p in policy.products if p.fee_refund_with_payout for _, c in p.prices_cents]
    if policy.payouts.maximum_cents and policy.payouts.maximum_cents < max(refunds, default=0):
        # the fee refund is paid with a payout and counts towards its maximum
        raise PolicyError("payouts.maximum_cents must be 0 or at least every refundable fee")
    return policy


def _catalogue(t: _Table) -> CataloguePolicy:
    off = t.texts("products_off")
    sizes = t.integers("sizes_off", 1)
    policy = CataloguePolicy(
        products_off=frozenset(off),
        sizes_off=frozenset(sizes),
        one_instant_funding_per_trader=t.boolean("one_instant_funding_per_trader"),
        unpaid_hold_hours=t.integer("unpaid_hold_hours", 1),
    )
    t.done()
    if len(set(off)) != len(off) or len(set(sizes)) != len(sizes):
        raise PolicyError("catalogue: a product or size is listed twice")
    return policy


def _referrals(t: _Table) -> ReferralsPolicy:
    policy = ReferralsPolicy(
        on=t.boolean("on"),
        commission=t.required_percent("commission_percent"),
        holdback_days=t.integer("holdback_days", 0),
        refused_countries=parse_countries(
            t.texts("refused_countries"), t.name("refused_countries")
        ),
    )
    t.done()
    if not 0 < policy.commission < 1:
        raise PolicyError("referrals: commission_percent between 0 and 100")
    return policy


def _detectors(t: _Table) -> DetectorsPolicy:
    policy = DetectorsPolicy(
        hedging=t.boolean("hedging"),
        hedging_window_seconds=t.integer("hedging_window_seconds", 1),
        hedging_overlap_percent=t.integer("hedging_overlap_percent", 1),
        hedging_matches=t.integer("hedging_matches", 1),
        hedging_days=t.integer("hedging_days", 1),
        gap=t.boolean("gap"),
        gap_before_break_minutes=t.integer("gap_before_break_minutes", 1),
        gap_release_minutes=t.integer("gap_release_minutes", 1),
        gap_size_multiple=t.ratio("gap_size_multiple"),
        gap_days=t.integer("gap_days", 1),
        reentry=t.boolean("reentry"),
        reentry_seconds=t.integer("reentry_seconds", 1),
        reentry_matches=t.integer("reentry_matches", 1),
        reentry_days=t.integer("reentry_days", 1),
    )
    t.done()
    if policy.hedging_overlap_percent > 100:
        raise PolicyError("detectors: hedging_overlap_percent from 1 to 100")
    if policy.gap_size_multiple < 1:
        raise PolicyError("detectors: gap_size_multiple at least 1")
    return policy


def _classes(t: _Table) -> ClassesPolicy:
    policy = ClassesPolicy(
        on=t.boolean("on"),
        a_min_trading_days=t.integer("a_min_trading_days", 1),
        a_min_trades=t.integer("a_min_trades", 1),
        a_min_t_stat=t.ratio("a_min_t_stat"),
        a_min_profit_factor=t.ratio("a_min_profit_factor"),
        a_min_paid_payouts=t.integer("a_min_paid_payouts"),
        a_max_short_percent=t.integer("a_max_short_percent"),
        c_short_percent=t.integer("c_short_percent", 1),
        short_seconds=t.integer("short_seconds", 1),
        d_case_days=t.integer("d_case_days", 1),
    )
    t.done()
    if not 0 <= policy.a_max_short_percent < policy.c_short_percent <= 100:
        raise PolicyError("classes: 0 <= a_max_short_percent < c_short_percent <= 100")
    if policy.a_min_profit_factor <= 1 or policy.a_min_t_stat <= 0:
        raise PolicyError("classes: a_min_profit_factor above 1, a_min_t_stat above 0")
    return policy


def _reserve(t: _Table) -> ReservePolicy:
    shares = t.mapping("sweep_percent")
    where = t.name("sweep_percent")
    sweep: dict[str, Fraction] = {}
    for product_id, value in shares.items():
        share = parse_percent(value, f"{where}.{product_id}")
        assert share is not None
        if not 0 < share <= 1:
            raise PolicyError(f"{where}.{product_id}: between 0 (excluded) and 100")
        sweep[str(product_id)] = share
    policy = ReservePolicy(
        on=t.boolean("on"),
        sweep=sweep,
        sweep_factor=t.ratio("sweep_factor"),
        open_weight=t.ratio("open_weight"),
        expected_weight=t.ratio("expected_weight"),
        amber_below=t.ratio("amber_below"),
        red_below=t.ratio("red_below"),
        breach_below=t.ratio("breach_below"),
        release_above=t.ratio("release_above"),
    )
    t.done()
    if policy.sweep_factor < 1:
        raise PolicyError("reserve.sweep_factor: at least 1")
    if not policy.amber_below >= policy.red_below >= policy.breach_below > 0:
        raise PolicyError("reserve: amber_below >= red_below >= breach_below > 0")
    if policy.release_above < policy.amber_below:
        raise PolicyError("reserve.release_above: at least amber_below")
    return policy


def _trial(t: _Table) -> TrialPolicy:
    trial = TrialPolicy(
        on=t.boolean("on"),
        product_id=t.text("product"),
        size_cents=t.integer("size_cents", 1),
        days=t.integer("days", 1),
    )
    t.done()
    return trial


def _addons(t: _Table) -> AddonsPolicy:
    offers = []
    for addon_id in ADDON_IDS:
        if not t.has(addon_id):
            continue
        o = t.table(addon_id)
        offers.append(
            AddonOffer(
                id=addon_id,
                price=o.required_percent("price_percent"),
                products=frozenset(o.texts("products")),
            )
        )
        o.done()
    policy = AddonsPolicy(
        offers=tuple(offers),
        split=t.required_percent("split_percent"),
        swing_leverage=Fraction(t.integer("swing_leverage", 1)),
        swap_free_nights=t.integer("swap_free_nights", 1),
        off=frozenset(t.texts("off")),
    )
    t.done()
    if any(not 0 < o.price < 1 or not o.products for o in policy.offers):
        raise PolicyError("addons: each price_percent between 0 and 100, with products")
    if not 0 < policy.split <= 1 or not policy.off <= {o.id for o in policy.offers}:
        raise PolicyError("addons: split_percent up to 100; off names offered add-ons")
    return policy


def all_sizes(policy: Policy) -> tuple[int, ...]:
    """Every account size any product has a price for, ascending."""
    return tuple(sorted({s for p in policy.products for s, _ in p.prices_cents}))


def check_catalogue(
    policy: Policy, products_off: frozenset[str], sizes_off: frozenset[int]
) -> None:
    """PolicyError unless every product and size switched off exists."""
    ids = {p.id for p in policy.products}
    if unknown := sorted(products_off - ids):
        raise PolicyError(f"catalogue: unknown products {unknown}")
    if unknown_sizes := sorted(sizes_off - set(all_sizes(policy))):
        raise PolicyError(f"catalogue: no product has the sizes {unknown_sizes} (cents)")


@dataclass(frozen=True, slots=True)
class Switch:
    """A security switch (rule AC16): its place in the policy file, the
    value it is locked at, and what it does in plain words."""

    name: str
    locked_at: bool
    explains: str


# Rule AC16 [decided 8 Nov 2026]: in production these cannot be switched off
# (or, for staff trading, on); the policy file refuses any other value. The
# two-person rule on settings (AC00) is locked the same way, in `_staff`.
LOCKED_SWITCHES: tuple[Switch, ...] = (
    Switch(
        "payouts.require_mfa",
        True,
        "A trader must have signed in with a second factor to set a payout wallet or ask "
        "for a payout.",
    ),
    Switch(
        "staff.login_needs_mfa",
        True,
        "Every staff and admin login needs a second factor (one-time code or passkey).",
    ),
    Switch(
        "staff.approvals_need_mfa",
        True,
        "Every approval needs a second factor entered a few minutes before.",
    ),
    Switch(
        "staff.screening_required",
        True,
        "No staff access without a current pre-employment screening approval.",
    ),
    Switch(
        "staff.settings_need_second_admin",
        True,
        "Every settings change is a proposal until a second person approves it.",
    ),
    Switch(
        "staff.staff_may_trade",
        False,
        "Staff and their households never buy challenges or trade.",
    ),
)


def switch_value(policy: Policy, name: str) -> bool:
    table, field_name = name.split(".")
    value = getattr(getattr(policy, table), field_name)
    assert isinstance(value, bool)
    return value


def _check_locked(policy: Policy) -> None:
    for switch in LOCKED_SWITCHES:
        if switch_value(policy, switch.name) is not switch.locked_at:
            wanted = "true" if switch.locked_at else "false"
            raise PolicyError(f"{switch.name} is locked at {wanted} (rule AC16)")


def _cases(t: _Table) -> CasePolicy:
    hours = t.table("sla_hours")
    sla = {level: hours.integer(level, 1) for level in ("low", "medium", "high")}
    hours.done()
    t.done()
    if not sla["low"] >= sla["medium"] >= sla["high"]:
        raise PolicyError(f"{t.name('sla_hours')}: higher severity needs a shorter time")
    return CasePolicy(sla)


def _contract(t: _Table) -> ContractPolicy:
    policy = ContractPolicy(
        template=t.text("template"),
        firm_name=t.text("firm_name", allow_empty=True),
        governing_law=t.text("governing_law", allow_empty=True),
        account_disclosure=t.text("account_disclosure"),
    )
    t.done()
    if not _TEMPLATE_ID.fullmatch(policy.template):
        raise PolicyError(f"{t.name('template')} must match {_TEMPLATE_ID.pattern}")
    if policy.account_disclosure not in DISCLOSURES:
        raise PolicyError(f"{t.name('account_disclosure')} must be one of {DISCLOSURES}")
    return policy


def _access(t: _Table) -> AccessPolicy:
    gating = t.boolean("country_gating")
    countries = parse_countries(t.texts("blocked_countries"), t.name("blocked_countries"))
    min_age = t.integer("min_age", 1)
    t.done()
    return AccessPolicy(gating, countries, min_age)


def _news(t: _Table) -> NewsPolicy:
    policy = NewsPolicy(t.integer("before_minutes") * 60_000, t.integer("after_minutes") * 60_000)
    t.done()
    return policy


def _payouts(t: _Table) -> PayoutPolicy:
    policy = PayoutPolicy(
        minimum_cents=t.integer("minimum_cents", 1),
        first_after_days=t.integer("first_after_days"),
        interval_days=t.integer("interval_days", 1),
        approvals_required=t.integer("approvals_required", 1),
        basis=t.text("basis"),
        flat_required=t.boolean("flat_required"),
        min_trading_days=t.integer("min_trading_days"),
        maximum_cents=t.integer("maximum_cents"),
        trading_during_payout=t.text("trading_during_payout"),
        wallet_cooling_off_hours=t.integer("wallet_cooling_off_hours"),
        require_mfa=t.boolean("require_mfa"),
        step_up_minutes=t.integer("step_up_minutes"),
        approval=t.text("approval"),
        short_hold_seconds=t.integer("short_hold_seconds", 1)
        if t.has("short_hold_seconds")
        else 60,
        short_hold_share_percent=(
            t.integer("short_hold_share_percent") if t.has("short_hold_share_percent") else 0
        ),
        networks=t.texts("networks") if t.has("networks") else ("TRC20",),
    )
    t.done()
    built = PAYOUT_NETWORKS
    if not policy.networks or len(set(policy.networks)) != len(policy.networks):
        raise PolicyError(f"{t.name('networks')}: one or more networks, each once")
    if unknown := [n for n in policy.networks if n not in built]:
        raise PolicyError(f"{t.name('networks')}: not built {unknown}; built: {list(built)}")
    if policy.short_hold_share_percent > 100:
        raise PolicyError(f"{t.name('short_hold_share_percent')} must be 0 to 100")
    if policy.approval not in PAYOUT_APPROVALS:
        raise PolicyError(f"{t.name('approval')} must be one of {PAYOUT_APPROVALS}")
    if policy.approval == "pass_authorise" and policy.approvals_required != 2:
        raise PolicyError(f"{t.name('approvals_required')} is 2 with pass_authorise")
    if policy.basis not in PAYOUT_BASES:
        raise PolicyError(f"{t.name('basis')} must be one of {PAYOUT_BASES}")
    if policy.trading_during_payout not in ("paused", "allowed"):
        raise PolicyError(f"{t.name('trading_during_payout')} must be paused or allowed")
    if policy.maximum_cents and policy.maximum_cents < policy.minimum_cents:
        raise PolicyError(f"{t.name('maximum_cents')} must be 0 or >= minimum_cents")
    return policy


def _staff(t: _Table) -> StaffPolicy:
    limits = Limits(
        apprentice_min_months=t.integer("apprentice_min_months", 1),
        apprentice_max_months=t.integer("apprentice_max_months", 1),
        power_max_days=t.integer("power_max_days", 1),
        acting_max_days=t.integer("acting_max_days", 1),
        sections_approval=t.text("sections_approval"),
        home_proposer_min_level=t.integer("home_proposer_min_level"),
    )
    if limits.sections_approval not in SECTIONS_APPROVALS:
        raise PolicyError(f"{t.name('sections_approval')} must be one of {SECTIONS_APPROVALS}")
    if not 0 <= limits.home_proposer_min_level <= 3:  # Apprentice to Lead
        raise PolicyError(f"{t.name('home_proposer_min_level')} must be 0 to 3")
    policy = StaffPolicy(
        approvals_need_mfa=t.boolean("approvals_need_mfa"),
        step_up_minutes=t.integer("step_up_minutes"),
        limits=limits,
        login_needs_mfa=t.boolean("login_needs_mfa"),
        staff_may_trade=t.boolean("staff_may_trade"),
        settings_need_second_admin=t.boolean("settings_need_second_admin"),
        proposal_expiry_hours=t.integer("proposal_expiry_hours", 1),
        stand_in_max_days=t.integer("stand_in_max_days", 1),
        leaver_cutoff_alert_minutes=t.integer("leaver_cutoff_alert_minutes", 1),
        leaver_cutoff_max_hours=t.integer("leaver_cutoff_max_hours", 1),
        training_gate=t.boolean("training_gate"),
        training_valid_months=t.integer("training_valid_months", 1),
        screening_required=t.boolean("screening_required"),
        screening_valid_months=t.integer("screening_valid_months", 1),
        screening_due_days=t.integer("screening_due_days", 1),
        escalate_hours=t.integer("escalate_hours", 1),
        dormant_days=t.integer("dormant_days", 1),
        review_days_lead=t.integer("review_days_lead", 1),
        review_days_admin=t.integer("review_days_admin", 1),
        office_timezone=t.text("office_timezone"),
        office_days=t.integers("office_days"),
        office_start_hour=t.integer("office_start_hour"),
        office_end_hour=t.integer("office_end_hour", 1),
        pair_threshold=t.integer("pair_threshold", 2),
    )
    t.done()
    if not policy.settings_need_second_admin:
        # rule AC00 [decided 8 Nov 2026]: every decision needs two people
        raise PolicyError(f"{t.name('settings_need_second_admin')} cannot be false (rule AC00)")
    if limits.apprentice_min_months > limits.apprentice_max_months:
        raise PolicyError(f"{t.name('apprentice_min_months')} must not exceed the maximum")
    try:
        ZoneInfo(policy.office_timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise PolicyError(f"{t.name('office_timezone')}: an IANA time zone") from exc
    if not set(policy.office_days) <= set(range(7)) or not policy.office_days:
        raise PolicyError(f"{t.name('office_days')}: weekdays 0 (Monday) to 6 (Sunday)")
    if not 0 <= policy.office_start_hour < policy.office_end_hour <= 24:
        raise PolicyError(f"{t.name('office_start_hour')} must be before the end hour")
    return policy


def _scaling(t: _Table) -> ScalingPolicy:
    policy = ScalingPolicy(
        review_days=t.integer("review_days", 1),
        min_net_profit=t.required_percent("min_net_profit_percent"),
        min_paid_payouts=t.integer("min_paid_payouts"),
        step=t.required_percent("step_percent"),
        split_after_step=t.integer("split_after_step", 1),
        split_after=t.required_percent("split_percent_after"),
        max_allocation_cents=t.integer("max_allocation_cents", 1),
    )
    t.done()
    if not 0 < policy.split_after <= 1:
        raise PolicyError("scaling.split_percent_after must be in (0, 100]")
    return policy


def _trading(t: _Table) -> TradingPolicy:
    config_version = t.text("config_version")
    delay = t.integer("delay_threshold_ms")
    gap = t.integer("gap_threshold_ms", 1)
    by_symbol = t.mapping("gap_threshold_ms_by_symbol")
    quote_age = t.integer("quote_age_ms")
    age_by_symbol = t.mapping("quote_age_ms_by_symbol")
    sanity_t = t.table("sanity")
    sanity = (
        sanity_t.integer("multiple"),
        sanity_t.integer("min_basis_points"),
        sanity_t.integer("min_changes", 1),
    )
    sanity_t.done()
    leverage_t, caps_t, spread_t = t.table("leverage"), t.table("lot_caps"), t.table("spread")
    commission_t = t.table("commission")
    swaps = _swaps(t.table("swaps")) if t.has("swaps") else None
    hours = _hours(t.table("hours")) if t.has("hours") else None
    t.done()
    commission = {c: commission_t.required_percent(c.value) for c in AssetClass}
    commission_t.done()
    leverage = {c: leverage_t.ratio(c.value) for c in AssetClass}
    leverage_t.done()
    caps: dict[AssetClass, tuple[int, int]] = {}
    for c in AssetClass:
        pair = caps_t.integers(c.value, 1)
        if len(pair) != 2:
            raise PolicyError(f"{caps_t.name(c.value)} must be [per order, per symbol]")
        caps[c] = (pair[0], pair[1])
    caps_t.done()
    window = spread_t.integer("window", 1)
    min_samples = spread_t.integer("min_samples", 1)
    vol = spread_t.ratio("vol_multiplier")
    news = spread_t.integer("news_multiplier", 1)
    max_multiple = spread_t.integer("max_multiple", 1)
    rollover = (
        spread_t.integer("rollover_multiplier", 1),
        spread_t.integer("rollover_before_minutes") * 60_000,
        spread_t.integer("rollover_after_minutes") * 60_000,
    )
    mins_t = spread_t.table("min_points")
    spread_t.done()
    instruments: list[InstrumentSpec] = []
    for symbol in LAUNCH_SYMBOLS:
        base = default_instrument(symbol)
        order_cap, symbol_cap = caps[symbol.asset_class]
        instruments.append(
            replace(
                base,
                leverage=leverage[symbol.asset_class],
                max_order_volume_per_100k=order_cap,
                max_symbol_volume_per_100k=symbol_cap,
                spread=_pricing(
                    _market_spread(
                        mins_t.integer(symbol.name, 1),
                        max_multiple,
                        window,
                        min_samples,
                        vol,
                        news,
                        rollover,
                    ),
                    commission[symbol.asset_class],
                    commission_t.name(symbol.asset_class.value),
                ),
                swap=None if swaps is None else swap_terms(symbol, swaps),
                hours=None if hours is None else hours[symbol.asset_class],
            )
        )
    mins_t.done()
    names = {s.name for s in LAUNCH_SYMBOLS}
    for key, table in (
        ("gap_threshold_ms_by_symbol", by_symbol),
        ("quote_age_ms_by_symbol", age_by_symbol),
    ):
        for name, ms in table.items():
            if name not in names:
                raise PolicyError(f"trading.{key}: unknown symbol {name!r}")
            if isinstance(ms, bool) or not isinstance(ms, int) or ms < 1:
                raise PolicyError(f"trading.{key}.{name} must be >= 1")
    guard = GuardConfig(
        delay,
        gap,
        dict(sorted(by_symbol.items())) or None,
        default_quote_age_ms=quote_age,
        quote_age_ms=dict(sorted(age_by_symbol.items())) or None,
        sanity_multiple=sanity[0],
        sanity_min_bp=sanity[1],
        sanity_min_changes=sanity[2],
    )
    return TradingPolicy(config_version, tuple(instruments), guard)


def _hours(t: _Table) -> dict[AssetClass, TradingHours]:
    """[trading.hours]: each asset class's schedule in New York time, with
    the dated closures that apply to it (`propfirm.trading.hours`)."""
    closures: dict[AssetClass, list[tuple[int, int]]] = {c: [] for c in AssetClass}
    for closure in t.tables("closures") if t.has("closures") else []:
        try:
            span = (new_york_ms(closure.text("from")), new_york_ms(closure.text("to")))
            classes = [AssetClass(c) for c in closure.texts("classes")]
        except ValueError as exc:
            raise PolicyError(f"{t.name('closures')}: {exc}") from exc
        closure.done()
        for c in classes:
            closures[c].append(span)
    flat_before = (
        t.integer("flat_before_break_minutes") if t.has("flat_before_break_minutes") else 0
    )
    long_break = t.integer("long_break_minutes", 1) if t.has("long_break_minutes") else 120
    out: dict[AssetClass, TradingHours] = {}
    for c in AssetClass:
        table = t.table(c.value)
        try:
            if table.boolean("always"):
                out[c] = replace(
                    ALWAYS,
                    closures=tuple(sorted(closures[c])),
                    flat_before_minutes=flat_before,
                    long_break_minutes=long_break,
                )
            else:
                daily = table.text("daily_break", allow_empty=True)
                start, _, end = daily.partition("-")
                out[c] = TradingHours(
                    open_minute=week_minute(table.text("open")),
                    close_minute=week_minute(table.text("close")),
                    break_start=day_minute(start) if daily else None,
                    break_end=day_minute(end) if daily else None,
                    closures=tuple(sorted(closures[c])),
                    flat_before_minutes=flat_before,
                    long_break_minutes=long_break,
                )
        except ValueError as exc:
            if isinstance(exc, PolicyError):
                raise
            raise PolicyError(f"{t.name(c.value)}: {exc}") from exc
        table.done()
    t.done()
    return out


def _swaps(t: _Table) -> SwapSettings:
    """[trading.swaps]: the owner settings each symbol's swap is worked out
    from (`propfirm.trading.swaps`)."""
    rates_t = t.table("rates_percent")
    rates = {code: rates_t.required_percent(code) for code in CURRENCIES}
    rates_t.done()
    try:
        settings = SwapSettings(
            rates=rates,
            markup=t.required_percent("markup_percent"),
            metal_markup=t.required_percent("metal_markup_percent"),
            oil_long=t.required_percent("oil_long_percent"),
            crypto=t.required_percent("crypto_percent"),
        )
    except ValueError as exc:
        raise PolicyError(f"{t.name('rates_percent')}: {exc}") from exc
    t.done()
    return settings


def _pricing(spread: MarketSpreadConfig, commission: Fraction, where: str) -> Pricing:
    """The spread alone, or with a commission when the asset class has one."""
    if commission == 0:
        return spread
    try:
        return SpreadAndCommission(spread, commission)
    except ValueError as exc:
        raise PolicyError(f"{where}: {exc}") from exc


def _market_spread(
    min_points: int,
    max_multiple: int,
    window: int,
    min_samples: int,
    vol: Fraction,
    news: int,
    rollover: tuple[int, int, int],
) -> MarketSpreadConfig:
    try:
        return MarketSpreadConfig(
            min_points=min_points,
            max_points=min_points * max_multiple,
            window=window,
            min_samples=min_samples,
            vol_multiplier=vol,
            news_multiplier=news,
            rollover_multiplier=rollover[0],
            rollover_before_ms=rollover[1],
            rollover_after_ms=rollover[2],
        )
    except ValueError as exc:
        raise PolicyError(f"trading.spread: {exc}") from exc


def _product(t: _Table) -> ProductPolicy:
    product_id = t.text("id")
    if not _PRODUCT_ID.fullmatch(product_id):
        raise PolicyError(f"{t.name('id')} must match {_PRODUCT_ID.pattern}")
    name = t.text("name")
    sizes = t.integers("sizes_cents", 1)
    prices = t.integers("prices_cents", 1)
    if not sizes or len(sizes) != len(prices) or list(sizes) != sorted(set(sizes)):
        raise PolicyError(f"{t.name('sizes_cents')}: one price per size, sizes ascending")
    split = t.required_percent("profit_split_percent")
    code_max = t.required_percent("code_discount_max_percent")
    repeats = tuple(
        parse_percent(v, t.name("repeat_discount_percent"))
        for v in t.texts("repeat_discount_percent")
    )
    refund = t.integer("fee_refund_with_payout")
    rung_after = t.integer("split_after_payouts", 1) if t.has("split_after_payouts") else 0
    rung = (
        t.required_percent("split_percent_after_payouts")
        if t.has("split_percent_after_payouts")
        else None
    )
    retake = (
        t.required_percent("retake_discount_percent")
        if t.has("retake_discount_percent")
        else Fraction(0)
    )
    code_free = t.integers("code_free_sizes_cents", 1) if t.has("code_free_sizes_cents") else ()
    stages = [_stage(s, product_id, sizes) for s in t.tables("stages")]
    t.done()
    if not 0 <= retake < 1 or not set(code_free) <= set(sizes):
        raise PolicyError(
            f"{product_id}: retake_discount_percent below 100, "
            "code_free_sizes_cents among the sizes"
        )
    if not 0 < split <= 1 or not 0 <= code_max < 1:
        raise PolicyError(f"{t.name('id')}: split must be in (0, 100], discounts below 100")
    order = [_STAGE_ORDER[r.stage] for r in stages]
    if order != sorted(set(order)) or stages[-1].stage is not Stage.FUNDED:
        raise PolicyError(f"{product_id}: stages must be distinct, in order, ending funded")
    if (rung_after == 0) != (rung is None) or (rung is not None and not split < rung <= 1):
        raise PolicyError(
            f"{product_id}: split_after_payouts and split_percent_after_payouts go together, "
            "the second above profit_split_percent"
        )
    return ProductPolicy(
        id=product_id,
        name=name,
        prices_cents=tuple(zip(sizes, prices, strict=True)),
        profit_split=split,
        code_discount_max=code_max,
        repeat_discounts=tuple(r for r in repeats if r is not None),
        fee_refund_with_payout=refund,
        rule_sets=tuple(stages),
        split_after_payouts=rung_after,
        split_after_payouts_value=rung,
        retake_discount=retake,
        code_free_sizes=tuple(code_free),
    )


def _stage(t: _Table, product_id: str, sizes: tuple[int, ...]) -> RuleSet:
    stage_text = t.text("stage")
    try:
        stage = Stage(stage_text)
    except ValueError:
        raise PolicyError(f"{t.name('stage')} must be s1, s2 or funded") from None
    version = t.integer("version", 1)
    kind_text = t.text("max_loss_kind")
    try:
        kind = MaxLossKind(kind_text)
    except ValueError:
        raise PolicyError(
            f"{t.name('max_loss_kind')} must be static, trailing or trailing_eod"
        ) from None
    try:
        rule_set = _rule_set(t, product_id, stage, version, kind, sizes)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, PolicyError):
            raise
        raise PolicyError(f"{t.name('stage')} {stage.value}: {exc}") from exc
    t.done()
    return rule_set


def _rule_set(
    t: _Table,
    product_id: str,
    stage: Stage,
    version: int,
    kind: MaxLossKind,
    sizes: tuple[int, ...],
) -> RuleSet:
    return RuleSet(
        rsv_id=f"{product_id}-{stage.value}-v{version}",
        product=product_id,
        stage=stage,
        profit_target=t.percent("profit_target_percent", optional=True),
        max_loss=t.required_percent("max_loss_percent"),
        max_loss_kind=kind,
        daily_loss=t.required_percent("daily_loss_percent"),
        min_trading_days=t.integer("min_trading_days"),
        consistency=t.percent("consistency_percent", optional=True),
        consistency_at_pass=t.boolean("consistency_at_pass"),
        sizes_cents=sizes,
        news_restricted=t.boolean("news_restricted"),
        floor_restarts_after_payout=t.optional_boolean("floor_restarts_after_payout", True),
        floor_locks_at_start=t.optional_boolean("floor_locks_at_start", True),
        flat_before_breaks=t.optional_boolean("flat_before_breaks", False),
        news_breaches=t.optional_boolean("news_breaches", False),
    )
