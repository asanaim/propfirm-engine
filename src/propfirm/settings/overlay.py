"""Admin settings layered over the policy file.

Three kinds of setting change at run time; the policy file holds the
starting values of the first two:

- country gating (admins): the switch and the list of blocked countries;
- stage limits (owners only, rule AC01): a product stage's profit target,
  daily loss limit and drawdown (maximum loss) limit with its kind, static
  or trailing;
- the owners' stand-in approver (owners only, rule AC00): an admin who may
  approve an owner-level proposal while an owner is unavailable, until a
  set time. It has no starting value: by default there is none.

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

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any

from propfirm.policy import (
    Policy,
    PolicyError,
    all_sizes,
    check_catalogue,
    parse_countries,
    parse_percent,
    percent_text,
)
from propfirm.rules.products import MaxLossKind, RuleSet, Stage
from propfirm.settings.methods import (
    METHOD_SECRET,
    PAYMENT_METHOD,
    MethodSecret,
    PaymentMethod,
    masked_secret,
    method_from_json,
    method_json,
    secret_from_json,
    secret_json,
)
from propfirm.settings.notices import (
    NOTICE_TEMPLATE,
    NOTICE_WRAPPER,
    Notices,
    NoticeTemplate,
    NoticeWrapper,
    template_from_json,
    template_json,
    wrapper_from_json,
    wrapper_json,
)
from propfirm.settings.offering import OFFERING, Offering, offering_from_json, offering_json
from propfirm.settings.site import (
    SITE_MODE,
    SITE_SECTION,
    SiteMode,
    SiteSection,
    mode_from_json,
    mode_json,
    section_from_json,
    section_json,
)

COUNTRY_GATING = "country_gating"
STAGE_LIMITS = "stage_limits"
STAND_IN = "stand_in"
POLICY_PAGE = "policy_page"
PAGE_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
MAX_PAGE_CHARS = 100_000


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
    # trailing kinds only; None keeps the current stage's switch (and older
    # records, which had neither); a static kind always sets both to True
    floor_restarts_after_payout: bool | None = None
    floor_locks_at_start: bool | None = None


@dataclass(frozen=True, slots=True)
class StandIn:
    """The owners' stand-in approver: `subject` (an admin, never an owner)
    may approve owner-level proposals (stage limits) until `until` (UTC
    milliseconds). Never a proposal of their own, never a stand-in change.
    `subject` None ends the appointment."""

    subject: str | None
    until: int  # 0 when ended


@dataclass(frozen=True, slots=True)
class PolicyPage:
    """A new version of a policy page (rule AC10): terms, privacy, trade
    policy or another. Plain text, shown as written (never as HTML)."""

    slug: str
    title: str
    body: str

    def __post_init__(self) -> None:
        if not PAGE_SLUG.fullmatch(self.slug):
            raise PolicyError("slug: lowercase letters, digits and dashes, up to 40")
        if not 1 <= len(self.title.strip()) <= 120:
            raise PolicyError("title: 1 to 120 characters")
        if not 1 <= len(self.body.strip()) <= MAX_PAGE_CHARS:
            raise PolicyError(f"body: 1 to {MAX_PAGE_CHARS} characters")


@dataclass(frozen=True, slots=True)
class PageVersion:
    slug: str
    version: int  # 1, 2, ... per page, in the order published
    title: str
    body: str
    published_at: int
    change_id: int
    author: str
    approved_by: str | None


type Setting = (
    CountryGating
    | StageLimits
    | StandIn
    | PolicyPage
    | SiteSection
    | SiteMode
    | NoticeTemplate
    | NoticeWrapper
    | PaymentMethod
    | MethodSecret
    | Offering
)


@dataclass(frozen=True, slots=True)
class SettingChange:
    id: int  # change number, from the database
    setting: Setting
    author: str  # Keycloak subject of the admin
    author_email: str | None
    reason: str
    ts: int  # UTC milliseconds
    approved_by: str | None = None  # the second admin (maker-checker); None before it
    proposal_id: int | None = None  # the proposal it carried out


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
        floor_restarts_after_payout=_switch(
            limits.max_loss_kind,
            limits.floor_restarts_after_payout,
            base.floor_restarts_after_payout,
        ),
        floor_locks_at_start=_switch(
            limits.max_loss_kind, limits.floor_locks_at_start, base.floor_locks_at_start
        ),
    )


def _switch(kind: MaxLossKind, chosen: bool | None, current: bool) -> bool:
    """A trailing floor's switch: as chosen, else as it was; always True
    (the only valid value) for a static floor."""
    if not kind.trails:
        return True
    return current if chosen is None else chosen


def apply_changes(base: Policy, changes: Iterable[SettingChange]) -> Policy:
    """The policy in force: the file with the latest change of each setting."""
    gating: CountryGating | None = None
    offering: Offering | None = None
    limits: dict[tuple[str, Stage], SettingChange] = {}
    for change in sorted(changes, key=lambda c: c.id):
        match change.setting:
            case CountryGating() as value:
                gating = value
            case Offering() as value:
                offering = value
            case StageLimits() as value:
                limits[(value.product_id, value.stage)] = change
            case (
                StandIn()
                | PolicyPage()
                | SiteSection()
                | SiteMode()
                | PaymentMethod()
                | MethodSecret()
            ):
                pass  # not part of the policy: `stand_in_in_force`, `policy_pages`
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
    catalogue = base.catalogue
    if offering is not None:
        try:
            check_catalogue(base, offering.products_off, offering.sizes_off)
        except PolicyError:
            # a product or size the file no longer has: switch off what still exists
            ids = {p.id for p in base.products}
            sizes = set(all_sizes(base))
            offering = Offering(offering.products_off & ids, offering.sizes_off & sizes)
        catalogue = replace(
            catalogue, products_off=offering.products_off, sizes_off=offering.sizes_off
        )
    return replace(base, access=access, products=tuple(products), catalogue=catalogue)


def policy_pages(changes: Iterable[SettingChange]) -> dict[str, list[PageVersion]]:
    """Every published version of every page, oldest first, by slug."""
    out: dict[str, list[PageVersion]] = {}
    for change in sorted(changes, key=lambda c: c.id):
        page = change.setting
        if isinstance(page, PolicyPage):
            versions = out.setdefault(page.slug, [])
            versions.append(
                PageVersion(
                    page.slug,
                    len(versions) + 1,
                    page.title,
                    page.body,
                    change.ts,
                    change.id,
                    change.author,
                    change.approved_by,
                )
            )
    return out


def site_sections(changes: Iterable[SettingChange]) -> dict[str, SiteSection]:
    """The latest approved text of each website section."""
    out: dict[str, SiteSection] = {}
    for change in sorted(changes, key=lambda c: c.id):
        if isinstance(change.setting, SiteSection):
            out[change.setting.section] = change.setting
    return out


def notices(changes: Iterable[SettingChange]) -> Notices:
    """The notification templates in force (rule AC12): the latest approved
    per event and the latest email wrapper; built-in wording otherwise."""
    templates: dict[str, NoticeTemplate] = {}
    wrapper = Notices().wrapper
    for change in sorted(changes, key=lambda c: c.id):
        match change.setting:
            case NoticeTemplate() as t:
                templates[t.event] = t
            case NoticeWrapper() as w:
                wrapper = w.text
    return Notices(templates, wrapper)


def site_mode(changes: Iterable[SettingChange]) -> SiteMode:
    """The site mode in force (rule AC17): the latest approved, else open."""
    out = SiteMode()
    for change in sorted(changes, key=lambda c: c.id):
        if isinstance(change.setting, SiteMode):
            out = change.setting
    return out


def public_json(setting: Setting) -> tuple[str, dict[str, Any]]:
    """(kind, value) for any view: a secret shows which one, never its value."""
    if isinstance(setting, MethodSecret):
        return METHOD_SECRET, masked_secret(setting)
    return to_json(setting)


def stand_in_in_force(changes: Iterable[SettingChange], now: int) -> StandIn | None:
    """The owners' stand-in approver at `now`: the latest stand-in change,
    if it names someone and has not run out."""
    latest: StandIn | None = None
    for change in sorted(changes, key=lambda c: c.id):
        if isinstance(change.setting, StandIn):
            latest = change.setting
    if latest is None or latest.subject is None or now >= latest.until:
        return None
    return latest


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
            value: dict[str, Any] = {
                "product_id": setting.product_id,
                "stage": setting.stage.value,
                "profit_target_percent": percent_text(setting.profit_target),
                "daily_loss_percent": percent_text(setting.daily_loss),
                "max_loss_percent": percent_text(setting.max_loss),
                "max_loss_kind": setting.max_loss_kind.value,
            }
            # the trailing switches only when chosen: a record without them
            # keeps the form it had before they existed
            for name in ("floor_restarts_after_payout", "floor_locks_at_start"):
                if (switch := getattr(setting, name)) is not None:
                    value[name] = switch
            return STAGE_LIMITS, value
        case StandIn():
            return STAND_IN, {"subject": setting.subject, "until": setting.until}
        case SiteSection():
            return SITE_SECTION, section_json(setting)
        case SiteMode():
            return SITE_MODE, mode_json(setting)
        case NoticeTemplate():
            return NOTICE_TEMPLATE, template_json(setting)
        case NoticeWrapper():
            return NOTICE_WRAPPER, wrapper_json(setting)
        case PaymentMethod():
            return PAYMENT_METHOD, method_json(setting)
        case MethodSecret():
            return METHOD_SECRET, secret_json(setting)
        case Offering():
            return OFFERING, offering_json(setting)
        case PolicyPage():
            return POLICY_PAGE, {"slug": setting.slug, "title": setting.title, "body": setting.body}


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
            value.get("floor_restarts_after_payout"),
            value.get("floor_locks_at_start"),
        )
    if kind == STAND_IN:
        subject, until = value["subject"], value["until"]
        if subject is not None and not isinstance(subject, str):
            raise PolicyError("stand-in subject must be text or null")
        if not isinstance(until, int) or isinstance(until, bool) or until < 0:
            raise PolicyError("stand-in until must be milliseconds")
        return StandIn(subject, until)
    if kind == SITE_SECTION:
        return section_from_json(value)
    if kind == SITE_MODE:
        return mode_from_json(value)
    if kind == NOTICE_TEMPLATE:
        return template_from_json(value)
    if kind == NOTICE_WRAPPER:
        return wrapper_from_json(value)
    if kind == PAYMENT_METHOD:
        return method_from_json(value)
    if kind == METHOD_SECRET:
        return secret_from_json(value)
    if kind == OFFERING:
        return offering_from_json(value)
    if kind == POLICY_PAGE:
        slug, title, body = value["slug"], value["title"], value["body"]
        if not all(isinstance(v, str) for v in (slug, title, body)):
            raise PolicyError("a policy page is text")
        return PolicyPage(slug, title, body)
    raise PolicyError(f"unknown setting kind {kind!r}")


def stage_limits(
    product_id: str,
    stage: str,
    profit_target_percent: str | None,
    daily_loss_percent: str,
    max_loss_percent: str,
    max_loss_kind: str,
    floor_restarts_after_payout: bool | None = None,
    floor_locks_at_start: bool | None = None,
) -> StageLimits:
    """Limits from their text form; PolicyError for a malformed value."""
    try:
        stage_value = Stage(stage)
    except ValueError:
        raise PolicyError("stage must be s1, s2 or funded") from None
    try:
        kind = MaxLossKind(max_loss_kind)
    except ValueError:
        raise PolicyError("max_loss_kind must be static, trailing or trailing_eod") from None
    target = (
        None
        if profit_target_percent is None
        else parse_percent(profit_target_percent, "profit_target_percent")
    )
    daily = parse_percent(daily_loss_percent, "daily_loss_percent")
    maximum = parse_percent(max_loss_percent, "max_loss_percent")
    assert daily is not None and maximum is not None
    for name, switch in (
        ("floor_restarts_after_payout", floor_restarts_after_payout),
        ("floor_locks_at_start", floor_locks_at_start),
    ):
        if switch is not None and not isinstance(switch, bool):
            raise PolicyError(f"{name} must be true, false or null")
        if switch is False and not kind.trails:
            raise PolicyError(f"{name} applies to a trailing maximum loss only")
    return StageLimits(
        product_id,
        stage_value,
        target,
        daily,
        maximum,
        kind,
        floor_restarts_after_payout,
        floor_locks_at_start,
    )
