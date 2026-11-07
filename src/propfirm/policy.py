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
from propfirm.rules.products import MaxLossKind, RuleSet, Stage
from propfirm.staff.records import SECTIONS_APPROVALS, Limits
from propfirm.trading.instruments import (
    InstrumentSpec,
    Pricing,
    SpreadAndCommission,
    default_instrument,
)

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


PAYOUT_APPROVALS = ("pass_authorise", "count")


@dataclass(frozen=True, slots=True)
class StaffPolicy:
    """The back office (staff spec, 5a): step-up sign-in on approvals and
    the grant limits."""

    approvals_need_mfa: bool = True  # every approval: a second factor
    step_up_minutes: int = 5  # entered at most this long ago (0: any MFA login)
    limits: Limits = field(default_factory=Limits)
    login_needs_mfa: bool = True  # every staff and admin login: a second factor
    staff_may_trade: bool = False  # staff and apprentices never buy or trade
    # maker-checker on admin settings: a second admin approves each change
    settings_need_second_admin: bool = True
    proposal_expiry_hours: int = 72
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
    review_months: int
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

    def rule_set(self, stage: Stage) -> RuleSet | None:
        return next((r for r in self.rule_sets if r.stage is stage), None)


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

    def rule_sets(self) -> tuple[RuleSet, ...]:
        return tuple(r for p in self.products for r in p.rule_sets)

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


_RSV_STAGE = re.compile(r"-(s1|s2|funded)-v[0-9]+(?:-a[0-9]+)?$")


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

    def table(self, key: str) -> _Table:
        return _Table(self._get(key), self.name(key))

    def tables(self, key: str) -> list[_Table]:
        value = self._get(key)
        if not isinstance(value, list) or not value:
            raise PolicyError(f"{self.name(key)} must be a non-empty list of tables")
        return [_Table(v, f"{self.name(key)}[{i}]") for i, v in enumerate(value)]

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
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, PolicyError):
            raise
        raise PolicyError(str(exc)) from exc
    root.done()
    ids = [p.id for p in policy.products]
    if len(set(ids)) != len(ids):
        raise PolicyError("product ids must be unique")
    refunds = [c for p in policy.products if p.fee_refund_with_payout for _, c in p.prices_cents]
    if policy.payouts.maximum_cents and policy.payouts.maximum_cents < max(refunds, default=0):
        # the fee refund is paid with a payout and counts towards its maximum
        raise PolicyError("payouts.maximum_cents must be 0 or at least every refundable fee")
    return policy


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
    )
    t.done()
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
        review_months=t.integer("review_months", 1),
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
    leverage_t, caps_t, spread_t = t.table("leverage"), t.table("lot_caps"), t.table("spread")
    commission_t = t.table("commission")
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
            )
        )
    mins_t.done()
    names = {s.name for s in LAUNCH_SYMBOLS}
    for name, ms in by_symbol.items():
        if name not in names:
            raise PolicyError(f"trading.gap_threshold_ms_by_symbol: unknown symbol {name!r}")
        if isinstance(ms, bool) or not isinstance(ms, int) or ms < 1:
            raise PolicyError(f"trading.gap_threshold_ms_by_symbol.{name} must be >= 1")
    guard = GuardConfig(delay, gap, dict(sorted(by_symbol.items())) or None)
    return TradingPolicy(config_version, tuple(instruments), guard)


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
    stages = [_stage(s, product_id, sizes) for s in t.tables("stages")]
    t.done()
    if not 0 < split <= 1 or not 0 <= code_max < 1:
        raise PolicyError(f"{t.name('id')}: split must be in (0, 100], discounts below 100")
    order = [_STAGE_ORDER[r.stage] for r in stages]
    if order != sorted(set(order)) or stages[-1].stage is not Stage.FUNDED:
        raise PolicyError(f"{product_id}: stages must be distinct, in order, ending funded")
    return ProductPolicy(
        id=product_id,
        name=name,
        prices_cents=tuple(zip(sizes, prices, strict=True)),
        profit_split=split,
        code_discount_max=code_max,
        repeat_discounts=tuple(r for r in repeats if r is not None),
        fee_refund_with_payout=refund,
        rule_sets=tuple(stages),
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
        raise PolicyError(f"{t.name('max_loss_kind')} must be static or trailing") from None
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
    )
