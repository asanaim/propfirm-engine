"""The platform API: customers, checkout, trading and admin settings.

Views: traders by Keycloak realm role (`trader`); employees by permission,
from the staff directory (`propfirm.staff`: tier, roles, grants), or, for a
login with no directory record, from the Keycloak role (`staff` = T2 with
every role, `admin` = T4). Below, "employee" is T1 to T3 and "admin" T4+:
- public: /health; /catalogue (products, sizes, prices, split, discounts,
  and every stage's profit target, daily loss limit, drawdown limit,
  trading days and consistency rule); /access (the minimum age);
- signed in, any role: /quotes (Twelve Data licence: display to logged-in
  users only);
- trader: their own CIF, orders, accounts (an account shows its own
  target and limits, and a funded account its payable profit and payout
  eligibility), progressions and funded onboardings; buying; their
  identity check, contract and signature; their payout wallet and payout
  requests (both need a login with MFA, by policy); trading their own
  accounts, trailing orders included (no new orders while a payout of the
  account is pending, by policy; a trailing stop on an open position is
  not a new order).
  Someone else's account answers 404, so ids cannot be probed;
- employee (role `staff`): reads every account, its history and inputs;
  reviews passed stages that lead to funding, and identity checks whose
  result differs from the customer record (approve or deny, never their
  own); integrity, fraud and compliance cases: raises alerts, takes, notes,
  decides; a decision with actions needs a second employee (never on their
  own customer record), and a void or profit removal is proposed by a T2+
  and authorised by an admin; payouts: a T2+ passes, then an admin
  authorises (never their own), or a T2+ denies; trades none; no settings;
  every approval needs a recent second factor (step-up); the staff
  directory: /staff/me, members and grants under four eyes;
  restricted news events (M13): publishes a month of the calendar files,
  adds a manual window with a reason, marks a no-fixed-time event
  (Bank of Japan) published; the risk desk's action log;
- admin: what an employee reads, and the admin-only settings: the
  country-gating switch and list (view and change); changing each stage's
  profit target, daily loss limit and drawdown limit; the change history;
  cancelling a restricted event before its window starts (it lifts a
  restriction, so not an employee's).
A signed-in request outside its role's view answers 403.
- the KYC provider (Didit): POST /webhooks/didit, checked by its signature;
  it only prompts a fresh read of the outcome from the provider.

Trading: every change is an input submitted to the engine, and the response
carries the events the engine recorded for it. An engine rejection is
HTTP 200 with status "rejected"; 422 is for requests the API itself
refuses; 202 means not journaled yet (fetch /inputs/{entry_id} later).

Prices and levels are integer points, volumes integer hundredths of a lot,
money integer cents, times integer UTC milliseconds; floats are refused.
"""

import asyncio
import csv
import io
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from importlib import metadata
from typing import Annotated, Any, Literal, Protocol, get_args

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints

from propfirm.analysis import analyse, trades_from
from propfirm.api.auth import AuthError, Principal, TokenVerifier, bearer_token
from propfirm.audit.chain import AuditEvent
from propfirm.audit.http import UNMATCHED, audited, clean, client_ip
from propfirm.audit.http import target as audit_target
from propfirm.audit.store import AuditStore
from propfirm.customers.books import Books, ExpenseRefused, statement_csv
from propfirm.customers.cases import needs_authorisation
from propfirm.customers.cases_store import CaseRecord, CaseRefused
from propfirm.customers.casework import Casework
from propfirm.customers.checkout import Checkout, CheckoutError, Registration
from propfirm.customers.corrections import CorrectionRefused, Corrections
from propfirm.customers.directory import (
    SEGMENTS,
    TX_STATUSES,
    TX_TYPES,
    TxFilter,
    export_rows,
)
from propfirm.customers.four_eyes import open_proposal, proposal_json
from propfirm.customers.funding import Onboarding, Signature
from propfirm.customers.kyc import KycDecision, KycError, SandboxKyc, alpha2, didit_webhook_valid
from propfirm.customers.leaderboard import TTL_MS as LEADERBOARD_TTL_MS
from propfirm.customers.leaderboard import Leaderboard
from propfirm.customers.liability import Claim, Liabilities
from propfirm.customers.lifecycle import Lifecycle
from propfirm.customers.lifecycle_store import DecisionError, ProgressionRecord
from propfirm.customers.onboarding_store import OnboardingRecord, OnboardingStateError
from propfirm.customers.payout_flow import payout_state
from propfirm.customers.payout_service import Payouts
from propfirm.customers.payout_store import PayoutRecord, PayoutRefused, Wallet
from propfirm.customers.payouts import Eligibility, eligibility_json
from propfirm.customers.pricing import discount_floor, price
from propfirm.customers.referrals import ReferralRefused, Referrals
from propfirm.customers.reserve import Evaluations, coverage, k_text
from propfirm.customers.scaling import Scaling, ScalingRefused
from propfirm.customers.store import (
    AccountRecord,
    AlreadyRegisteredError,
    CifRecord,
    CodeTakenError,
    OrderRecord,
    share_of,
)
from propfirm.journal.inputs import (
    CancelOrder,
    ClosePosition,
    Input,
    ModifyStops,
    PlaceOrder,
    PlaceTrailingEntry,
    SetTrailingStop,
)
from propfirm.market.symbols import AssetClass
from propfirm.newsfeed.alerts import SourceAlerts, alert_view
from propfirm.policy import (
    LOCKED_SWITCHES,
    AddonsPolicy,
    CataloguePolicy,
    Policy,
    PolicyError,
    PolicySource,
    ProductPolicy,
    StaffPolicy,
    all_sizes,
    check_catalogue,
    instant_funding,
    parse_countries,
    parse_percent,
    percent_text,
    rule_set_view,
    switch_value,
)
from propfirm.rules.addons import addons_of, is_trial
from propfirm.rules.calendar import Calendar
from propfirm.rules.clock import NEW_YORK_17
from propfirm.rules.news import EventScope
from propfirm.rules.products import KIND_WORDS
from propfirm.seo import (
    ProductText,
    body_html,
    dollars,
    head_html,
    meta,
    robots_txt,
    sitemap_xml,
)
from propfirm.service.feeder import now_ms
from propfirm.service.gateway import Outcome
from propfirm.service.projector import INTERVALS
from propfirm.settings.authority import OWNER_APPROVES, Refused, check_appointment, owner_level
from propfirm.settings.methods import (
    ADAPTERS,
    METHOD_ID,
    SECRET_NAME,
    SecretsUnavailable,
    Vault,
    method_from_json,
    method_json,
)
from propfirm.settings.newsdesk import DEFAULT_IMPACT, Author, NewsDesk, NewsRefused, action_view
from propfirm.settings.notices import (
    BODY_MAX,
    CHANNELS,
    DEFAULT_WRAPPER,
    EVENTS,
    SUBJECT_MAX,
    WRAPPER_MAX,
    Notices,
    NoticeTemplate,
    NoticeWrapper,
    utc_text,
)
from propfirm.settings.offering import Offering
from propfirm.settings.overlay import (
    PAGE_SLUG,
    CountryGating,
    PageVersion,
    PolicyPage,
    Setting,
    StandIn,
    public_json,
    stage_limits,
    to_json,
)
from propfirm.settings.service import Settings, SettingsError
from propfirm.settings.site import (
    SECTIONS,
    SiteMode,
    SiteSection,
    mode_json,
    rendered,
    section_json,
    token_values,
)
from propfirm.settings.store import ChangeStatus, Proposal
from propfirm.staff.cutoff import Cutoffs, IdentityError, cutoff_view
from propfirm.staff.desk import (
    ApprenticeDesk,
    DeskError,
    StaffDesk,
    apprentice_proposal_view,
    message_view,
    notification_view,
)
from propfirm.staff.directory import Actor, Directory
from propfirm.staff.masking import mask_pii
from propfirm.staff.model import PERMISSIONS, TITLES, Access, Tier, legacy_access
from propfirm.staff.oversight import Oversight, oversight_policy
from propfirm.staff.records import Grant, Member, StaffRefused, access_of
from propfirm.staff.screening import Screening
from propfirm.staff.screening import checks as screening_checks
from propfirm.staff.training import courses, current, valid_until
from propfirm.trading.orders import OrderRequest, OrderType, Side

ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
Ident = Annotated[str, Path(pattern=ID_PATTERN)]
REJECTIONS = frozenset({"OrderRejected", "InputRejected"})
TRADER_ROLES = frozenset({"trader"})
# Employees: what each may do is a permission (`propfirm.staff.model`), from
# the staff directory; without one, Keycloak's staff and admin roles map as
# before (staff T2, admin T4).


class LiveSource(Protocol):
    async def head(self) -> dict[str, Any] | None: ...

    async def account(self, account_id: str) -> dict[str, Any] | None: ...

    async def quotes(self) -> dict[str, dict[str, Any]]: ...

    async def news(self) -> list[dict[str, Any]]: ...


class ClassStore(Protocol):
    async def account_class(self, acc: str) -> tuple[str, str, int] | None: ...

    async def set_account_class(
        self, acc: str, cls: str, reason: str, by: str, ts: int
    ) -> None: ...

    async def account(self, acc: str) -> AccountRecord | None: ...


class CodeStore(Protocol):
    """Discount codes (D-14.14)."""

    async def codes(self) -> list[dict[str, Any]]: ...

    async def code_changes(self, code: str) -> list[dict[str, object]]: ...

    async def create_code(
        self,
        code: str,
        discount: Fraction,
        expires_at: int | None,
        max_uses: int | None,
        by: str,
        reason: str,
        ts: int,
    ) -> None: ...

    async def switch_code(self, code: str, on: bool, by: str, reason: str, ts: int) -> bool: ...


class LeaderboardStore(Protocol):
    async def leaderboard_entrants(self) -> list[tuple[AccountRecord, str, str]]: ...

    async def leaderboard_visible(self, cif: str) -> bool: ...

    async def set_leaderboard_visible(self, cif: str, visible: bool, ts: int) -> None: ...


class HistorySource(Protocol):
    async def account_events(
        self, account_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]: ...

    async def bars(
        self, symbol: str, interval: str, limit: int = 300, before: int | None = None
    ) -> list[dict[str, int]]: ...


class Submitter(Protocol):
    async def submit(self, item: Input, wait_s: float = 5.0) -> Outcome: ...

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome: ...


class Traders(Protocol):
    async def counts(self) -> dict[str, int]: ...

    async def traders(
        self,
        segment: str = "all",
        q: str | None = None,
        limit: int = 200,
        offset: int = 0,
        *,
        cif_only: bool = False,
    ) -> list[dict[str, Any]]: ...

    async def trader(self, cif: str) -> dict[str, Any] | None: ...


class ReportSource(Protocol):
    async def transactions(
        self, f: TxFilter, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]: ...

    async def emails(
        self, delivery: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]: ...


class SystemSource(Protocol):
    async def info(self) -> dict[str, Any]: ...


class Customers(Protocol):
    async def cif_by_subject(self, subject: str) -> CifRecord | None: ...

    async def account_owner(self, acc: str) -> str | None: ...

    async def accounts_for(self, cif: str) -> list[AccountRecord]: ...

    async def orders_for(self, cif: str) -> list[OrderRecord]: ...

    async def order(self, ord_id: str) -> OrderRecord | None: ...

    async def account(self, acc: str) -> AccountRecord | None: ...

    async def cif(self, cif: str) -> CifRecord | None: ...


def _today() -> date:
    return datetime.now(UTC).date()


@dataclass(frozen=True, slots=True)
class Deps:
    live: LiveSource
    history: HistorySource
    gateway: Submitter
    verifier: TokenVerifier
    customers: Customers
    checkout: Checkout | None = None
    policy: PolicySource | None = None  # the policy in force
    settings: Settings | None = None  # admin changes
    lifecycle: Lifecycle | None = None  # stage progression
    onboarding: Onboarding | None = None  # funded onboarding
    cases: Casework | None = None  # integrity, fraud and compliance cases
    payouts: Payouts | None = None  # payout wallets, requests and approvals
    traders: Traders | None = None  # traders by status, and each trader's file (AC19)
    reports: ReportSource | None = None  # transaction and email logs (AC20)
    system: SystemSource | None = None  # versions and parts for admins (AC23)
    public_url: str | None = None  # the portal's address, for canonical links (D10)
    news: NewsDesk | None = None  # restricted-events calendar and the risk desk's actions
    source_alerts: SourceAlerts | None = None  # news sources the collector cannot read
    staff: Directory | None = None  # the staff directory; None: Keycloak roles only
    desk: StaffDesk | None = None  # the staff page: duties, notifications, messages
    cutoffs: Cutoffs | None = None  # leavers' logins disabled at the identity provider
    apprentices: ApprenticeDesk | None = None  # apprentices' proposals to their mentors
    oversight: Oversight | None = None  # sign-ins, dormant accounts, reviews, anomalies
    audit: AuditStore | None = None  # the security audit trail (7b); None: not recorded
    leaderboard: LeaderboardStore | None = None  # the public leaderboard and its opt-outs
    codes: CodeStore | None = None  # the coupons screen (D-14.14)
    referrals: Referrals | None = None  # the referral ledger (D-14.15)
    books: Books | None = None  # the operator ledger (D-14.16)
    liabilities: Liabilities | None = None  # the liability desk (D-14.18)
    evaluations: Evaluations | None = None  # reserve coverage: evaluations (D-14.20)
    corrections: Corrections | None = None  # bad-price corrections (D-14.4)
    classes: ClassStore | None = None  # trader classes (D-14.5)
    scaling: Scaling | None = None  # scaling steps (D-14.8)
    client_ip_header: str | None = None  # set by a trusted proxy (X-Forwarded-For), else None
    didit_webhook_secret: str | None = None
    sandbox: bool = False  # sandbox payments endpoint; never in production
    country_header: str | None = None  # set by a trusted proxy (GeoIP), else None
    vault: Vault = field(default_factory=Vault.from_env)  # method secrets (AC14)
    clock: Callable[[], int] = now_ms
    today: Callable[[], date] = _today
    new_id: Callable[[], str] = lambda: uuid.uuid4().hex
    wait_s: float = 5.0
    price_source: str = "synthetic"  # "synthetic" or "twelve_data": named beside prices
    stream_poll_s: float = 0.5  # how often a stream looks for changes
    stream_max_s: float = 300.0  # a stream ends then (an access token lasts 5 minutes)


# shown beside every chart and price table. Twelve Data's attribution
# guidelines: "Data provided by Twelve Data", with "Twelve Data" a followed
# (dofollow) link to https://twelvedata.com/ near the data, in each section.
ATTRIBUTION: dict[str, dict[str, str | None]] = {
    "synthetic": {"text": "Synthetic prices (development)", "link": None, "url": None},
    "twelve_data": {
        "text": "Data provided by Twelve Data",
        "link": "Twelve Data",
        "url": "https://twelvedata.com/",
    },
}

# --- request bodies -----------------------------------------------------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TodoNoteBody(_Body):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    due_at: Annotated[int | None, Field(ge=0)] = None


class OrderBody(_Body):
    symbol: str
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop"]
    volume: StrictInt
    level: StrictInt | None = None
    stop_loss: StrictInt | None = None
    take_profit: StrictInt | None = None
    order_id: Annotated[str, StringConstraints(pattern=ID_PATTERN)] | None = None


class TrailingEntryBody(_Body):
    """A trailing buy or sell: distance and step in whole points (>= 1; the
    engine rejects smaller values with a recorded reason)."""

    symbol: str
    side: Literal["buy", "sell"]
    volume: StrictInt
    distance: StrictInt
    step: StrictInt
    order_id: Annotated[str, StringConstraints(pattern=ID_PATTERN)] | None = None


class TrailingStopBody(_Body):
    distance: StrictInt
    step: StrictInt
    order_id: Annotated[str, StringConstraints(pattern=ID_PATTERN)] | None = None


EventId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Currency = Literal["AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"]  # = CURRENCIES


class NewsReasonBody(_Body):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ManualNewsBody(NewsReasonBody):
    event_id: EventId
    title: Title
    scope: Literal["currency", "oil_inventory"]
    currency: Currency | None = None
    ts: StrictInt  # the release time, ms UTC


class PublishedNewsBody(NewsReasonBody):
    event_id: EventId
    title: Title
    currency: Currency


class ClassBody(_Body):
    cls: Annotated[Literal["A", "B", "C", "D"], Field(alias="class")]
    reason: Annotated[str, StringConstraints(min_length=3, max_length=500)]


class CorrectionBody(_Body):
    symbol: Annotated[str, StringConstraints(min_length=3, max_length=20)]
    price_seq: Annotated[int, Field(ge=1)]
    good_price: Annotated[int, Field(ge=1)]  # points, at the symbol's digits
    reason: Annotated[str, StringConstraints(min_length=3, max_length=500)]


class LeaderboardBody(_Body):
    visible: StrictBool


class CloseBody(_Body):
    volume: StrictInt | None = None


class StopsBody(_Body):
    stop_loss: StrictInt | None
    take_profit: StrictInt | None


class RegisterBody(_Body):
    full_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    date_of_birth: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    residence_country: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
    # a referrer's code (D-14.15); a refused one does not stop registration
    ref: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9]{8}$")] | None = None


class ExpenseBody(_Body):
    """An operating expense to post (D-14.16); a second member of staff approves."""

    category: Literal[
        "hosting",
        "market_data",
        "software",
        "identity_checks",
        "processor_fees",
        "legal",
        "marketing",
        "staff",
        "other",
    ]
    amount_cents: Annotated[int, Field(ge=1, le=10**12)]
    memo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    paid: bool  # paid now (cash) or owed (payables)
    incurred_at: Annotated[int, Field(ge=0)]


class ExpenseDecision(_Body):
    approve: bool
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ClawbackBody(_Body):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


PRODUCT_PATTERN = r"^[a-z][a-z0-9-]{0,30}$"
Percent = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


CodeText = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9-]{3,32}$")]


class CodeBody(_Body):
    """A new discount code (D-14.14)."""

    code: CodeText
    discount_percent: Percent  # at most the highest any product allows
    expires_at: Annotated[int, Field(ge=0)] | None = None  # ms; none: no end
    max_uses: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    reason: Reason


class CodeSwitchBody(_Body):
    on: bool
    reason: Reason


class PurchaseBody(_Body):
    product_id: Annotated[str, StringConstraints(pattern=PRODUCT_PATTERN)]
    size_cents: StrictInt
    method: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")] | None = None
    code: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9-]{3,32}$")] | None = None
    # D-13.18: add-on ids (split90, swing, swapfree), at most one of each
    addons: Annotated[
        list[Annotated[str, StringConstraints(pattern=r"^[a-z0-9]{1,16}$")]],
        Field(max_length=3),
    ] = []


class DecisionBody(_Body):
    reason: Reason


SidText = Annotated[str, StringConstraints(pattern=r"^(STF|APP)[0-9]{8}$")]
Login = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]  # a Keycloak id
Email = Annotated[str, StringConstraints(pattern=r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{1,63}$")]
Team = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Role = Literal["support", "kyc", "risk", "payouts", "integrity", "product", "marketing", "it"]
Power = Literal["settings.view", "settings.change", "news.cancel"]  # = staff.model.POWERS
SectionLevel = Annotated[
    str,
    StringConstraints(pattern=r"^(support|kyc|risk|payouts|integrity|product|marketing|it):[0-3]$"),
]


class MemberBody(_Body):
    """A joiner: `tier` and `roles` are requested for them at once (each
    waits for its approver). An apprentice needs a T2 or T3 `mentor` and
    `ends_at` 3 to 6 months ahead; a contractor a `lead` (sponsor) and
    `ends_at`."""

    subject: Login
    name: Title
    email: Email | None = None
    kind: Literal["staff", "apprentice", "contractor"]
    team: Team | None = None
    lead: SidText | None = None
    mentor: SidText | None = None
    ends_at: StrictInt | None = None
    tier: StrictInt | None = None
    roles: list[Role] = []
    # a level per section [decided 6 Nov 2026], e.g. {"kyc": 2, "payouts": 1}
    sections: dict[Role, Annotated[int, Field(strict=True, ge=0, le=3)]] = {}
    reason: Reason


class MoveBody(_Body):
    team: Team | None = None
    lead: SidText | None = None
    reason: Reason


class GrantBody(_Body):
    """`value`: a tier 0 to 5 (tier, acting), a role, an admin power, or a
    section level "<section>:<0..3>" (level)."""

    sid: SidText
    grant: Literal["tier", "role", "power", "acting", "level"]
    value: StrictInt | Role | Power | SectionLevel
    starts_at: StrictInt | None = None
    ends_at: StrictInt | None = None
    reason: Reason


class MessageBody(_Body):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ReviewBody(_Body):
    decision: Literal["keep", "change"]  # change: revoke or adjust grants as well
    note: Reason


class TrainingBody(_Body):
    section: Role
    note: Reason  # what was assessed, e.g. "course and test passed, 86 %"


class ScreeningCheckBody(_Body):
    check: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,40}$")]
    result: Literal["pass", "fail"]
    # the provider's or document's reference: never the report or the document
    reference: Annotated[
        str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)
    ]


class ScreeningDecisionBody(_Body):
    decision: Literal["approved", "rejected"]
    note: Reason


class ApprenticeProposalBody(_Body):
    """An apprentice's proposal on a live record [staff spec 5c]: a case
    note ({"text"}) or a case decision ({"outcome", "actions", "reason"})."""

    action: Literal["case.note", "case.decide"]
    target: Annotated[str, StringConstraints(pattern=r"^CAS[0-9]{8}$")]
    body: dict[str, Any]


class GraduateBody(_Body):
    """The new staff login (its own Keycloak user) of the graduate."""

    subject: Login
    email: Email | None = None
    reason: Reason


class WalletBody(_Body):
    address: Annotated[str, StringConstraints(min_length=34, max_length=34)]


class AlertBody(_Body):
    cif: Annotated[str, StringConstraints(pattern=r"^CIF[0-9]{8}$")]
    acc: Annotated[str, StringConstraints(pattern=r"^ACC[0-9]{8}$")] | None = None
    kind: Literal["integrity", "fraud", "aml"]
    severity: Literal["low", "medium", "high"]
    reason: Reason
    evidence: dict[str, Any] = {}


class NoteBody(_Body):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class CaseAction(_Body):
    action: Literal["remove_profit", "void_account", "block_customer"]
    acc: Annotated[str, StringConstraints(pattern=r"^ACC[0-9]{8}$")] | None = None
    amount_cents: StrictInt | None = None


class CaseDecisionBody(_Body):
    outcome: Literal["cleared", "warning", "actioned"]
    actions: list[CaseAction] = []
    reason: Reason


class SignBody(_Body):
    full_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class SandboxKycBody(_Body):
    """Sandbox: the outcome to pretend; the identity defaults to the CIF's."""

    result: Literal["approved", "declined"]
    full_name: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    date_of_birth: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    document_country: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,3}$")] | None = None


class CountryGatingBody(_Body):
    country_gating: bool
    blocked_countries: list[Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]]
    reason: Reason


class UserFieldBody(_Body):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
    label: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    type: Literal["text", "number", "email"] = "text"
    required: bool = True


class MethodBody(_Body):
    kind: Literal["payment", "withdrawal"]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    adapter: str
    enabled: bool
    min_cents: Annotated[StrictInt, Field(ge=0)]
    max_cents: Annotated[StrictInt, Field(ge=0)]  # 0: no maximum
    fixed_charge_cents: Annotated[StrictInt, Field(ge=0)]
    percent_charge: Percent
    instructions: Annotated[str, StringConstraints(max_length=2_000)] = ""
    user_fields: list[UserFieldBody] = []
    reason: Reason


class SecretBody(_Body):
    value: Annotated[str, StringConstraints(min_length=1, max_length=4_000)]
    reason: Reason


class SiteSectionBody(_Body):
    fields: dict[str, Annotated[str, StringConstraints(max_length=300)]]
    items: list[dict[str, Annotated[str, StringConstraints(max_length=300)]]] = []
    reason: Reason


class NoticeTemplateBody(_Body):
    subject: Annotated[str, Field(min_length=1, max_length=SUBJECT_MAX)]
    body: Annotated[str, Field(min_length=1, max_length=BODY_MAX)]
    reason: Reason


class NoticeWrapperBody(_Body):
    text: Annotated[str, Field(min_length=1, max_length=WRAPPER_MAX)]
    reason: Reason


class OfferingBody(_Body):
    products_off: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=40)]], Field(max_length=50)
    ]
    sizes_off: Annotated[list[Annotated[int, Field(ge=1)]], Field(max_length=50)]
    # D-13.18; left out: the add-on switches stay as they are
    addons_off: (
        Annotated[list[Annotated[str, Field(min_length=1, max_length=16)]], Field(max_length=10)]
        | None
    ) = None
    reason: Reason


class SiteModeBody(_Body):
    maintenance: StrictBool
    registration_open: StrictBool
    reason: Reason


class PolicyPageBody(_Body):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    body: Annotated[str, StringConstraints(min_length=1, max_length=100_000)]
    reason: Reason


class AcceptPolicyBody(_Body):
    version: Annotated[int, Field(ge=1)]


class ImpactBody(_Body):
    impact: Literal["high", "medium", "low"]
    reason: Reason


class StandInBody(_Body):
    subject: Login | None  # the admin's Keycloak id; null ends the appointment
    until: Annotated[int, Field(ge=0)]  # UTC milliseconds
    reason: Reason


class StageLimitsBody(_Body):
    profit_target_percent: Percent | None  # null: no target (funded stages)
    daily_loss_percent: Percent
    max_loss_percent: Percent  # the drawdown limit
    max_loss_kind: Literal["static", "trailing", "trailing_eod"]
    # trailing kinds only; null keeps the stage's current switch
    floor_restarts_after_payout: bool | None = None
    floor_locks_at_start: bool | None = None
    reason: Reason


# --- views ----------------------------------------------------------------------------


def product_view(
    p: ProductPolicy,
    catalogue: CataloguePolicy | None = None,
    addons: AddonsPolicy | None = None,
) -> dict[str, Any]:
    """A product as the shop shows it; with `catalogue`, only the sizes on
    sale; with `addons`, the add-ons on sale with it (D-13.18)."""
    sizes = p.prices_cents if catalogue is None else catalogue.sizes(p)
    offered = [] if addons is None else addons.for_product(p.id)
    return {
        "id": p.id,
        "name": p.name,
        "profit_split_percent": percent_text(p.profit_split),
        # the ladder (section 1): None where the product has no payout rung
        "split_after_payouts": p.split_after_payouts or None,
        "split_percent_after_payouts": percent_text(p.split_after_payouts_value)
        if p.split_after_payouts_value is not None
        else None,
        "code_discount_max_percent": percent_text(p.code_discount_max),
        "repeat_discount_percent": [percent_text(d) for d in p.repeat_discounts],
        "retake_discount_percent": percent_text(p.retake_discount) if p.retake_discount else None,
        "code_free_sizes_cents": list(p.code_free_sizes),
        "fee_refund_with_payout": p.fee_refund_with_payout or None,
        "sizes": [{"size_cents": s, "price_cents": c} for s, c in sizes],
        "stages": [rule_set_view(r) for r in p.rule_sets],
        "addons": [
            {"id": o.id, "price_percent": percent_text(o.price)}
            for o in offered
            if addons is not None and o.id not in addons.off
        ],
    }


def calendar_view(calendar: Calendar, scheduled: set[str]) -> dict[str, Any]:
    return {
        "version": calendar.version,
        "month": calendar.month,
        "checked": calendar.checked,
        "events": [
            {
                "event_id": e.event.event_id,
                "title": e.title,
                "ts": e.event.ts,
                "local": e.local,
                "zone": e.zone,
                "scope": e.event.scope.value,
                "currency": e.event.currency,
                "source": e.source,
                "in_engine": e.event.event_id in scheduled,  # scheduled and not over
            }
            for e in calendar.events
        ],
        "unscheduled": [
            {"date": u.date, "title": u.title, "currency": u.currency, "source": u.source}
            for u in calendar.unscheduled
        ],
    }


def access_view(a: Access) -> dict[str, Any]:
    return {
        "source": a.source,
        "sid": a.sid,
        "kind": a.kind,
        "tier": None if a.tier is None else f"T{int(a.tier)}",
        "title": None if a.tier is None else TITLES[a.tier],
        "roles": sorted(a.roles),
        "sections": {s: f"T{int(lv)}" for s, lv in sorted(a.levels.items())},
        "powers": sorted(a.powers),
        "permissions": a.permissions(),
        "grants": list(a.grants),
    }


def member_view(m: Member, now: int) -> dict[str, Any]:
    return {
        "sid": m.sid,
        "subject": m.subject,
        "name": m.name,
        "email": m.email,
        "kind": m.kind,
        "team": m.team,
        "lead": m.lead,
        "mentor": m.mentor,
        "starts_at": m.starts_at,
        "ends_at": m.ends_at,
        "status": m.status if m.active(now) or m.status != "active" else "ended",
        "from_sid": m.from_sid,
        "to_sid": m.to_sid,
        "recommended_by": m.recommended_by,
    }


def grant_view(g: Grant, now: int) -> dict[str, Any]:
    return {
        "grt": g.grt,
        "sid": g.sid,
        "grant": g.grant,
        "value": f"T{g.value}" if g.grant in ("tier", "acting") else g.value,
        "starts_at": g.starts_at,
        "ends_at": g.ends_at,
        "status": g.state(now),
        "in_force": g.in_force(now),
        "requested_by": g.requested_by,
        "requested_at": g.requested_at,
        "reason": g.reason,
        "approved_by": g.approved_by,
        "approved_at": g.approved_at,
        "ended_by": g.ended_by,
        "ended_at": g.ended_at,
    }


STAFF_ERRORS = {
    "unknown": 404,
    "forbidden": 403,
    "own_access": 403,
    "four_eyes": 403,
    "invalid": 422,
    "exists": 409,
    "pending": 409,
    "not_allowed": 409,
    "training": 409,
}


def _staff_refused(exc: StaffRefused) -> HTTPException:
    return HTTPException(
        status_code=STAFF_ERRORS[exc.code], detail={"code": exc.code, "message": str(exc)}
    )


def change_view(status: ChangeStatus) -> dict[str, Any]:
    change = status.change
    kind, value = public_json(change.setting)
    return {
        "id": change.id,
        "kind": kind,
        "value": value,
        "author": change.author,
        "author_email": change.author_email,
        "reason": change.reason,
        "created_at": change.ts,
        "status": "pending" if status.applied_at is None else "applied",
        "applied_at": status.applied_at,
        "journal_seq": status.journal_seq,
        "approved_by": change.approved_by,
        "proposal_id": change.proposal_id,
    }


def page_view(p: PageVersion, *, body: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "slug": p.slug,
        "version": p.version,
        "title": p.title,
        "published_at": p.published_at,
    }
    if body:
        out["body"] = p.body
    return out


def proposal_view(p: Proposal, now: int, expiry_ms: int) -> dict[str, Any]:
    kind, value = public_json(p.setting)
    status = p.decision or ("expired" if expiry_ms and now - p.ts > expiry_ms else "open")
    return {
        "id": p.id,
        "kind": kind,
        "value": value,
        "author": p.author,
        "author_email": p.author_email,
        "reason": p.reason,
        "created_at": p.ts,
        "expires_at": p.ts + expiry_ms if expiry_ms else None,
        "status": status,
        "decided_by": p.decided_by,
        "decision_reason": p.decision_reason,
        "decided_at": p.decided_at,
        "change_id": p.change_id,
    }


SETTINGS_ERRORS = {
    "unknown": 404,
    "engine_refused": 502,
    "four_eyes": 403,
    "owners_only": 403,
    "forbidden": 403,
    "not_maker": 403,
    "decided": 409,
    "expired": 409,
    "stale": 409,
}


def _settings_refused(exc: SettingsError) -> HTTPException:
    return HTTPException(
        status_code=SETTINGS_ERRORS.get(exc.code, 422),
        detail={"code": exc.code, "message": str(exc)},
    )


def cif_view(c: CifRecord) -> dict[str, Any]:
    return {
        "cif": c.cif,
        "email": c.email,
        "full_name": c.full_name,
        "date_of_birth": c.date_of_birth.isoformat(),
        "residence_country": c.residence_country,
        "status": c.status,
        "created_at": c.created_at,
    }


def order_view(o: OrderRecord) -> dict[str, Any]:
    return {
        "ord": o.ord,
        "cif": o.cif,
        "product_id": o.product_id,
        "size_cents": o.size_cents,
        "list_price_cents": o.list_price_cents,
        "discount_kind": o.discount_kind,
        "discount_percent": percent_text(o.discount),
        "discount_code": o.discount_code,
        "addons": list(addons_of(o.rsv_id)),  # D-13.18
        "trial": is_trial(o.rsv_id),  # D-13.17
        # what the add-ons cost: the price less the discounted list price
        "addon_cents": o.price_cents
        - (o.list_price_cents - discount_floor(o.list_price_cents, o.discount)),
        "price_cents": o.price_cents,
        "rsv_id": o.rsv_id,
        "status": o.status,
        "acc": o.acc,
        "failure": o.failure,
        "created_at": o.created_at,
        "paid_at": o.paid_at,
        "provisioned_at": o.provisioned_at,
    }


def account_record_view(a: AccountRecord) -> dict[str, Any]:
    return {
        "acc": a.acc,
        "ord": a.ord,
        "product_id": a.product_id,
        "rsv_id": a.rsv_id,
        "size_cents": a.size_cents,
        "stage": a.stage,
        "progression": a.progression,
        "onboarding": a.onboarding,
        "created_at": a.created_at,
    }


def progression_view(p: ProgressionRecord) -> dict[str, Any]:
    return {
        "prg": p.prg,
        "cif": p.cif,
        "ord": p.ord,
        "product_id": p.product_id,
        "size_cents": p.size_cents,
        "from_acc": p.from_acc,
        "from_stage": p.from_stage,
        "to_stage": p.to_stage,
        "status": p.status,
        "to_rsv_id": p.to_rsv_id,
        "to_acc": p.to_acc,
        "failure": p.failure,
        "passed_at": p.passed_at,
        "passed_seq": p.passed_seq,
        "decided_by": p.decided_by,
        "decided_at": p.decided_at,
        "created_at": p.created_at,
    }


def onboarding_view(o: OnboardingRecord) -> dict[str, Any]:
    return {
        "onb": o.onb,
        "cif": o.cif,
        "source": o.source,
        "prg": o.prg,
        "ord": o.ord,
        "product_id": o.product_id,
        "size_cents": o.size_cents,
        "rsv_id": o.rsv_id,
        "status": o.status,
        "kyc": o.kyc,
        "kyc_attempts": o.kyc_attempts,
        "acc": o.acc,
        "contract": o.contract,
        "failure": o.failure,
        "created_at": o.created_at,
    }


ONBOARDING_ERRORS = {
    "unknown": 404,
    "not_verifying": 409,
    "not_signing": 409,
    "not_in_review": 409,
    "open_case": 409,
    "attempts": 409,
    "not_signer": 403,
    "blocked": 403,
    "own_onboarding": 403,
    "four_eyes": 403,
    "other_text": 422,
    "name": 422,
}


def _onboarding_refused(exc: OnboardingStateError) -> HTTPException:
    return HTTPException(
        status_code=ONBOARDING_ERRORS[exc.code], detail={"code": exc.code, "message": str(exc)}
    )


def eligibility_view(e: Eligibility) -> dict[str, Any]:
    return eligibility_json(e)


def wallet_view(w: Wallet) -> dict[str, Any]:
    return {"wal": w.wal, "network": w.network, "address": w.address, "added_at": w.added_at}


def payout_view(p: PayoutRecord) -> dict[str, Any]:
    return {
        "po": p.po,
        "acc": p.acc,
        "cif": p.cif,
        "wal": p.wal,
        "requested_at": p.requested_at,
        "trader_amount_cents": p.trader_amount_cents,
        "debit_cents": p.debit_cents,
        "status": p.status,
        "approvals": p.approvals,
        "debited_at": p.debited_at,
        "paid_at": p.paid_at,
        "provider_ref": p.provider_ref,
        "failure": p.failure,
    }


PAYOUT_ERRORS = {
    "unknown": 404,
    "own_payout": 403,
    "mfa_required": 401,  # RFC 9470 step-up: sign in again with a second factor
    "reauth_required": 401,
    "not_allowed": 409,
    "pending": 409,
    "not_eligible": 409,
    "product": 409,
    "invalid": 422,
}


def _payout_refused(exc: PayoutRefused) -> HTTPException:
    headers = None
    if exc.code in ("mfa_required", "reauth_required"):
        # RFC 9470: the portal signs the user in again (prompt, max_age)
        challenge = 'Bearer error="insufficient_user_authentication"'
        if exc.max_age_s is not None:
            challenge += f", max_age={exc.max_age_s}"
        headers = {"WWW-Authenticate": challenge}
    return HTTPException(
        status_code=PAYOUT_ERRORS[exc.code],
        detail={"code": exc.code, "message": str(exc)},
        headers=headers,
    )


def case_view(c: CaseRecord) -> dict[str, Any]:
    return {
        "cas": c.cas,
        "cif": c.cif,
        "opened_at": c.opened_at,
        "status": c.status,
        "severity": c.severity,
        "due_at": c.due_at,
        "assignee": c.assignee,
        "outcome": c.outcome,
        "proposed_by": c.proposed_by,
        "closed_at": c.closed_at,
    }


CASE_ERRORS = {
    "unknown": 404,
    "own_case": 403,
    "not_allowed": 409,
    "another_case_open": 409,
    "invalid": 422,
}


def _case_refused(exc: CaseRefused) -> HTTPException:
    return HTTPException(
        status_code=CASE_ERRORS[exc.code], detail={"code": exc.code, "message": str(exc)}
    )


def _answer(outcome: Outcome) -> JSONResponse:
    if outcome.seq is None:
        return JSONResponse({"entry_id": outcome.entry_id, "status": "pending"}, status_code=202)
    events: Sequence[Any] = outcome.events
    rejected = any(isinstance(e, dict) and e.get("type") in REJECTIONS for e in events)
    return JSONResponse(
        {
            "entry_id": outcome.entry_id,
            "seq": outcome.seq,
            "status": "rejected" if rejected else "done",
            "events": list(events),
        }
    )


# --- dependencies ---------------------------------------------------------------------


def _deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    return deps


D = Annotated[Deps, Depends(_deps)]


async def _principal(
    request: Request, deps: D, authorization: Annotated[str | None, Header()] = None
) -> Principal:
    try:
        principal = await deps.verifier.verify(bearer_token(authorization))
    except AuthError as exc:
        raise HTTPException(
            status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    request.state.subject = principal.subject  # for the audit trail
    return principal


Me = Annotated[Principal, Depends(_principal)]


def _role(me: Principal, roles: frozenset[str]) -> None:
    if not me.roles & roles:
        raise HTTPException(status_code=403, detail="not in this login's view")


async def _staff_policy(deps: Deps) -> StaffPolicy:
    return (await deps.policy.current()).staff if deps.policy is not None else StaffPolicy()


async def _trader(deps: D, me: Me) -> Principal:
    """A trader, never staff: staff and apprentices do not buy or trade
    (`staff.staff_may_trade`, decided 6 Nov 2026)."""
    _role(me, TRADER_ROLES)
    banned = not (await _staff_policy(deps)).staff_may_trade
    if banned and (await _access(deps, me)).tier is not None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "staff_trading_banned",
                "message": "staff do not hold challenges or trade",
            },
        )
    return me


def _actor(me: Principal) -> Actor:
    return Actor(me.subject, me.roles)


async def _access(deps: Deps, me: Principal) -> Access:
    if deps.staff is None:
        return legacy_access(me.subject, me.roles)
    return await deps.staff.access(_actor(me), deps.clock())


async def _staff_access(deps: Deps, me: Principal) -> Access:
    """The login's staff access; a login with any staff access must have
    signed in with a second factor (`staff.login_needs_mfa`)."""
    access = await _access(deps, me)
    if access.source == "screening":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "screening_required",
                "message": "your pre-employment screening is not complete or has expired; "
                "staff access opens once an admin approves it",
            },
        )
    if access.tier is not None and deps.oversight is not None and me.mfa:
        await deps.oversight.signed_in(me.subject, deps.clock())  # dormant accounts (5d)
    if access.tier is not None and not me.mfa and (await _staff_policy(deps)).login_needs_mfa:
        raise HTTPException(
            status_code=401,
            detail={"code": "mfa_required", "message": "staff sign in with a second factor"},
            headers={"WWW-Authenticate": 'Bearer error="insufficient_user_authentication"'},
        )
    return access


async def _step_up(deps: Deps, me: Principal) -> None:
    """Every approval: a second factor, entered at most `step_up_minutes`
    ago (RFC 9470 challenge, as the trader's payout request)."""
    staff = await _staff_policy(deps)
    if not staff.approvals_need_mfa:
        return
    minutes = staff.step_up_minutes
    challenge = 'Bearer error="insufficient_user_authentication"'
    if not me.mfa:
        raise HTTPException(
            status_code=401,
            detail={"code": "mfa_required", "message": "approvals need a second factor"},
            headers={"WWW-Authenticate": challenge},
        )
    limit = minutes * 60_000
    if limit and (me.auth_time is None or deps.clock() - me.auth_time > limit):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "reauth_required",
                "message": f"confirm with your second factor (within {minutes} minutes)",
            },
            headers={"WWW-Authenticate": f"{challenge}, max_age={minutes * 60}"},
        )


def _forbidden(name: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={
            "code": "forbidden",
            "message": f"needs {name}: {PERMISSIONS[name].describes}",
            "permission": name,
        },
    )


async def _need(deps: Deps, me: Principal, name: str) -> Access:
    """The login's access, if it has the permission (and, for an
    approval, a recent second factor)."""
    access = await _staff_access(deps, me)
    if not access.has(name):
        raise _forbidden(name)
    if PERMISSIONS[name].approval:
        await _step_up(deps, me)
    return access


def _can(name: str) -> Callable[..., Awaitable[Principal]]:
    async def check(deps: D, me: Me) -> Principal:
        await _need(deps, me, name)
        return me

    check.__name__ = f"can_{name.replace('.', '_')}"
    return check


async def _viewer(request: Request, deps: D, me: Me) -> Principal:
    """Reads of back-office records: staff (T1+) see them whole; apprentices
    (T0) with personal data masked (`propfirm.staff.masking`)."""
    access = await _staff_access(deps, me)
    if access.has("staff.view"):
        return me
    if access.has("staff.view_masked"):
        request.state.mask_pii = True
        return me
    raise _forbidden("staff.view")


async def _employee(deps: D, me: Me) -> Principal:
    """Anyone with staff access now (an apprentice too)."""
    access = await _staff_access(deps, me)
    if access.tier is None:
        raise HTTPException(status_code=403, detail="not in this login's view")
    return me


Trader = Annotated[Principal, Depends(_trader)]
Staff = Annotated[Principal, Depends(_can("staff.view"))]
Viewer = Annotated[Principal, Depends(_viewer)]
Employee = Annotated[Principal, Depends(_employee)]
Approver = Annotated[Principal, Depends(_can("progression.decide"))]
IdentityReviewer = Annotated[Principal, Depends(_can("onboarding.decide"))]
CaseWorker = Annotated[Principal, Depends(_can("case.work"))]
PayoutDenier = Annotated[Principal, Depends(_can("payout.deny"))]
NewsDesker = Annotated[Principal, Depends(_can("news.desk"))]
NewsCanceller = Annotated[Principal, Depends(_can("news.cancel"))]
SettingsReader = Annotated[Principal, Depends(_can("settings.view"))]
SettingsChanger = Annotated[Principal, Depends(_can("settings.change"))]
DirectoryReader = Annotated[Principal, Depends(_can("staff.directory"))]
PrgId = Annotated[str, Path(pattern=r"^PRG[0-9]{8}$")]
STATUSES = Literal[
    "provisioning", "provisioned", "failed", "review", "approved", "denied", "blocked"
]
OnbId = Annotated[str, Path(pattern=r"^ONB[0-9]{8}$")]
CasId = Annotated[str, Path(pattern=r"^CAS[0-9]{8}$")]
PoId = Annotated[str, Path(pattern=r"^PO[0-9]{8}$")]
CorId = Annotated[str, Path(pattern=r"^COR[0-9]{8}$")]
SclId = Annotated[str, Path(pattern=r"^SCL[0-9]{8}$")]
PAYOUT_STATUS = Literal[
    "requested", "approved", "debited", "sent", "paid", "denied", "cancelled", "failed"
]
CASE_STATUSES = Literal["open", "decision_pending", "actioning", "closed"]
TX_TYPE = Literal["purchase", "payout"]
TX_STATUS = Literal[
    "pending", "paid", "provisioned", "failed", "requested", "approved", "debited", "sent",
    "denied", "cancelled",
]  # fmt: skip
assert set(get_args(TX_TYPE)) == set(TX_TYPES) and set(get_args(TX_STATUS)) == set(TX_STATUSES)
EXPORT_LIMIT = 10_000
SEGMENT = Literal[
    "all", "trading", "funded", "no_account", "kyc_pending", "kyc_declined", "open_case", "blocked"
]
assert set(get_args(SEGMENT)) == set(SEGMENTS)  # the directory's segments, one list
ONB_STATUSES = Literal[
    "verification",
    "verification_declined",
    "checks",
    "review",
    "rejected",
    "contract",
    "signature",
    "signed",
    "provisioned",
    "failed",
]


def _engine_version() -> str:
    try:
        return metadata.version("propfirm-engine")
    except metadata.PackageNotFoundError:
        return "unknown"


async def _policy(deps: Deps) -> Policy:
    if deps.policy is None:
        raise HTTPException(status_code=503, detail="policy not loaded")
    return await deps.policy.current()


def _lifecycle(deps: Deps) -> Lifecycle:
    if deps.lifecycle is None:
        raise HTTPException(status_code=503, detail="lifecycle not available")
    return deps.lifecycle


def _settings(deps: Deps) -> Settings:
    if deps.settings is None:
        raise HTTPException(status_code=503, detail="settings not available")
    return deps.settings


def _cases(deps: Deps) -> Casework:
    if deps.cases is None:
        raise HTTPException(status_code=503, detail="cases are not available")
    return deps.cases


def _payouts(deps: Deps) -> Payouts:
    if deps.payouts is None:
        raise HTTPException(status_code=503, detail="payouts are not available")
    return deps.payouts


def _onboarding(deps: Deps) -> Onboarding:
    if deps.onboarding is None:
        raise HTTPException(status_code=503, detail="funded onboarding is not available")
    return deps.onboarding


DAY_MS = 86_400_000


async def training_view(deps: Deps, access: Access, now: int) -> list[dict[str, Any]]:
    """Each section held: its course, the pass in force and until when."""
    if deps.staff is None or access.sid is None:
        return []
    months = (await deps.staff.policy_now()).staff.training_valid_months
    passes = await deps.staff.training(access.sid)
    out = []
    for section in sorted(access.roles):
        course = courses()[section]
        found = current(passes, section, now, months)
        out.append(
            {
                "section": section,
                "course": course.course,
                "title": course.title,
                "version": course.version,
                "passed_at": None if found is None else found.passed_at,
                "valid_until": None if found is None else valid_until(found, months),
                "status": "missing" if found is None else "valid",
            }
        )
    return out


def _todo(kind: str, ref: str, title: str, since: int, due_at: int | None = None) -> dict[str, Any]:
    return {"kind": kind, "ref": ref, "title": title, "since": since, "due_at": due_at}


async def build_todo(deps: Deps, me: Principal, access: Access) -> list[dict[str, Any]]:
    """The work waiting for this login, from the queues its permissions
    reach [staff spec 5b], soonest due (or oldest) first. Each item names
    the record; the record's own endpoint does the work."""
    now, out = deps.clock(), []
    if deps.payouts is not None and (access.has("payout.pass") or access.has("payout.authorise")):
        store = deps.payouts.store
        for p in await store.payouts(status="requested"):
            state = payout_state(p.po, await store.history(p.po))
            if me.subject in state.approvals:
                continue
            step = state.next_step or "pass"
            if access.has(f"payout.{step}"):
                amount = p.trader_amount_cents
                title = f"{step.title()} payout {p.po} ({amount // 100:,}.{amount % 100:02d} USD)"
                out.append(_todo(f"payout_{step}", p.po, title, p.requested_at))
    if deps.cases is not None and access.has("case.work"):
        gone: set[str] = set()  # leavers' open work is taken over [staff spec 5d]
        if deps.staff is not None:
            async with deps.staff.store.read() as ops:
                gone = {m.subject for m in await ops.members() if not m.active(now)}
        for c in await deps.cases.store.cases(status="open"):
            if c.assignee in (None, me.subject) or c.assignee in gone:
                verb = "Work" if c.assignee == me.subject else "Take"
                title = f"{verb} case {c.cas} ({c.severity})"
                out.append(_todo("case", c.cas, title, c.opened_at, c.due_at))
        if access.has("case.confirm") or access.has("case.authorise"):
            for c in await deps.cases.store.cases(status="decision_pending"):
                if c.proposed_by != me.subject:
                    title = f"Confirm or authorise the decision on case {c.cas}"
                    out.append(_todo("case_decision", c.cas, title, c.opened_at, c.due_at))
    if deps.onboarding is not None and access.has("onboarding.decide"):
        for o in await deps.onboarding.store.with_status("review"):
            out.append(_todo("identity_review", o.onb, f"Review identity, {o.onb}", o.created_at))
    if deps.lifecycle is not None and access.has("progression.decide"):
        for g in await deps.lifecycle.store.with_status("review"):
            out.append(_todo("funded_review", g.prg, f"Funded review {g.prg}", g.created_at))
    if deps.staff is not None:
        actor = Actor(me.subject, me.roles)
        for grant in await deps.staff.awaiting(actor, now):
            title = f"Approve or deny {grant.grant} {grant.value} for {grant.sid}"
            out.append(_todo("grant", grant.grt, title, grant.requested_at))
        for member in await deps.staff.graduations(actor, now):
            title = f"Graduate apprentice {member.sid}"
            out.append(_todo("graduation", member.sid, title, member.starts_at))
        if access.sid is not None:
            async with deps.staff.store.read() as ops:
                for grant in await ops.grants_of(access.sid):
                    ends = grant.ends_at
                    if grant.in_force(now) and ends is not None and ends - now <= 7 * DAY_MS:
                        title = f"Your {grant.grant} {grant.value} ends soon ({grant.grt})"
                        out.append(_todo("grant_ending", grant.grt, title, now, ends))
    if (
        deps.settings is not None
        and access.has("settings.change")
        and access.tier is not None
        and access.tier >= Tier.ADMIN
    ):
        expiry = (await deps.settings.current()).staff.proposal_expiry_hours * 3_600_000
        try:
            waiting = await deps.settings.proposals(open_only=True)
        except SettingsError:
            waiting = []  # no proposal store
        for prop in waiting:
            if prop.author != me.subject and now - prop.ts <= expiry:
                title = f"Approve or reject settings proposal {prop.id}"
                out.append(_todo("settings_proposal", str(prop.id), title, prop.ts))
    if deps.source_alerts is not None and access.has("news.desk"):
        for alert in await deps.source_alerts.open_alerts():
            title = f"Add the news a blocked source misses: {alert.source}"
            out.append(_todo("news_gap", f"news-source:{alert.source}", title, alert.since))
    if deps.oversight is not None and (access.sid is not None):
        policy = oversight_policy(await _staff_policy(deps))
        for sid, due in await deps.oversight.reviews_due(access.sid, access.tier, now, policy):
            title = f"Review {sid}'s access (keep or change)"
            out.append(_todo("access_review", sid, title, due, due))
    for t in await training_view(deps, access, now):
        if access.tier is not None and access.tier >= Tier.ADMIN:
            break
        if t["status"] == "missing":
            title = f"Pass the {t['section']} course: {t['title']}"
            out.append(_todo("training", t["section"], title, now))
        elif t["valid_until"] is not None and t["valid_until"] - now <= 30 * DAY_MS:
            title = f"Refresh the {t['section']} course before it lapses"
            out.append(_todo("training", t["section"], title, now, t["valid_until"]))
    if deps.apprentices is not None and access.sid is not None:
        for mine in await deps.apprentices.proposals(mentor=access.sid, open_only=True):
            title = f"Your apprentice {mine.sid} proposes {mine.action} on {mine.target}"
            out.append(_todo("apprentice_proposal", str(mine.id), title, mine.ts))
    if deps.desk is not None:
        for sheet in await deps.desk.duties_view(access):
            if not sheet["acknowledged"]:
                title = f"Read and acknowledge your duties: {sheet['title']} v{sheet['version']}"
                out.append(_todo("duties", sheet["sheet"], title, now))
    if deps.staff is not None:
        for sid, what, sc in await deps.staff.screening_due(_actor(me), now):
            if what == "approve":
                title = f"Approve or reject the screening of {sid}"
                out.append(_todo("screening_approve", sid, title, now))
            else:
                missing = ", ".join(sc.missing) or "renewal"
                title = f"Screen {sid}: {missing}"
                out.append(_todo("screening_check", sid, title, now, sc.valid_until))
        # the employee's own notes
        async with deps.staff.store.read() as ops:
            for note_id, text, due_at, ts in await ops.open_notes(me.subject):
                out.append(_todo("note", str(note_id), text, ts, due_at))
    out.sort(key=lambda t: (t["due_at"] if t["due_at"] is not None else t["since"], t["ref"]))
    return out


def _checkout(deps: Deps) -> Checkout:
    if deps.checkout is None:
        raise HTTPException(status_code=503, detail="checkout not available")
    return deps.checkout


async def _cif(deps: Deps, me: Principal) -> CifRecord:
    cif = await deps.customers.cif_by_subject(me.subject)
    if cif is None:
        raise HTTPException(status_code=404, detail="not registered: POST /me first")
    return cif


async def _may_read(deps: Deps, me: Principal, account_id: str) -> None:
    if (await _staff_access(deps, me)).has("staff.view"):
        return
    _role(me, TRADER_ROLES)
    await _owns(deps, me, account_id)


async def _owns(deps: Deps, me: Principal, account_id: str) -> None:
    cif = await deps.customers.cif_by_subject(me.subject)
    owner = await deps.customers.account_owner(account_id)
    if cif is None or owner != cif.cif:
        raise HTTPException(status_code=404, detail="unknown account")


def _refused(exc: CheckoutError) -> HTTPException:
    status = {
        "refused": 403,
        "blocked": 403,
        "screening": 403,
        "unknown_product": 404,
        "unknown_order": 404,
        "unknown_payment": 404,
        "trial_used": 409,
        "trial_off": 404,
    }.get(exc.code, 422)
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


async def _mode(deps: Deps) -> SiteMode:
    """The site mode in force (rule AC17); open when settings are not attached."""
    return SiteMode() if deps.settings is None else await deps.settings.mode()


def _closed(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


# --- the app --------------------------------------------------------------------------


def create_app(
    deps: Deps | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """`deps` directly (tests), or set on `app.state.deps` by `lifespan`."""
    app = FastAPI(title="pipridge platform API", version="0.2.0", lifespan=lifespan)
    app.state.deps = deps

    @app.middleware("http")
    async def masked_for_apprentices(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """An apprentice's read (`_viewer`): personal data masked."""
        response = await call_next(request)
        if not getattr(request.state, "mask_pii", False):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        if not response.headers.get("content-type", "").startswith("application/json"):
            return Response(b"", status_code=500)  # never pass an unmasked body on
        return JSONResponse(mask_pii(json.loads(body)), status_code=response.status_code)

    @app.middleware("http")
    async def audit_trail(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Every audited request (`propfirm.audit.http`) is appended to the
        hash-chained audit trail after it is answered; the answer carries
        the record's request id. A failed write is logged, never turned
        into a failed request (the action has happened by then)."""
        deps: Deps | None = request.app.state.deps
        if deps is None or deps.audit is None:
            return await call_next(request)
        request_id = deps.new_id()
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        route = getattr(request.scope.get("route"), "path", UNMATCHED)
        if not audited(request.method, route, response.status_code):
            return response
        try:
            event = AuditEvent(
                deps.clock(),
                clean(getattr(request.state, "subject", None) or "anonymous", 200),
                f"{clean(request.method, 7)} {clean(route, 200)}",
                audit_target(request.path_params),
                response.status_code,
                client_ip(
                    request.headers.get(deps.client_ip_header) if deps.client_ip_header else None,
                    request.client.host if request.client else None,
                ),
                request_id,
            )
            await deps.audit.append(event)
        except Exception:  # logged; the response stands
            logging.getLogger(__name__).exception(
                "audit record not written for %s %s", request.method, route
            )
        return response

    async def submit(deps: Deps, build: Callable[[int], Input]) -> JSONResponse:
        try:
            item = build(deps.clock())
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _answer(await deps.gateway.submit(item, deps.wait_s))

    # --- public -----------------------------------------------------------------------

    @app.get("/health")
    async def health(deps: D) -> dict[str, Any]:
        head = await deps.live.head()
        return {"status": "ok", "journal_seq": head["seq"] if head else None}

    @app.get("/tools/instruments")
    async def tool_instruments(deps: D) -> list[dict[str, Any]]:
        """What the public trading calculators need per symbol: contract size,
        leverage, quote currency and the size of one pip (forex: the fourth
        decimal, or the second for JPY pairs; others: one point). No prices:
        those are licensed to signed-in users only."""
        out = []
        for i in (await _policy(deps)).trading.instruments:
            forex = i.symbol.asset_class == AssetClass.FOREX
            places = i.digits - 1 if forex else i.digits
            pct = Decimal(i.commission.numerator * 100) / Decimal(i.commission.denominator)
            out.append(
                {
                    "symbol": i.name,
                    "asset_class": str(i.symbol.asset_class),
                    "digits": i.digits,
                    "contract_size": i.contract_size,
                    "leverage": f"{i.leverage.numerator}/{i.leverage.denominator}",
                    "quote_currency": i.quote_currency,
                    "pip": f"{Decimal(1).scaleb(-places):f}",
                    "pip_name": "pip" if forex else "point",
                    # charged once when a position opens, as a share of the notional
                    "commission_percent": f"{pct.normalize():f}",
                }
            )
        return out

    @app.get("/catalogue")
    async def catalogue(deps: D) -> list[dict[str, Any]]:
        """Products on sale: sizes on sale, prices, terms, targets and loss
        limits (review D4: products and sizes switched off are not shown)."""
        policy = await _policy(deps)
        on_sale = [p for p in policy.products if policy.catalogue.on_sale(p)]
        return [product_view(p, policy.catalogue, policy.addons) for p in on_sale]

    @app.get("/access")
    async def access(deps: D) -> dict[str, Any]:
        """The minimum age to register (country gating is admin-only)."""
        return {"min_age": (await _policy(deps)).access.min_age}

    # --- customer ---------------------------------------------------------------------

    @app.get("/me")
    async def me(deps: D, me: Trader) -> dict[str, Any]:
        cif = await _cif(deps, me)
        accounts = await deps.customers.accounts_for(cif.cif)
        orders = await deps.customers.orders_for(cif.cif)
        progressions = (
            await deps.lifecycle.store.progressions_for(cif.cif) if deps.lifecycle else []
        )
        onboardings = (
            await deps.onboarding.store.onboardings_for(cif.cif) if deps.onboarding else []
        )
        return {
            **cif_view(cif),
            "accounts": [account_record_view(a) for a in accounts],
            "orders": [order_view(o) for o in orders],
            "progressions": [progression_view(p) for p in progressions],
            "onboardings": [onboarding_view(o) for o in onboardings],
        }

    # --- payout wallet and payouts ------------------------------------------------

    @app.post("/me/wallet", status_code=201)
    async def add_wallet(deps: D, me: Trader, body: WalletBody) -> dict[str, Any]:
        """A new payout wallet (USDT, TRC-20): used after the cooling-off
        period; replaces the one in use (MFA login, by policy)."""
        cif = await _cif(deps, me)
        try:
            wallet = await _payouts(deps).add_wallet(
                cif, me.subject, me.mfa, body.address, deps.clock(), me.auth_time
            )
        except PayoutRefused as exc:
            raise _payout_refused(exc) from exc
        return wallet_view(wallet)

    def _corrections(deps: Deps) -> Corrections:
        if deps.corrections is None:
            raise HTTPException(status_code=503, detail="corrections not available")
        return deps.corrections

    def _correction_refused(exc: CorrectionRefused) -> HTTPException:
        status = {"unknown": 404, "no_fills": 404, "no_price": 404}.get(exc.code, 409)
        return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})

    @app.put("/staff/accounts/{acc}/class")
    async def set_class(deps: D, me: CaseWorker, acc: Ident, body: ClassBody) -> dict[str, Any]:
        """Set an account's trader class (D-14.5), with a reason; kept with
        who set it. Class C or D: every payout of the account is reviewed."""
        store = deps.classes
        if store is None:
            raise HTTPException(status_code=503, detail="classes not available")
        if await store.account(acc) is None:
            raise HTTPException(status_code=404, detail="unknown account")
        await store.set_account_class(acc, body.cls, body.reason, me.subject, deps.clock())
        found = await store.account_class(acc)
        assert found is not None
        return {"acc": acc, "class": found[0], "reason": found[1], "since": found[2]}

    def _scaling(deps: Deps) -> Scaling:
        if deps.scaling is None:
            raise HTTPException(status_code=503, detail="scaling not available")
        return deps.scaling

    def _scaling_refused(exc: ScalingRefused) -> HTTPException:
        status = 404 if exc.code == "unknown" else 409
        return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})

    def _books(deps: Deps) -> Books:
        if deps.books is None:
            raise HTTPException(status_code=503, detail="the books are not configured")
        return deps.books

    def claim_view(c: Claim) -> dict[str, Any]:
        return {
            "acc": c.acc,
            "cif": c.cif,
            "product_id": c.product_id,
            "certain_cents": c.certain_cents,
            "open_cents": c.open_cents,
            "expected_cents": c.expected_cents,
            "total_cents": c.total_cents,
            "open_profit_cents": c.open_profit_cents,
        }

    @app.get("/staff/liabilities")
    async def liability_desk(deps: D, me: SettingsReader) -> dict[str, Any]:
        """What the firm owes or may owe traders (D-14.18): certain, open
        and expected, the largest single claim and the largest claims."""
        if deps.liabilities is None:
            raise HTTPException(status_code=503, detail="the liability desk is not configured")
        d = await deps.liabilities.desk(deps.clock())
        reserve: dict[str, Any] | None = None
        policy = (await deps.policy.current()).reserve if deps.policy is not None else None
        if policy is not None and policy.on and deps.books and deps.evaluations:
            expected, evaluations = await deps.evaluations.expected(policy)
            c = coverage(await deps.books.reserve_cents(), d, expected, evaluations, policy)
            reserve = {
                "reserve_cents": c.reserve_cents,
                "certain_cents": c.certain_cents,
                "open_profit_cents": c.open_profit_cents,
                "expected_cents": c.expected_cents,
                "evaluations": c.evaluations,
                "largest_customer_cents": c.largest_customer_cents,
                "required_cents": c.required_cents,
                "k": k_text(c.k),
                "state": c.state,
                "releasable_cents": c.releasable_cents,
                "thresholds": {
                    "amber_below": k_text(policy.amber_below),
                    "red_below": k_text(policy.red_below),
                    "breach_below": k_text(policy.breach_below),
                    "release_above": k_text(policy.release_above),
                },
                "weights": {
                    "open": k_text(policy.open_weight),
                    "expected": k_text(policy.expected_weight),
                },
            }
        return {
            "as_of": d.as_of,
            "certain_cents": d.certain_cents,
            "payouts_certain_cents": d.payouts_certain_cents,
            "commissions_cents": d.commissions_cents,
            "open_cents": d.open_cents,
            "expected_cents": d.expected_cents,
            "total_cents": d.total_cents,
            "funded_accounts": d.funded_accounts,
            "largest": None if d.largest is None else claim_view(d.largest),
            "claims": [claim_view(c) for c in d.claims],
            "unread": list(d.unread),
            "open_profit_cents": d.open_profit_cents,
            "largest_customer_cents": d.largest_customer_cents,
            "reserve": reserve,
        }

    @app.get("/staff/books/trial-balance")
    async def trial_balance(
        deps: D, me: SettingsReader, until: Annotated[int | None, Query(ge=0)] = None
    ) -> dict[str, Any]:
        """Every account's balance (debit positive) up to `until` (ms); the
        balances sum to zero."""
        lines = await _books(deps).trial_balance(until)
        return {
            "until": until,
            "lines": [
                {
                    "account": x.account,
                    "type": x.type,
                    "name": x.name,
                    "balance_cents": x.balance_cents,
                }
                for x in lines
            ],
            "total_cents": sum(x.balance_cents for x in lines),
        }

    async def _statement(deps: Deps, view: str, first: str, last: str) -> list[dict[str, Any]]:
        if last < first:
            raise HTTPException(status_code=422, detail="to is before from")
        try:
            return await _books(deps).statement(view, first, last)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/staff/books/income-statement")
    async def income_statement(
        deps: D,
        me: SettingsReader,
        first: Annotated[str, Query(alias="from", pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")],
        last: Annotated[str, Query(alias="to", pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")],
        view: Literal["accrual", "cash"] = "accrual",
    ) -> dict[str, Any]:
        """Income statement by UTC month and product (D-14.17), cents;
        income and costs positive. Product `all` is every product together;
        `none` holds costs of no product."""
        rows = await _statement(deps, view, first, last)
        return {"view": view, "from": first, "to": last, "rows": rows}

    @app.get("/staff/books/income-statement.csv")
    async def income_statement_csv(
        deps: D,
        me: SettingsReader,
        first: Annotated[str, Query(alias="from", pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")],
        last: Annotated[str, Query(alias="to", pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")],
        view: Literal["accrual", "cash"] = "accrual",
    ) -> Response:
        rows = await _statement(deps, view, first, last)
        return Response(
            statement_csv(rows, view),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="income-{view}-{first}-{last}.csv"'
            },
        )

    @app.get("/staff/books/entries")
    async def book_entries(deps: D, me: SettingsReader) -> list[dict[str, Any]]:
        return await _books(deps).entries()

    @app.get("/staff/books/expenses")
    async def book_expenses(deps: D, me: SettingsReader) -> list[dict[str, Any]]:
        return await _books(deps).expenses()

    @app.post("/staff/books/expenses", status_code=201)
    async def propose_expense(deps: D, me: SettingsChanger, body: ExpenseBody) -> dict[str, Any]:
        """Propose an operating expense; it is posted when a second member
        of staff approves it."""
        return await _books(deps).propose_expense(
            body.category,
            body.amount_cents,
            body.memo,
            body.paid,
            body.incurred_at,
            me.subject,
            deps.clock(),
        )

    @app.post("/staff/books/expenses/{exp}/decision")
    async def decide_expense(
        deps: D,
        me: SettingsChanger,
        exp: Annotated[str, Path(pattern=r"^EXP[0-9]{8}$")],
        body: ExpenseDecision,
    ) -> dict[str, Any]:
        try:
            return await _books(deps).decide_expense(
                exp, body.approve, me.subject, body.reason, deps.clock()
            )
        except ExpenseRefused as exc:
            status = {"unknown": 404, "same_person": 403}.get(exc.code, 409)
            raise HTTPException(
                status_code=status, detail={"code": exc.code, "message": str(exc)}
            ) from exc

    def _referrals(deps: Deps) -> Referrals:
        if deps.referrals is None:
            raise HTTPException(status_code=503, detail="referrals are not configured")
        return deps.referrals

    @app.get("/me/referrals")
    async def my_referrals(deps: D, me: Trader) -> dict[str, Any]:
        """The trader's referral code, what it has earned and the ledger
        (D-14.15)."""
        cif = await _cif(deps, me)
        referrals = _referrals(deps)
        policy = (await _policy(deps)).referrals
        now = deps.clock()
        refused = cif.residence_country in policy.refused_countries
        code = None if refused or not policy.on else await referrals.code_for(cif.cif, now)
        b = await referrals.balance(cif.cif, now)
        return {
            "on": policy.on and not refused,
            "code": code,
            "commission_percent": percent_text(policy.commission),
            "holdback_days": policy.holdback_days,
            "referred": b.referred,
            "accrued_cents": b.accrued_cents,
            "available_cents": b.available_cents,
            "entries": [
                {k: e[k] for k in ("ord", "kind", "amount_cents", "available_at", "ts")}
                for e in await referrals.entries(cif.cif)
            ],
        }

    @app.get("/staff/referrals")
    async def staff_referrals(deps: D, me: SettingsReader) -> list[dict[str, Any]]:
        """The referral ledger, newest first."""
        return await _referrals(deps).entries()

    @app.post("/staff/referrals/{ord_id}/clawback")
    async def claw_back(
        deps: D,
        me: CaseWorker,
        ord_id: Annotated[str, Path(pattern=r"^ORD[0-9]{8}$")],
        body: ClawbackBody,
    ) -> dict[str, Any]:
        """Take back the commission on an order (refund, self-referral):
        a new negative entry, kept with who and why."""
        try:
            return await _referrals(deps).claw_back(ord_id, me.subject, body.reason, deps.clock())
        except ReferralRefused as exc:
            status = 404 if exc.code == "unknown" else 409
            raise HTTPException(
                status_code=status, detail={"code": exc.code, "message": str(exc)}
            ) from exc

    def _codes(deps: Deps) -> CodeStore:
        if deps.codes is None:
            raise HTTPException(status_code=503, detail="discount codes are not configured")
        return deps.codes

    def code_view(row: dict[str, Any], now: int) -> dict[str, Any]:
        share = share_of(row["discount"])
        expires = row["expires_at"]
        used_up = row["max_uses"] is not None and row["uses"] >= row["max_uses"]
        ended = expires is not None and expires <= now
        return {
            "code": row["code"],
            "discount_percent": percent_text(share),
            "on": row["active"],
            "valid": bool(row["active"]) and not used_up and not ended,
            "created_at": row["created_at"],
            "created_by": row["created_by"],
            "expires_at": expires,
            "max_uses": row["max_uses"],
            "uses": row["uses"],
            "paid": row["paid"],
            "given_cents": row["given_cents"],
        }

    @app.get("/staff/codes")
    async def list_codes(deps: D, me: SettingsReader) -> list[dict[str, Any]]:
        """Discount codes with their uses and what they gave away (D-14.14)."""
        now = deps.clock()
        return [code_view(r, now) for r in await _codes(deps).codes()]

    @app.post("/staff/codes", status_code=201)
    async def create_code(deps: D, me: SettingsChanger, body: CodeBody) -> dict[str, Any]:
        """A new code, on at once. Capped at the highest code discount any
        product allows; a product with a lower cap refuses it at checkout."""
        policy = await _policy(deps)
        cap = max((p.code_discount_max for p in policy.products), default=Fraction(0))
        share = parse_percent(body.discount_percent, "discount_percent")
        assert share is not None
        if not 0 < share <= cap:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "discount",
                    "message": f"a code takes off more than 0 % and at most {percent_text(cap)} %",
                },
            )
        now = deps.clock()
        if body.expires_at is not None and body.expires_at <= now:
            raise HTTPException(
                status_code=422, detail={"code": "expires_at", "message": "the end has passed"}
            )
        try:
            await _codes(deps).create_code(
                body.code, share, body.expires_at, body.max_uses, me.subject, body.reason, now
            )
        except CodeTakenError:
            raise HTTPException(
                status_code=409, detail={"code": "code_taken", "message": "this code exists"}
            ) from None
        row = next(r for r in await _codes(deps).codes() if r["code"] == body.code)
        return code_view(row, now)

    @app.post("/staff/codes/{code}/switch")
    async def switch_code(
        deps: D,
        me: SettingsChanger,
        code: Annotated[str, Path(pattern=r"^[A-Z0-9-]{3,32}$")],
        body: CodeSwitchBody,
    ) -> dict[str, Any]:
        now = deps.clock()
        if not await _codes(deps).switch_code(code, body.on, me.subject, body.reason, now):
            raise HTTPException(status_code=404, detail="unknown code")
        row = next(r for r in await _codes(deps).codes() if r["code"] == code)
        return {**code_view(row, now), "changes": await _codes(deps).code_changes(code)}

    @app.get("/staff/codes/{code}")
    async def get_code(
        deps: D, me: SettingsReader, code: Annotated[str, Path(pattern=r"^[A-Z0-9-]{3,32}$")]
    ) -> dict[str, Any]:
        row = next((r for r in await _codes(deps).codes() if r["code"] == code), None)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown code")
        return {**code_view(row, deps.clock()), "changes": await _codes(deps).code_changes(code)}

    @app.get("/staff/scaling")
    async def list_scaling(deps: D, me: Approver) -> list[dict[str, Any]]:
        """Scaling steps, newest first: proposed by the daily job, applied
        or rejected by staff (D-14.8)."""
        return await _scaling(deps).recent()

    @app.post("/staff/scaling/run")
    async def run_scaling(deps: D, me: Approver) -> dict[str, Any]:
        """Run the scaling check now, instead of waiting for the hourly run."""
        return {"proposed": await _scaling(deps).run(deps.clock(), every_ms=0)}

    @app.post("/staff/scaling/{scl}/approve")
    async def approve_scaling(deps: D, me: Approver, scl: SclId) -> dict[str, Any]:
        try:
            return await _scaling(deps).approve(scl, me.subject, deps.clock())
        except ScalingRefused as exc:
            raise _scaling_refused(exc) from exc

    @app.post("/staff/scaling/{scl}/reject")
    async def reject_scaling(deps: D, me: Approver, scl: SclId) -> dict[str, Any]:
        try:
            return await _scaling(deps).reject(scl, me.subject, deps.clock())
        except ScalingRefused as exc:
            raise _scaling_refused(exc) from exc

    @app.get("/staff/corrections")
    async def list_corrections(deps: D, me: CaseWorker) -> list[dict[str, Any]]:
        return await _corrections(deps).recent()

    @app.get("/staff/corrections/suspects")
    async def suspect_prices(
        deps: D,
        me: CaseWorker,
        hours: Annotated[int, Query(ge=1, le=24 * 31)] = 72,
        min_basis_points: Annotated[int, Query(ge=1, le=10_000)] = 20,
    ) -> list[dict[str, Any]]:
        """Spikes in the stored prices (a move out and straight back), newest
        first, with the fills each caused: candidates for a correction."""
        since = deps.clock() - hours * 3_600_000
        return await _corrections(deps).suspects(since, min_basis_points)

    @app.get("/staff/corrections/preview")
    async def preview_correction(
        deps: D,
        me: CaseWorker,
        symbol: Annotated[str, Query(min_length=3, max_length=20)],
        price_seq: Annotated[int, Query(ge=1)],
        good_price: Annotated[int, Query(ge=1)],
    ) -> dict[str, Any]:
        """What a correction would do, before it is proposed."""
        try:
            update, items = await _corrections(deps).preview(symbol, price_seq, good_price)
        except CorrectionRefused as exc:
            raise _correction_refused(exc) from exc
        return {
            "bad_price": update.price,
            "price_ts": update.receive_ts,
            "items": [i.view() for i in items],
        }

    @app.post("/staff/corrections", status_code=201)
    async def propose_correction(deps: D, me: CaseWorker, body: CorrectionBody) -> dict[str, Any]:
        """Flag an applied price as bad (D-14.4); a second person approves."""
        try:
            return await _corrections(deps).propose(
                body.symbol, body.price_seq, body.good_price, body.reason, me.subject, deps.clock()
            )
        except CorrectionRefused as exc:
            raise _correction_refused(exc) from exc

    @app.get("/staff/corrections/{cor}")
    async def get_correction(deps: D, me: CaseWorker, cor: CorId) -> dict[str, Any]:
        found = await _corrections(deps).get(cor)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown correction")
        return found

    @app.post("/staff/corrections/{cor}/approve")
    async def approve_correction(deps: D, me: CaseWorker, cor: CorId) -> dict[str, Any]:
        """The second person: the balances are corrected and the accounts the
        bad price breached reinstated."""
        try:
            return await _corrections(deps).approve(cor, me.subject, deps.clock())
        except CorrectionRefused as exc:
            raise _correction_refused(exc) from exc

    @app.post("/staff/corrections/{cor}/reject")
    async def reject_correction(deps: D, me: CaseWorker, cor: CorId) -> dict[str, Any]:
        try:
            return await _corrections(deps).reject(cor, me.subject, deps.clock())
        except CorrectionRefused as exc:
            raise _correction_refused(exc) from exc

    boards: dict[int, Leaderboard] = {}

    def _board(deps: Deps) -> Leaderboard:
        store = deps.leaderboard
        if store is None:
            raise HTTPException(status_code=404, detail="no leaderboard")
        board = boards.get(id(deps))
        if board is None:
            board = boards[id(deps)] = Leaderboard(store.leaderboard_entrants, deps.live.account)
        return board

    @app.get("/leaderboard")
    async def leaderboard(deps: D) -> dict[str, Any]:
        """The public leaderboard: active funded accounts by gain on the
        starting balance (first name and initial, country); refreshed at
        most once a minute. Traders who opted out are left off."""
        rows = await _board(deps).rows(deps.clock())
        return {"rows": [r.view() for r in rows], "refresh_ms": LEADERBOARD_TTL_MS}

    @app.get("/me/leaderboard")
    async def my_leaderboard(deps: D, me: Trader) -> dict[str, Any]:
        cif = await _cif(deps, me)
        store = deps.leaderboard
        visible = True if store is None else await store.leaderboard_visible(cif.cif)
        return {"visible": visible}

    @app.put("/me/leaderboard")
    async def set_my_leaderboard(deps: D, me: Trader, body: LeaderboardBody) -> dict[str, Any]:
        """Show or hide my funded accounts on the public leaderboard."""
        cif = await _cif(deps, me)
        store = deps.leaderboard
        if store is None:
            raise HTTPException(status_code=404, detail="no leaderboard")
        await store.set_leaderboard_visible(cif.cif, body.visible, deps.clock())
        return {"visible": body.visible}

    @app.get("/me/analysis")
    async def my_analysis(
        deps: D,
        me: Trader,
        account: Annotated[str | None, Query(pattern=r"^ACC[0-9]{8}$")] = None,
        since: Annotated[int, Query(ge=0)] = 0,
        until: Annotated[int | None, Query(ge=0)] = None,
    ) -> dict[str, Any]:
        """Trader's analysis: figures from the trader's own closed trades,
        over all their accounts or one, in [since, until) (ms UTC)."""
        cif = await _cif(deps, me)
        owned = (
            [a.acc for a in await deps.customers.accounts_for(cif.cif)] if deps.customers else []
        )
        if account is not None:
            if account not in owned:
                raise HTTPException(status_code=404, detail="unknown account")
            owned = [account]
        events: list[dict[str, Any]] = []
        for acc in owned:
            after = 0
            while True:
                page = await deps.history.account_events(acc, after, 1_000)
                events += page
                if len(page) < 1_000:
                    break
                after = int(page[-1]["seq"])
        return {"accounts": owned, **analyse(trades_from(events), since, until)}

    @app.get("/me/retakes")
    async def my_retakes(deps: D, me: Trader) -> list[dict[str, Any]]:
        """Breached accounts the trader may retake at the retake price (D-13.6)."""
        cif = await _cif(deps, me)
        policy = await _policy(deps)
        out = []
        for order in await _checkout(deps).retake_offers(cif.cif, deps.clock()):
            product = policy.product(order.product_id)
            on_sale = dict(policy.catalogue.sizes(product))
            if product.id in policy.catalogue.products_off or order.size_cents not in on_sale:
                continue  # not on sale now
            quote = price(product, order.size_cents, 1, retake=True)
            out.append(
                {
                    "of_ord": order.ord,
                    "product_id": product.id,
                    "size_cents": order.size_cents,
                    "list_price_cents": quote.list_price_cents,
                    "discount_percent": percent_text(quote.discount),
                    "price_cents": quote.price_cents,
                }
            )
        return out

    @app.get("/me/wallet")
    async def my_wallet(deps: D, me: Trader) -> dict[str, Any]:
        cif = await _cif(deps, me)
        wallet = await _payouts(deps).store.wallet(cif.cif)
        if wallet is None:
            raise HTTPException(status_code=404, detail="no payout wallet")
        return wallet_view(wallet)

    @app.post("/me/payouts/{po}/cancel")
    async def cancel_payout(deps: D, me: Trader, po: PoId) -> dict[str, Any]:
        cif = await _cif(deps, me)
        try:
            return payout_view(await _payouts(deps).cancel(po, cif, me.subject, deps.clock()))
        except PayoutRefused as exc:
            raise _payout_refused(exc) from exc

    # --- funded onboarding --------------------------------------------------------

    async def verification_url(deps: Deps, record: OnboardingRecord) -> str | None:
        if record.status != "verification" or record.kyc is None:
            return None
        store = _onboarding(deps).store
        case = await store.case(record.kyc)
        if case is None or await store.result(record.kyc) is not None:
            return None
        return case.url

    @app.post("/me/onboardings/{onb}/verification")
    async def start_verification(deps: D, me: Trader, onb: OnbId) -> dict[str, Any]:
        """Open (or return) the identity check; answers where to complete it."""
        cif = await _cif(deps, me)
        try:
            record = await _onboarding(deps).start_verification(onb, cif, deps.clock())
        except OnboardingStateError as exc:
            raise _onboarding_refused(exc) from exc
        except KycError as exc:
            raise HTTPException(status_code=502, detail="identity provider unavailable") from exc
        return {**onboarding_view(record), "verification_url": await verification_url(deps, record)}

    @app.get("/me/onboardings/{onb}")
    async def my_onboarding(deps: D, me: Trader, onb: OnbId) -> dict[str, Any]:
        cif = await _cif(deps, me)
        record = await _onboarding(deps).store.onboarding(onb)
        if record is None or record.cif != cif.cif:
            raise HTTPException(status_code=404, detail="unknown onboarding")
        return {**onboarding_view(record), "verification_url": await verification_url(deps, record)}

    @app.get("/me/onboardings/{onb}/contract")
    async def contract(deps: D, me: Trader, onb: OnbId) -> dict[str, Any]:
        """The agreement to read before signing (and after)."""
        cif = await _cif(deps, me)
        store = _onboarding(deps).store
        record = await store.onboarding(onb)
        if record is None or record.cif != cif.cif or record.contract is None:
            raise HTTPException(status_code=404, detail="no contract")
        found = await store.contract(record.contract)
        assert found is not None
        return {
            "contract": found.contract,
            "template": found.template,
            "sha256": found.sha256,
            "draft": found.draft,
            "body": found.body,
            "created_at": found.created_at,
        }

    @app.post("/me/onboardings/{onb}/sign")
    async def sign(
        deps: D, me: Trader, onb: OnbId, body: SignBody, request: Request
    ) -> dict[str, Any]:
        """Sign as the verified person: type the name on the identity document
        and confirm the SHA-256 of the text read."""
        cif = await _cif(deps, me)
        signature = Signature(
            subject=me.subject,
            typed_name=body.full_name,
            sha256=body.sha256,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        try:
            record = await _onboarding(deps).sign(onb, cif, signature, deps.clock())
        except OnboardingStateError as exc:
            raise _onboarding_refused(exc) from exc
        return onboarding_view(record)

    @app.post("/webhooks/didit")
    async def didit_webhook(deps: D, request: Request) -> dict[str, Any]:
        """Didit's notice that a session changed: checked, then the outcome is
        read from Didit itself."""
        secret = deps.didit_webhook_secret
        onboarding = deps.onboarding
        if not secret or onboarding is None or onboarding.kyc.name != "didit":
            raise HTTPException(status_code=404, detail="not found")
        body = await request.body()
        valid = didit_webhook_valid(
            body,
            request.headers.get("x-signature-v2"),
            request.headers.get("x-timestamp"),
            secret,
            deps.clock() // 1000,
        )
        if not valid:
            raise HTTPException(status_code=401, detail="invalid signature")
        session_id = (await request.json()).get("session_id")
        if not isinstance(session_id, str):
            return {"ok": True}
        try:
            await onboarding.kyc_update("didit", session_id, deps.clock())
        except KycError as exc:  # Didit retries; the poll catches up too
            raise HTTPException(status_code=503, detail="decision not readable yet") from exc
        return {"ok": True}

    @app.post("/dev/kyc/{session_id}/complete")
    async def sandbox_kyc(
        deps: D, me: Trader, session_id: Ident, body: SandboxKycBody
    ) -> dict[str, Any]:
        """Sandbox only: pretend the customer finished the identity check."""
        onboarding = deps.onboarding
        if not deps.sandbox or onboarding is None or not isinstance(onboarding.kyc, SandboxKyc):
            raise HTTPException(status_code=404, detail="not found")
        cif = await _cif(deps, me)
        case = await onboarding.store.case_by_session("sandbox", session_id)
        if case is None or case.cif != cif.cif:
            raise HTTPException(status_code=404, detail="unknown session")
        if body.result == "declined":
            decision = KycDecision("declined", detail={"status": "Declined"})
        else:
            decision = KycDecision(
                "approved",
                full_name=body.full_name or cif.full_name,
                date_of_birth=date.fromisoformat(body.date_of_birth)
                if body.date_of_birth
                else cif.date_of_birth,
                document_country=alpha2(body.document_country) or cif.residence_country,
                detail={"status": "Approved"},
            )
        onboarding.kyc.complete(session_id, decision)
        await onboarding.kyc_update("sandbox", session_id, deps.clock())
        record = await onboarding.store.waiting_on(case.kyc)
        return {"kyc": case.kyc, "result": body.result, "still_waiting": record}

    @app.post("/me", status_code=201)
    async def register(deps: D, me: Trader, body: RegisterBody, request: Request) -> dict[str, Any]:
        try:
            dob = date.fromisoformat(body.date_of_birth)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="date_of_birth is not a date") from exc
        ip_country = request.headers.get(deps.country_header) if deps.country_header else None
        if ip_country is not None and not (len(ip_country) == 2 and ip_country.isupper()):
            ip_country = None  # unknown or malformed: no signal
        mode = await _mode(deps)
        if mode.maintenance:
            raise _closed("maintenance", "the site is under maintenance; try again later")
        if not mode.registering:
            raise _closed("registration_closed", "registration is closed for now")
        form = Registration(body.full_name, dob, body.residence_country)
        try:
            cif = await _checkout(deps).register(
                me.subject, me.email, form, deps.today(), deps.clock(), ip_country
            )
        except AlreadyRegisteredError as exc:
            raise HTTPException(status_code=409, detail="already registered") from exc
        except CheckoutError as exc:
            raise _refused(exc) from exc
        view = cif_view(cif)
        if body.ref is not None and deps.referrals is not None:
            try:
                await deps.referrals.attribute(cif.cif, body.ref, deps.clock())
                view["referral"] = {"accepted": True, "code": None}
            except ReferralRefused as exc:
                view["referral"] = {"accepted": False, "code": exc.code}
        return view

    @app.post("/orders", status_code=201)
    async def purchase(deps: D, me: Trader, body: PurchaseBody) -> dict[str, Any]:
        cif = await _cif(deps, me)
        if not (await _mode(deps)).selling:  # rule AC17
            raise _closed("maintenance", "challenges are not on sale during maintenance")
        if deps.settings is not None and await deps.settings.pending_pages(me.subject):
            raise HTTPException(  # rule AC10: accept the policies in force first
                status_code=409,
                detail={"code": "policies_pending", "message": "accept the updated policies"},
            )
        await check_method(deps, body)
        try:
            order, pay_url = await _checkout(deps).place_order(
                cif, body.product_id, body.size_cents, body.code, deps.clock(), tuple(body.addons)
            )
        except CheckoutError as exc:
            raise _refused(exc) from exc
        return {**order_view(order), "pay_url": pay_url}

    @app.get("/trial")
    async def trial_offer(deps: D) -> dict[str, Any]:
        """The free trial on offer (D-13.17); public."""
        policy = await _policy(deps)
        t = policy.trial
        return {
            "on": t.on,
            "product_id": t.product_id,
            "size_cents": t.size_cents,
            "days": t.days,
            "stage": rule_set_view(policy.trial_rule_set()) if t.on else None,
        }

    @app.post("/me/trial", status_code=201)
    async def start_trial(deps: D, me: Trader) -> dict[str, Any]:
        """Open the trader's free trial: no payment, one per customer."""
        cif = await _cif(deps, me)
        if not (await _mode(deps)).selling:  # rule AC17
            raise _closed("maintenance", "trials do not start during maintenance")
        if deps.settings is not None and await deps.settings.pending_pages(me.subject):
            raise HTTPException(  # rule AC10: accept the policies in force first
                status_code=409,
                detail={"code": "policies_pending", "message": "accept the updated policies"},
            )
        try:
            order = await _checkout(deps).start_trial(cif, deps.clock())
        except CheckoutError as exc:
            raise _refused(exc) from exc
        return order_view(order)

    @app.get("/orders/{ord_id}")
    async def get_order(deps: D, me: Trader, ord_id: Ident) -> dict[str, Any]:
        cif = await _cif(deps, me)
        order = await deps.customers.order(ord_id)
        if order is None or order.cif != cif.cif:
            raise HTTPException(status_code=404, detail="unknown order")
        return order_view(order)

    @app.post("/dev/payments/{ord_id}/complete")
    async def sandbox_pay(deps: D, me: Trader, ord_id: Ident) -> dict[str, Any]:
        """Sandbox only: pretend the customer paid the order."""
        if not deps.sandbox:
            raise HTTPException(status_code=404, detail="not found")
        cif = await _cif(deps, me)
        order = await deps.customers.order(ord_id)
        if order is None or order.cif != cif.cif:
            raise HTTPException(status_code=404, detail="unknown order")
        try:
            paid = await _checkout(deps).payment_received(
                order.provider, order.provider_ref, deps.clock()
            )
        except CheckoutError as exc:
            raise _refused(exc) from exc
        return order_view(paid)

    # --- trading ----------------------------------------------------------------------

    @app.get("/quotes")
    async def quotes(deps: D, me: Me) -> dict[str, dict[str, Any]]:
        return await deps.live.quotes()

    @app.get("/news")
    async def news(deps: D, me: Me) -> list[dict[str, Any]]:
        """Restricted events not over yet (M13): no trade opens or closes on
        their symbols from `start_ts` to `end_ts` (ms UTC); `title` from the
        calendar or the risk desk (null if neither names the event)."""
        titles = await deps.news.titles() if deps.news is not None else {}
        return [{**e, "title": titles.get(e["event_id"])} for e in await deps.live.news()]

    @app.get("/calendar")
    async def calendar(deps: D) -> list[dict[str, Any]]:
        """The restricted-events calendar, public (it is the firm's published
        rule, as other firms publish theirs): the events not over yet, as the
        engine holds them, with `release_ts` (ms UTC), the window
        [`start_ts`, `end_ts`), the symbols, a title, the currency and the
        publisher's schedule page where the firm's calendar files name them."""
        titles = await deps.news.titles() if deps.news is not None else {}
        impacts = await deps.news.impacts() if deps.news is not None else {}
        listed = {
            e.event.event_id: e
            for c in (deps.news.calendars if deps.news is not None else ())
            for e in c.events
        }
        out = []
        for e in await deps.live.news():
            item = listed.get(e["event_id"])
            out.append(
                {
                    **e,
                    "title": titles.get(e["event_id"]),
                    "release_ts": item.event.ts if item else None,
                    "currency": item.event.currency if item else None,
                    "source": item.source if item else None,
                    # marked by the risk desk; every event here is restricted
                    "impact": impacts.get(e["event_id"], DEFAULT_IMPACT),
                    "restricted": True,
                }
            )
        return sorted(out, key=lambda x: (x["start_ts"], x["event_id"]))

    @app.get("/market")
    async def market(deps: D, me: Me) -> dict[str, Any]:
        """Where prices come from (to name beside every chart and price
        table), the symbols, and the bar sizes."""
        policy = await _policy(deps)
        return {
            "source": deps.price_source,
            "attribution": ATTRIBUTION[deps.price_source],
            "symbols": [{"symbol": i.name, "digits": i.digits} for i in policy.trading.instruments],
            "intervals": list(INTERVALS),
        }

    @app.get("/bars")
    async def bars(
        deps: D,
        me: Me,
        symbol: Annotated[str, Query(pattern=r"^[A-Z0-9]{2,10}/[A-Z0-9]{2,10}$")],
        interval: Annotated[str, Query(pattern="^(" + "|".join(INTERVALS) + ")$")] = "1m",
        limit: Annotated[int, Query(ge=1, le=1_000)] = 300,
        before: Annotated[int | None, Query(ge=0)] = None,
    ) -> list[dict[str, int]]:
        """Bars of one symbol from the firm's own prices (points; `time`:
        the bar's start, ms UTC). Signed-in users only (price licence)."""
        policy = await _policy(deps)
        if symbol not in {i.name for i in policy.trading.instruments}:
            raise HTTPException(status_code=404, detail="unknown symbol")
        return await deps.history.bars(symbol, interval, limit, before)

    @app.get("/accounts/{account_id}/stream")
    async def stream(request: Request, deps: D, me: Me, account_id: Ident) -> StreamingResponse:
        """Server-sent events: `account` (the live state) and `quotes`
        whenever they change, a comment every 15 s otherwise, and `end`
        after `stream_max_s` (the client connects again with a fresh token)."""
        await _may_read(deps, me, account_id)
        if await deps.live.account(account_id) is None:
            raise HTTPException(status_code=404, detail="unknown account")

        async def events() -> AsyncIterator[str]:
            sent: dict[str, str] = {}
            started = last = time.monotonic()
            while time.monotonic() - started < deps.stream_max_s:
                if await request.is_disconnected():
                    return
                found = {
                    "account": await deps.live.account(account_id),
                    "quotes": await deps.live.quotes(),
                }
                for name, value in found.items():
                    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
                    if sent.get(name) != text:
                        sent[name] = text
                        last = time.monotonic()
                        yield f"event: {name}\ndata: {text}\n\n"
                if time.monotonic() - last >= 15:
                    last = time.monotonic()
                    yield ": keep-alive\n\n"
                await asyncio.sleep(deps.stream_poll_s)
            yield "event: end\ndata: {}\n\n"

        headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
        return StreamingResponse(events(), media_type="text/event-stream", headers=headers)

    @app.get("/accounts/{account_id}")
    async def account(deps: D, me: Me, account_id: Ident) -> dict[str, Any]:
        await _may_read(deps, me, account_id)
        found = await deps.live.account(account_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown account")
        return found

    @app.get("/accounts/{account_id}/payout")
    async def payout(deps: D, me: Me, account_id: Ident) -> dict[str, Any]:
        """The account's payable profit and every payout eligibility check,
        now. Readable by its owner and by employees."""
        await _may_read(deps, me, account_id)
        try:
            found = await _payouts(deps).eligibility(account_id, deps.clock())
        except PayoutRefused as exc:
            raise _payout_refused(exc) from exc
        return eligibility_view(found)

    @app.post("/accounts/{account_id}/payouts", status_code=201)
    async def request_payout(deps: D, me: Trader, account_id: Ident) -> dict[str, Any]:
        """Ask for the payout the eligibility shows now (MFA login, by policy)."""
        await _owns(deps, me, account_id)
        cif = await _cif(deps, me)
        try:
            record = await _payouts(deps).request(
                account_id, cif, me.subject, me.mfa, deps.clock(), me.auth_time
            )
        except PayoutRefused as exc:
            raise _payout_refused(exc) from exc
        return payout_view(record)

    @app.get("/accounts/{account_id}/payouts")
    async def account_payouts(deps: D, me: Me, account_id: Ident) -> list[dict[str, Any]]:
        await _may_read(deps, me, account_id)
        return [payout_view(p) for p in await _payouts(deps).store.payouts(acc=account_id)]

    @app.get("/accounts/{account_id}/history")
    async def history(
        deps: D,
        me: Me,
        account_id: Ident,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
    ) -> list[dict[str, Any]]:
        await _may_read(deps, me, account_id)
        return await deps.history.account_events(account_id, after, limit)

    @app.get("/inputs/{entry_id}")
    async def outcome(
        deps: D, me: Me, entry_id: Annotated[str, Path(pattern=r"^[0-9]+-[0-9]+$")]
    ) -> JSONResponse:
        result = await deps.gateway.outcome(entry_id, 0)
        accounts = {e.get("account_id") for e in result.events if isinstance(e, dict)}
        accounts.discard(None)
        if not (await _staff_access(deps, me)).has("staff.view"):
            _role(me, TRADER_ROLES)
            if not accounts:
                raise HTTPException(status_code=404, detail="unknown input")
            for acc in accounts:
                await _owns(deps, me, str(acc))
        return _answer(result)

    async def no_payout_pending(deps: Deps, account_id: str) -> None:
        if deps.payouts is not None:
            paused = (await _policy(deps)).payouts.trading_during_payout == "paused"
            if paused and await deps.payouts.store.in_progress(account_id) is not None:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "payout_pending", "message": "no new orders until paid out"},
                )

    @app.post("/accounts/{account_id}/orders")
    async def place(deps: D, me: Trader, account_id: Ident, body: OrderBody) -> JSONResponse:
        await _owns(deps, me, account_id)
        await no_payout_pending(deps, account_id)

        def build(ts: int) -> Input:
            request = OrderRequest(
                order_id=body.order_id or deps.new_id(),
                account_id=account_id,
                symbol=body.symbol,
                side=Side(body.side),
                order_type=OrderType(body.type),
                volume=body.volume,
                level=body.level,
                stop_loss=body.stop_loss,
                take_profit=body.take_profit,
            )
            return PlaceOrder(request, ts)

        return await submit(deps, build)

    @app.post("/accounts/{account_id}/trailing-orders")
    async def place_trailing(
        deps: D, me: Trader, account_id: Ident, body: TrailingEntryBody
    ) -> JSONResponse:
        """A trailing buy (follows the ask) or trailing sell (follows the bid);
        cancelled like any order (DELETE .../orders/{order_id})."""
        await _owns(deps, me, account_id)
        await no_payout_pending(deps, account_id)
        order_id = body.order_id or deps.new_id()
        return await submit(
            deps,
            lambda ts: PlaceTrailingEntry(
                order_id,
                account_id,
                body.symbol,
                Side(body.side),
                body.volume,
                body.distance,
                body.step,
                ts,
            ),
        )

    @app.put("/accounts/{account_id}/positions/{position_id}/trailing-stop")
    async def trailing_stop(
        deps: D, me: Trader, account_id: Ident, position_id: Ident, body: TrailingStopBody
    ) -> JSONResponse:
        """Set the position's trailing stop, replacing any it has; removed
        with DELETE .../orders/{order_id}."""
        await _owns(deps, me, account_id)
        order_id = body.order_id or deps.new_id()
        return await submit(
            deps,
            lambda ts: SetTrailingStop(
                order_id, account_id, position_id, body.distance, body.step, ts
            ),
        )

    @app.delete("/accounts/{account_id}/orders/{order_id}")
    async def cancel(deps: D, me: Trader, account_id: Ident, order_id: Ident) -> JSONResponse:
        await _owns(deps, me, account_id)
        return await submit(deps, lambda ts: CancelOrder(account_id, order_id, ts))

    @app.post("/accounts/{account_id}/positions/{position_id}/close")
    async def close(
        deps: D, me: Trader, account_id: Ident, position_id: Ident, body: CloseBody
    ) -> JSONResponse:
        await _owns(deps, me, account_id)
        return await submit(
            deps, lambda ts: ClosePosition(account_id, position_id, ts, body.volume)
        )

    @app.put("/accounts/{account_id}/positions/{position_id}/stops")
    async def stops(
        deps: D, me: Trader, account_id: Ident, position_id: Ident, body: StopsBody
    ) -> JSONResponse:
        await _owns(deps, me, account_id)
        return await submit(
            deps,
            lambda ts: ModifyStops(account_id, position_id, body.stop_loss, body.take_profit, ts),
        )

    # --- employees: stage progression ------------------------------------------------

    @app.get("/staff/progressions")
    async def progressions(
        deps: D,
        me: Viewer,
        status: STATUSES = "review",
        limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
    ) -> list[dict[str, Any]]:
        """Progressions in one status; by default the funded reviews waiting."""
        found = await _lifecycle(deps).store.with_status(status, limit)
        return [progression_view(p) for p in found]

    @app.get("/staff/progressions/{prg}")
    async def progression(deps: D, me: Viewer, prg: PrgId) -> dict[str, Any]:
        """One progression with its full history."""
        store = _lifecycle(deps).store
        record = await store.progression(prg)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown progression")
        history = [
            {"kind": e.kind, "ts": e.ts, "detail": dict(e.detail)} for e in await store.history(prg)
        ]
        waiting = proposal_json(open_proposal(await store.history(prg)))
        return {**progression_view(record), "proposal": waiting, "history": history}

    async def decide(deps: Deps, me: Principal, prg: str, approve: bool, reason: str) -> Any:
        try:
            record = await _lifecycle(deps).decide(
                prg, approve, me.subject, me.email, reason, deps.clock()
            )
        except DecisionError as exc:
            status = {
                "unknown": 404,
                "not_in_review": 409,
                "own_progression": 403,
                "four_eyes": 403,
                "open_case": 409,
            }[exc.code]
            raise HTTPException(
                status_code=status, detail={"code": exc.code, "message": str(exc)}
            ) from exc
        waiting = open_proposal(await _lifecycle(deps).store.history(prg))
        return {**progression_view(record), "proposal": proposal_json(waiting)}

    @app.post("/staff/progressions/{prg}/approve")
    async def approve(deps: D, me: Approver, prg: PrgId, body: DecisionBody) -> dict[str, Any]:
        """Approve a funded review: funded onboarding follows (step 3b). Rule
        AC00: the first decision is a proposal; a second person, never the
        proposer, carries it out (the same decision) or rejects it."""
        result: dict[str, Any] = await decide(deps, me, prg, True, body.reason)
        return result

    @app.post("/staff/progressions/{prg}/deny")
    async def deny(deps: D, me: Approver, prg: PrgId, body: DecisionBody) -> dict[str, Any]:
        result: dict[str, Any] = await decide(deps, me, prg, False, body.reason)
        return result

    # --- employees: identity reviews ---------------------------------------------------

    @app.get("/staff/onboardings")
    async def onboardings(
        deps: D,
        me: Viewer,
        status: ONB_STATUSES = "review",
        limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
    ) -> list[dict[str, Any]]:
        """Onboardings in one status; by default the identity reviews waiting."""
        found = await _onboarding(deps).store.with_status(status, limit)
        return [onboarding_view(o) for o in found]

    @app.get("/staff/onboardings/{onb}")
    async def onboarding(deps: D, me: Viewer, onb: OnbId) -> dict[str, Any]:
        """One onboarding with its full history and the verified identity."""
        store = _onboarding(deps).store
        record = await store.onboarding(onb)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown onboarding")
        history = [
            {"kind": e.kind, "ts": e.ts, "detail": dict(e.detail)} for e in await store.history(onb)
        ]
        verified = None
        if record.kyc is not None:
            result = await store.result(record.kyc)
            if result is not None:
                verified = {
                    "kyc": result.kyc,
                    "result": result.result,
                    "full_name": result.full_name,
                    "date_of_birth": result.date_of_birth.isoformat()
                    if result.date_of_birth
                    else None,
                    "document_country": result.document_country,
                }
        waiting = proposal_json(open_proposal(await store.history(onb)))
        return {
            **onboarding_view(record),
            "verified": verified,
            "proposal": waiting,
            "history": history,
        }

    async def decide_onboarding(
        deps: Deps, me: Principal, onb: str, approve: bool, reason: str
    ) -> dict[str, Any]:
        try:
            record = await _onboarding(deps).decide(
                onb, approve, me.subject, me.email, reason, deps.clock()
            )
        except OnboardingStateError as exc:
            raise _onboarding_refused(exc) from exc
        waiting = open_proposal(await _onboarding(deps).store.history(onb))
        return {**onboarding_view(record), "proposal": proposal_json(waiting)}

    @app.post("/staff/onboardings/{onb}/approve")
    async def approve_onboarding(
        deps: D, me: IdentityReviewer, onb: OnbId, body: DecisionBody
    ) -> dict[str, Any]:
        """Accept a verified identity that differs from the customer record.
        Rule AC00: two people, as a funded review."""
        return await decide_onboarding(deps, me, onb, True, body.reason)

    @app.post("/staff/onboardings/{onb}/deny")
    async def deny_onboarding(
        deps: D, me: IdentityReviewer, onb: OnbId, body: DecisionBody
    ) -> dict[str, Any]:
        return await decide_onboarding(deps, me, onb, False, body.reason)

    # --- employees: integrity, fraud and compliance cases --------------------------

    @app.post("/staff/cases", status_code=201)
    async def raise_alert(deps: D, me: CaseWorker, body: AlertBody) -> dict[str, Any]:
        """An employee's alert: joins the customer's case not closed, or opens one."""
        cif = await deps.customers.cif(body.cif)
        if cif is None:
            raise HTTPException(status_code=404, detail="unknown customer")
        if cif.subject == me.subject:
            raise _case_refused(CaseRefused("own_case", "nobody raises a case about themselves"))
        if body.acc is not None:
            account = await deps.customers.account(body.acc)
            if account is None or account.cif != body.cif:
                raise HTTPException(status_code=422, detail="not this customer's account")
        detail = {"reason": body.reason, "evidence": body.evidence, "by": me.subject}
        cas, alr = await _cases(deps).alert(
            body.cif,
            body.acc,
            f"staff:{me.subject}",
            body.kind,
            body.severity,
            detail,
            deps.clock(),
        )
        record = await _cases(deps).store.case(cas)
        assert record is not None
        return {**case_view(record), "alert": alr}

    def _traders(deps: Deps) -> Traders:
        if deps.traders is None:
            raise HTTPException(status_code=503, detail="trader directory not available")
        return deps.traders

    @app.get("/staff/traders")
    async def list_traders(
        request: Request,
        deps: D,
        me: Viewer,
        segment: SEGMENT = "all",
        q: Annotated[str | None, Query(min_length=2, max_length=80)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Traders by status (rule AC19), newest first, with each segment's
        count; an apprentice sees names and emails masked, and searches by
        CIF only (a name or email search would confirm what the mask hides)."""
        d = _traders(deps)
        by_cif = bool(getattr(request.state, "mask_pii", False))
        found = await d.traders(segment, q, limit, offset, cif_only=by_cif)
        return {"counts": await d.counts(), "traders": found}

    def _reports(deps: Deps) -> ReportSource:
        if deps.reports is None:
            raise HTTPException(status_code=503, detail="reports not available")
        return deps.reports

    def _tx_filter(
        type: TX_TYPE | None,
        status: TX_STATUS | None,
        cif: str | None,
        remark: str | None,
        since: int | None,
        until: int | None,
    ) -> TxFilter:
        return TxFilter(type, status, cif, remark, since, until)

    @app.get("/staff/reports/transactions")
    async def transaction_log(
        deps: D,
        me: Viewer,
        type: TX_TYPE | None = None,
        status: TX_STATUS | None = None,
        cif: Annotated[str | None, Query(pattern=r"^CIF[0-9]{8}$")] = None,
        remark: Annotated[str | None, Query(min_length=2, max_length=80)] = None,
        since: Annotated[int | None, Query(ge=0)] = None,
        until: Annotated[int | None, Query(ge=0)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The transaction log (rule AC20): purchases (money in) and payouts
        (money out), newest first, filtered by type, status, trader, remark
        and time (epoch ms, `until` excluded); totals count money once
        settled."""
        f = _tx_filter(type, status, cif, remark, since, until)
        return await _reports(deps).transactions(f, limit, offset)

    @app.get("/staff/reports/transactions/export")
    async def transaction_export(
        deps: D,
        me: Annotated[Principal, Depends(_can("staff.view"))],
        type: TX_TYPE | None = None,
        status: TX_STATUS | None = None,
        cif: Annotated[str | None, Query(pattern=r"^CIF[0-9]{8}$")] = None,
        remark: Annotated[str | None, Query(min_length=2, max_length=80)] = None,
        since: Annotated[int | None, Query(ge=0)] = None,
        until: Annotated[int | None, Query(ge=0)] = None,
    ) -> Response:
        """The filtered transaction log as CSV, at most EXPORT_LIMIT rows
        (staff with full view; an apprentice does not export)."""
        f = _tx_filter(type, status, cif, remark, since, until)
        found = await _reports(deps).transactions(f, EXPORT_LIMIT + 1, 0)
        rows = found["rows"]
        if len(rows) > EXPORT_LIMIT:
            raise HTTPException(
                status_code=422, detail=f"over {EXPORT_LIMIT} rows: narrow the filters"
            )
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\r\n").writerows(export_rows(rows))
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="transactions.csv"'},
        )

    @app.get("/staff/reports/emails")
    async def email_log(
        deps: D,
        me: Viewer,
        delivery: Literal["sent", "failed", "waiting"] | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, Any]]:
        """Emails sent to traders (rule AC20), newest first, with the latest
        delivery state; subjects only, never bodies."""
        return await _reports(deps).emails(delivery, limit, offset)

    @app.get("/staff/traders/{cif}")
    async def get_trader(
        deps: D, me: Viewer, cif: Annotated[str, Path(pattern=r"^CIF[0-9]{8}$")]
    ) -> dict[str, Any]:
        """One trader's file (rule AC19): accounts, purchases, identity checks,
        cases, payouts, emails sent and registration events."""
        found = await _traders(deps).trader(cif)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown trader")
        return found

    @app.get("/staff/cases")
    async def list_cases(
        deps: D,
        me: Viewer,
        status: CASE_STATUSES | None = None,
        cif: Annotated[str | None, Query(pattern=r"^CIF[0-9]{8}$")] = None,
        overdue: bool = False,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
    ) -> list[dict[str, Any]]:
        """Cases by status or customer, soonest due first; `overdue`: past due, not closed."""
        found = await _cases(deps).store.cases(
            status, cif, deps.clock() if overdue else None, limit
        )
        return [case_view(c) for c in found]

    @app.get("/staff/cases/{cas}")
    async def get_case(deps: D, me: Viewer, cas: CasId) -> dict[str, Any]:
        store = _cases(deps).store
        record = await store.case(cas)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown case")
        alerts = [
            {
                "alr": a.alr,
                "acc": a.acc,
                "source": a.source,
                "kind": a.kind,
                "severity": a.severity,
                "ts": a.ts,
                "detail": a.detail,
            }
            for a in await store.alerts(cas)
        ]
        history = [
            {"kind": e.kind, "ts": e.ts, "detail": dict(e.detail)} for e in await store.history(cas)
        ]
        return {**case_view(record), "alerts": alerts, "history": history}

    async def case_step(deps: Deps, step: Callable[[Casework], Awaitable[CaseRecord]]) -> Any:
        try:
            return case_view(await step(_cases(deps)))
        except CaseRefused as exc:
            raise _case_refused(exc) from exc

    @app.post("/staff/cases/{cas}/assign")
    async def assign_case(deps: D, me: CaseWorker, cas: CasId) -> dict[str, Any]:
        """Take the case."""
        result: dict[str, Any] = await case_step(
            deps, lambda c: c.assign(cas, me.subject, deps.clock())
        )
        return result

    @app.post("/staff/cases/{cas}/notes")
    async def note_case(deps: D, me: CaseWorker, cas: CasId, body: NoteBody) -> dict[str, Any]:
        result: dict[str, Any] = await case_step(
            deps, lambda c: c.note(cas, me.subject, body.text, deps.clock())
        )
        return result

    @app.post("/staff/cases/{cas}/decide")
    async def decide_case(
        deps: D, me: CaseWorker, cas: CasId, body: CaseDecisionBody
    ) -> dict[str, Any]:
        """cleared or warning closes the case; actioned proposes actions that
        a second employee must confirm. Proposing a void or a profit removal
        is a pass: T2 or above."""
        actions = [a.model_dump(exclude_none=True) for a in body.actions]
        if needs_authorisation(actions):
            await _need(deps, me, "case.pass")
        result: dict[str, Any] = await case_step(
            deps,
            lambda c: c.decide(cas, me.subject, body.outcome, actions, body.reason, deps.clock()),
        )
        return result

    @app.post("/staff/cases/{cas}/confirm")
    async def confirm_case(deps: D, me: Me, cas: CasId, body: DecisionBody) -> dict[str, Any]:
        """The second employee confirms the proposed actions; they are carried
        out. A void or profit removal needs an admin (authorise); a customer
        block alone a T2 or above (confirm)."""
        try:
            proposed = await _cases(deps).proposed(cas)
        except CaseRefused as exc:
            raise _case_refused(exc) from exc
        step = "authorise" if needs_authorisation(proposed) else "confirm"
        access = await _need(deps, me, f"case.{step}")
        tier = None if access.tier is None else int(access.tier)
        result: dict[str, Any] = await case_step(
            deps, lambda c: c.confirm(cas, me.subject, body.reason, deps.clock(), step, tier)
        )
        return result

    @app.post("/staff/cases/{cas}/reject")
    async def reject_case(
        deps: D, me: CaseWorker, cas: CasId, body: DecisionBody
    ) -> dict[str, Any]:
        """The second employee sends the proposed actions back."""
        result: dict[str, Any] = await case_step(
            deps, lambda c: c.reject(cas, me.subject, body.reason, deps.clock())
        )
        return result

    @app.post("/staff/cases/{cas}/reopen")
    async def reopen_case(
        deps: D, me: CaseWorker, cas: CasId, body: DecisionBody
    ) -> dict[str, Any]:
        """Reopen a closed case on new evidence (an appeal)."""
        result: dict[str, Any] = await case_step(
            deps, lambda c: c.reopen(cas, me.subject, body.reason, deps.clock())
        )
        return result

    # --- admin: settings --------------------------------------------------------------

    # --- employees: payouts --------------------------------------------------------

    @app.get("/staff/payouts")
    async def list_payouts(
        deps: D,
        me: Viewer,
        status: PAYOUT_STATUS | None = None,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
    ) -> list[dict[str, Any]]:
        found = await _payouts(deps).store.payouts(status=status, limit=limit)
        return [payout_view(p) for p in found]

    @app.get("/staff/payouts/{po}")
    async def get_payout(deps: D, me: Viewer, po: PoId) -> dict[str, Any]:
        store = _payouts(deps).store
        record = await store.payout(po)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown payout")
        wallet = await store.wallet_by_id(record.wal)
        assert wallet is not None
        history = [
            {"kind": e.kind, "ts": e.ts, "detail": e.detail} for e in await store.history(po)
        ]
        try:
            measure = await _payouts(deps).short_hold(record.acc)
        except Exception:  # the history projection is not readable
            measure = None
        found = None if deps.classes is None else await deps.classes.account_class(record.acc)
        return {
            **payout_view(record),
            "wallet": wallet_view(wallet),
            "history": history,
            "short_hold": None if measure is None else measure.view(),
            "risk_class": None
            if found is None
            else {"class": found[0], "reason": found[1], "since": found[2]},
        }

    async def payout_step(step: Callable[[], Awaitable[PayoutRecord]]) -> dict[str, Any]:
        try:
            return payout_view(await step())
        except PayoutRefused as exc:
            raise _payout_refused(exc) from exc

    @app.post("/staff/payouts/{po}/approve")
    async def approve_payout(deps: D, me: Me, po: PoId, body: DecisionBody) -> dict[str, Any]:
        """The step due [decided 31 Oct 2026]: a pass (T2 or above, payouts
        role), then an authorisation (an admin, another person). Payouts
        requested under the count rule take any employee who may pass."""
        payouts = _payouts(deps)
        try:
            step = await payouts.next_step(po)
        except PayoutRefused as exc:
            raise _payout_refused(exc) from exc
        access = await _need(deps, me, f"payout.{step or 'pass'}")
        tier = None if access.tier is None else int(access.tier)
        return await payout_step(
            lambda: payouts.approve(po, me.subject, body.reason, deps.clock(), step, tier)
        )

    @app.post("/staff/payouts/{po}/deny")
    async def deny_payout(
        deps: D, me: PayoutDenier, po: PoId, body: DecisionBody
    ) -> dict[str, Any]:
        payouts = _payouts(deps)
        return await payout_step(lambda: payouts.deny(po, me.subject, body.reason, deps.clock()))

    @app.get("/admin/general")
    async def admin_general(deps: D, me: SettingsReader) -> list[dict[str, Any]]:
        """The firm-wide basics (rule AC05, General), read only: each with
        what it means and why it is fixed in the deployment rather than a
        console setting."""
        policy = await _policy(deps)
        now = deps.clock()
        reset = NEW_YORK_17.next_boundary(now)
        return [
            {
                "name": "Base currency",
                "value": "US dollar (USD)",
                "explains": "Prices, balances, fees and payouts are kept in US cents.",
                "why_fixed": "Every amount in the records is in US cents; another currency "
                "would need a conversion of every record, not a switch.",
            },
            {
                "name": "Trading day",
                "value": "17:00 to 17:00 New York time (America/New_York)",
                "explains": "Daily loss limits and trading days are counted from this "
                f"reset; the next is at {utc_text(reset)}.",
                "why_fixed": "The forex market's day close. Moving it would move every "
                "running account's daily limit in the middle of a day.",
            },
            {
                "name": "Time in records",
                "value": "UTC, to the millisecond",
                "explains": "Every record carries its time in UTC; screens may show local time.",
                "why_fixed": "Records must replay the same way on any machine.",
            },
            {
                "name": "Trading configuration version",
                "value": policy.trading.config_version,
                "explains": "The version of the instrument and trading settings in force.",
                "why_fixed": "Set by the policy file; product changes go through Setting "
                "proposals.",
            },
            {
                "name": "Engine version",
                "value": _engine_version(),
                "explains": "The version of the engine serving this console.",
                "why_fixed": "Changes with each release.",
            },
        ]

    @app.get("/admin/security")
    async def admin_security(deps: D, me: SettingsReader) -> list[dict[str, Any]]:
        """The security switches (rule AC16), each with what it does; all are
        locked in the policy file and cannot be changed from the console."""
        policy = await _policy(deps)
        return [
            {
                "name": s.name,
                "value": switch_value(policy, s.name),
                "locked": True,
                "explains": s.explains,
            }
            for s in LOCKED_SWITCHES
        ]

    @app.get("/admin/access")
    async def admin_access(deps: D, me: SettingsReader) -> dict[str, Any]:
        """The country-gating switch and list in force, and the minimum age."""
        gate = (await _policy(deps)).access
        return {
            "country_gating": gate.country_gating,
            "blocked_countries": sorted(gate.blocked_countries),
            "min_age": gate.min_age,
        }

    # --- maker-checker on admin settings (5a-2) ----------------------------------------

    async def expiry_ms(deps: Deps) -> int:
        return (await _staff_policy(deps)).proposal_expiry_hours * 3_600_000

    async def propose_setting(
        deps: Deps, me: Principal, setting: Setting, reason: str
    ) -> dict[str, Any]:
        """The change waits for a second person (rule AC00); they are
        notified. Owner-level changes (AC01) wait for the other owner."""
        now = deps.clock()
        tier = (await _access(deps, me)).tier
        try:
            proposal = await _settings(deps).propose(
                me.subject, me.email, setting, reason, now, tier=tier
            )
        except SettingsError as exc:
            raise _settings_refused(exc) from exc
        if deps.desk is not None:
            kind, _ = to_json(setting)
            who = (
                "the other owner"
                if owner_level(setting)
                else "an owner"
                if isinstance(setting, OWNER_APPROVES)
                else "a second admin"
            )
            await deps.desk.notify(
                "settings.change",
                "settings_proposal",
                f"setting-proposal:{proposal.id}",
                f"A {kind.replace('_', ' ')} change waits for {who}",
                f"Proposal {proposal.id} by {me.email or me.subject}: {reason}",
                now,
            )
        return {
            "status": "proposed",
            "proposal": proposal_view(proposal, now, await expiry_ms(deps)),
        }

    async def close_proposal(deps: Deps, pid: int, outcome: str) -> None:
        if deps.desk is not None:
            await deps.desk.close(f"setting-proposal:{pid}", outcome, deps.clock())

    @app.get("/admin/settings/proposals")
    async def setting_proposals(
        deps: D, me: SettingsReader, open_only: Annotated[bool, Query(alias="open")] = True
    ) -> list[dict[str, Any]]:
        now, expiry = deps.clock(), await expiry_ms(deps)
        return [proposal_view(p, now, expiry) for p in await _settings(deps).proposals(open_only)]

    @app.post("/admin/settings/proposals/{pid}/approve")
    async def approve_setting(
        deps: D, me: Me, pid: Annotated[int, Path(ge=1)], body: DecisionBody
    ) -> JSONResponse:
        """The second person, never the maker (rule AC00): an admin or owner
        for country gating; the other owner, or the owners' stand-in, for a
        product change; the other owner for a stand-in appointment. The
        change takes effect: 200 when in force, 202 while the engine has
        not answered."""
        access = await _need(deps, me, "settings.change")
        try:
            status, rule_set = await _settings(deps).approve(
                pid, me.subject, body.reason, deps.clock(), await expiry_ms(deps), tier=access.tier
            )
        except SettingsError as exc:
            raise _settings_refused(exc) from exc
        await close_proposal(deps, pid, "approved")
        applied = status.applied_at is not None
        out = {"change": change_view(status), "status": "applied" if applied else "pending"}
        if rule_set is not None:
            out["rsv_id"] = rule_set.rsv_id
        return JSONResponse(out, status_code=200 if applied else 202)

    @app.post("/admin/settings/proposals/{pid}/reject")
    async def reject_setting(
        deps: D, me: SettingsChanger, pid: Annotated[int, Path(ge=1)], body: DecisionBody
    ) -> dict[str, Any]:
        tier = (await _access(deps, me)).tier
        try:
            found = await _settings(deps).reject(
                pid, me.subject, body.reason, deps.clock(), tier=tier
            )
        except SettingsError as exc:
            raise _settings_refused(exc) from exc
        await close_proposal(deps, pid, "rejected")
        return proposal_view(found, deps.clock(), await expiry_ms(deps))

    @app.post("/admin/settings/proposals/{pid}/withdraw")
    async def withdraw_setting(
        deps: D, me: SettingsChanger, pid: Annotated[int, Path(ge=1)], body: DecisionBody
    ) -> dict[str, Any]:
        try:
            found = await _settings(deps).withdraw(pid, me.subject, body.reason, deps.clock())
        except SettingsError as exc:
            raise _settings_refused(exc) from exc
        await close_proposal(deps, pid, "withdrawn")
        return proposal_view(found, deps.clock(), await expiry_ms(deps))

    @app.put("/admin/access/country-gating")
    async def set_country_gating(
        deps: D, me: SettingsChanger, body: CountryGatingBody
    ) -> dict[str, Any]:
        settings = _settings(deps)
        try:
            countries = parse_countries(body.blocked_countries, "blocked_countries")
        except PolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        setting = CountryGating(body.country_gating, countries)
        if (await _staff_policy(deps)).settings_need_second_admin:
            return await propose_setting(deps, me, setting, body.reason)
        status = await settings.set_country_gating(
            me.subject, me.email, setting, body.reason, deps.clock()
        )
        return change_view(status)

    @app.put("/admin/products/{product_id}/stages/{stage}/limits")
    async def set_stage_limits(
        deps: D,
        me: SettingsChanger,
        product_id: Annotated[str, Path(pattern=PRODUCT_PATTERN)],
        stage: Literal["s1", "s2", "funded"],
        body: StageLimitsBody,
    ) -> JSONResponse:
        """New limits for one stage: a new rule-set version for new accounts.
        200 when the engine has it (in force), 202 while it waits."""
        settings = _settings(deps)
        try:
            setting = stage_limits(
                product_id,
                stage,
                body.profit_target_percent,
                body.daily_loss_percent,
                body.max_loss_percent,
                body.max_loss_kind,
                body.floor_restarts_after_payout,
                body.floor_locks_at_start,
            )
            if (await _staff_policy(deps)).settings_need_second_admin:
                return JSONResponse(
                    await propose_setting(deps, me, setting, body.reason), status_code=202
                )
            if (await _access(deps, me)).tier != Tier.OWNER:
                raise SettingsError("owners_only", "an owner changes a product (rule AC01)")
            status, rule_set = await settings.set_stage_limits(
                me.subject, me.email, setting, body.reason, deps.clock()
            )
        except PolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SettingsError as exc:
            raise _settings_refused(exc) from exc
        applied = status.applied_at is not None
        return JSONResponse(
            {
                "change": change_view(status),
                "rsv_id": rule_set.rsv_id,
                "status": "applied" if applied else "pending",
            },
            status_code=200 if applied else 202,
        )

    # --- the owners' stand-in approver (rule AC00, decided 8 Nov 2026) -----------------

    # --- payment and withdrawal methods (rules AC02, AC13, AC14) ------------------------

    async def check_method(deps: Deps, body: PurchaseBody) -> None:
        """Rule AC13: once payment methods are attached, a purchase names an
        enabled one, its provider is this checkout's, and the list price is
        within its limits. With none attached the checkout's provider is used."""
        if deps.settings is None:
            return
        methods = {
            m.method_id: m
            for m in (await deps.settings.methods()).values()
            if m.kind == "payment" and m.enabled
        }
        if not methods:
            return
        method = methods.get(body.method or "")
        wrong = (
            deps.checkout is not None
            and method is not None
            and (method.adapter != deps.checkout.adapter)
        )
        if method is None or wrong:
            raise HTTPException(
                status_code=422,
                detail={"code": "method", "message": f"choose one of {sorted(methods)}"},
            )
        try:
            prices = dict((await _policy(deps)).product(body.product_id).prices_cents)
        except KeyError:
            return  # the checkout refuses an unknown product
        price = prices.get(body.size_cents)
        if price is not None and not method.allows(price):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "method_limits",
                    "message": f"{method.name} does not take this amount",
                },
            )

    @app.get("/payment-methods")
    async def payment_methods(
        deps: D, kind: Literal["payment", "withdrawal"] = "payment"
    ) -> list[dict[str, Any]]:
        """The enabled methods of one kind, with limits and charges (public)."""
        return [
            {k: v for k, v in method_json(m).items() if k != "adapter"}
            for m in sorted((await _settings(deps).methods()).values(), key=lambda m: m.method_id)
            if m.kind == kind and m.enabled
        ]

    @app.get("/admin/payment-methods")
    async def admin_payment_methods(deps: D, me: SettingsReader) -> dict[str, Any]:
        """Every method, and which secrets each has set (never their values)."""
        settings = _settings(deps)
        secrets = await settings.secrets()
        return {
            "adapters": list(ADAPTERS),
            "methods": [
                {
                    **method_json(m),
                    "secrets": sorted(name for (mid, name) in secrets if mid == m.method_id),
                }
                for m in sorted((await settings.methods()).values(), key=lambda m: m.method_id)
            ],
        }

    @app.put("/admin/payment-methods/{method_id}")
    async def propose_method(
        deps: D,
        me: SettingsChanger,
        method_id: Annotated[str, Path(pattern=METHOD_ID.pattern)],
        body: MethodBody,
    ) -> dict[str, Any]:
        """Attach or change a method (enable or disable it too): an admin or
        owner proposes, an owner who is not the proposer approves (AC02)."""
        try:
            method = method_from_json(
                {**body.model_dump(exclude={"reason"}), "method_id": method_id}
            )
        except (PolicyError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await propose_setting(deps, me, method, body.reason)

    @app.put("/admin/payment-methods/{method_id}/secrets/{name}")
    async def propose_secret(
        deps: D,
        me: SettingsChanger,
        method_id: Annotated[str, Path(pattern=METHOD_ID.pattern)],
        name: Annotated[str, Path(pattern=SECRET_NAME.pattern)],
        body: SecretBody,
    ) -> dict[str, Any]:
        """Set or replace a secret (AC14): encrypted at once, never shown
        again; it takes effect when an owner approves."""
        if method_id not in await _settings(deps).methods():
            raise HTTPException(status_code=404, detail="unknown method")
        try:
            sealed = deps.vault.seal(method_id, name, body.value, os.urandom(12))
        except SecretsUnavailable as exc:
            raise HTTPException(
                status_code=503, detail={"code": "no_secrets_key", "message": str(exc)}
            ) from exc
        out = await propose_setting(deps, me, sealed, body.reason)
        return out

    # --- the website's text (rules AC07, AC08) ------------------------------------------

    @app.get("/site")
    async def site(deps: D) -> dict[str, Any]:
        """The home page text approved for each section (sections not edited
        are absent: the site keeps its own wording), with every product figure
        filled from the products and policy in force (public)."""
        values = token_values(await _policy(deps))
        sections = await _settings(deps).site()
        return {name: rendered(s, values) for name, s in sorted(sections.items())}

    @app.get("/admin/notices")
    async def admin_notices(deps: D, me: SettingsReader) -> dict[str, Any]:
        """The notification templates (rule AC12): each event's variables and
        built-in wording, the wording in force, and the email wrapper."""
        current = Notices() if deps.settings is None else await deps.settings.notices()
        return {
            "channels": list(CHANNELS),
            "events": {
                name: {
                    "label": e.label,
                    "variables": dict(e.variables),
                    "required": sorted(e.required),
                    "default": {"subject": e.subject, "body": e.body},
                    "sample": dict(e.sample),
                }
                for name, e in EVENTS.items()
            },
            "templates": {
                name: {
                    "subject": current.template(name).subject,
                    "body": current.template(name).body,
                    "approved": name in current.templates,
                }
                for name in EVENTS
            },
            "wrapper": {"email": current.wrapper},
            "default_wrapper": DEFAULT_WRAPPER,
            "limits": {"subject": SUBJECT_MAX, "body": BODY_MAX, "wrapper": WRAPPER_MAX},
        }

    @app.put("/admin/notices/{event}")
    async def propose_notice(
        deps: D,
        me: SettingsChanger,
        event: Annotated[str, Path(pattern=r"^[a-z_]{1,40}$")],
        body: NoticeTemplateBody,
    ) -> dict[str, Any]:
        """New wording for one event's email: a proposal a second admin or
        owner approves (rules AC00, AC12)."""
        try:
            setting = NoticeTemplate(event, body.subject, body.body)
        except PolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await propose_setting(deps, me, setting, body.reason)

    @app.put("/admin/notice-wrapper/{channel}")
    async def propose_wrapper(
        deps: D,
        me: SettingsChanger,
        channel: Annotated[str, Path(pattern=r"^[a-z]{1,20}$")],
        body: NoticeWrapperBody,
    ) -> dict[str, Any]:
        """New text around every email body ({body} once), by proposal."""
        try:
            setting = NoticeWrapper(channel, body.text)
        except PolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await propose_setting(deps, me, setting, body.reason)

    @app.get("/admin/offering")
    async def admin_offering(deps: D, me: SettingsReader) -> dict[str, Any]:
        """Products and account sizes, each on sale or switched off, with the
        allocation cap and the one-Instant rule (review D4)."""
        policy = await _policy(deps)
        c = policy.catalogue
        return {
            "products": [
                {
                    "id": p.id,
                    "name": p.name,
                    "on": p.id not in c.products_off,
                    "instant": instant_funding(p),
                    "sizes": [
                        {"size_cents": s, "price_cents": price, "on": s not in c.sizes_off}
                        for s, price in p.prices_cents
                    ],
                }
                for p in policy.products
            ],
            "sizes": [{"size_cents": s, "on": s not in c.sizes_off} for s in all_sizes(policy)],
            "allocation_cap_cents": policy.scaling.max_allocation_cents,
            "one_instant_funding_per_trader": c.one_instant_funding_per_trader,
            "unpaid_hold_hours": c.unpaid_hold_hours,
            "addons": [
                {
                    "id": o.id,
                    "price_percent": percent_text(o.price),
                    "products": sorted(o.products),
                    "on": o.id not in policy.addons.off,
                }
                for o in policy.addons.offers
            ],
        }

    @app.put("/admin/offering")
    async def propose_offering(deps: D, me: SettingsChanger, body: OfferingBody) -> dict[str, Any]:
        """Switch products or account sizes off (or on again): a proposal a
        second admin or owner approves (rule AC00). Stops new sales only."""
        policy = await _policy(deps)
        addons_off = policy.addons.off if body.addons_off is None else frozenset(body.addons_off)
        setting = Offering(frozenset(body.products_off), frozenset(body.sizes_off), addons_off)
        try:
            check_catalogue(policy, setting.products_off, setting.sizes_off)
            if unknown := sorted(addons_off - {o.id for o in policy.addons.offers}):
                raise PolicyError(f"unknown add-ons {unknown}")
        except PolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await propose_setting(deps, me, setting, body.reason)

    @app.get("/site/status")
    async def site_status(deps: D) -> dict[str, Any]:
        """Whether the site is under maintenance and registration is open
        (rule AC17, public); the pages' text is in `/site`."""
        mode = await _mode(deps)
        return {**mode_json(mode), "registering": mode.registering, "selling": mode.selling}

    @app.put("/admin/site-mode")
    async def propose_site_mode(deps: D, me: SettingsChanger, body: SiteModeBody) -> dict[str, Any]:
        """Turn maintenance on or off, or open or close registration: a
        proposal a second admin or owner approves (rules AC00, AC16, AC17)."""
        setting = SiteMode(body.maintenance, body.registration_open)
        return await propose_setting(deps, me, setting, body.reason)

    @app.get("/admin/site")
    async def admin_site(deps: D, me: SettingsReader) -> dict[str, Any]:
        """For the editor: each section's fields and items, the approved text
        (with tokens, not filled), and each token's value now."""
        sections = await _settings(deps).site()
        return {
            "shapes": {
                name: {
                    "fields": dict(shape.fields),
                    "items": dict(shape.items),
                    "max_items": shape.max_items,
                    "optional": sorted(shape.optional),
                    "images": sorted(shape.images),
                    "figures": sorted(shape.figures),
                    "private": sorted(shape.private),
                    "item_optional": sorted(shape.item_optional),
                }
                for name, shape in SECTIONS.items()
            },
            "sections": {name: section_json(s) for name, s in sorted(sections.items())},
            "tokens": token_values(await _policy(deps)),
            "mode": mode_json(await _mode(deps)),
        }

    @app.put("/admin/site/{section}")
    async def propose_site(
        deps: D,
        me: SettingsChanger,
        section: Annotated[str, Path(pattern=r"^[a-z_]{1,20}$")],
        body: SiteSectionBody,
    ) -> dict[str, Any]:
        """New text for one section: a proposal a second admin or owner
        approves (rule AC00). Figures are tokens, never typed (AC08)."""
        try:
            setting = SiteSection(section, body.fields, tuple(body.items))
        except PolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await propose_setting(deps, me, setting, body.reason)

    # --- policy pages (rule AC10) ------------------------------------------------------

    # --- the public site for search engines and link previews (D10, AC11) ----------

    def _base_url(deps: Deps, request: Request) -> str:
        """The portal's address: configured, else as the web server forwarded it."""
        if deps.public_url:
            return deps.public_url.rstrip("/")
        host = request.headers.get("x-forwarded-host") or "localhost:5173"
        scheme = request.headers.get("x-forwarded-proto") or "http"
        if not re.fullmatch(r"[A-Za-z0-9.-]+(:[0-9]{1,5})?", host) or scheme not in (
            "http",
            "https",
        ):
            return "http://localhost:5173"
        return f"{scheme}://{host}"

    async def _seo_inputs(deps: Deps) -> tuple[dict[str, Any], list[Any]]:
        values = token_values(await _policy(deps))
        sections = await _settings(deps).site() if deps.settings is not None else {}
        site = {name: rendered(s, values) for name, s in sections.items()}
        pages = await _settings(deps).latest_pages() if deps.settings is not None else []
        return site, pages

    SEO_PATH = Annotated[str, Query(max_length=2000)]  # any path: unknown ones are private

    @app.get("/seo/head")
    async def seo_head(deps: D, request: Request, path: SEO_PATH) -> Response:
        """The page's title, description, robots, canonical link and social
        card, for the web server to put in its <head> (public)."""
        site, pages = await _seo_inputs(deps)
        slugs = [p.slug for p in pages]
        m = meta(path, site.get("seo"), slugs, {p.slug: p.title for p in pages})
        return Response(head_html(path, m, _base_url(deps, request)), media_type="text/html")

    @app.get("/seo/body")
    async def seo_body(deps: D, path: SEO_PATH) -> Response:
        """The page's text before any script runs (public pages only)."""
        policy = await _policy(deps)
        site, pages = await _seo_inputs(deps)
        products = []
        for p in policy.products:
            if not policy.catalogue.on_sale(p):
                continue
            first = p.rule_sets[0]
            target = (
                "no profit target"
                if first.profit_target is None
                else f"{percent_text(first.profit_target)}% profit target"
            )
            kind = KIND_WORDS[first.max_loss_kind]
            rules = (
                f"{target}, {percent_text(first.daily_loss)}% daily loss, "
                f"{percent_text(first.max_loss)}% {kind}maximum loss"
            )
            sizes = tuple((dollars(s), dollars(c)) for s, c in policy.catalogue.sizes(p))
            products.append(ProductText(p.name, percent_text(p.profit_split) or "", sizes, rules))
        split = max(
            (p.profit_split for p in policy.products if policy.catalogue.on_sale(p)), default=None
        )
        page = None
        if path.startswith("/legal/"):
            found = next((p for p in pages if p.slug == path[len("/legal/") :]), None)
            page = (found.title, found.body) if found else None
        text = body_html(
            path,
            site,
            products,
            percent_text(split) or "" if split is not None else "",
            page,
            [p.slug for p in pages],
        )
        return Response(text, media_type="text/html")

    @app.get("/sitemap.xml")
    async def sitemap(deps: D, request: Request) -> Response:
        """The public pages, for search engines (an editor's noindex left out)."""
        site, pages = await _seo_inputs(deps)
        seo = site.get("seo") or {}
        hidden = [i.get("path", "") for i in seo.get("items", []) if i.get("robots") == "noindex"]
        xml = sitemap_xml(_base_url(deps, request), [p.slug for p in pages], hidden)
        return Response(xml, media_type="application/xml")

    @app.get("/robots.txt")
    async def robots(deps: D, request: Request) -> Response:
        return Response(robots_txt(_base_url(deps, request)), media_type="text/plain")

    @app.get("/policies")
    async def policies(deps: D) -> list[dict[str, Any]]:
        """The policy pages in force (public): their latest versions."""
        return [page_view(p, body=False) for p in await _settings(deps).latest_pages()]

    @app.get("/policies/{slug}")
    async def policy_page(
        deps: D,
        slug: Annotated[str, Path(pattern=PAGE_SLUG.pattern)],
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> dict[str, Any]:
        """One page: the version in force, or an earlier one (public)."""
        versions = (await _settings(deps).pages()).get(slug, [])
        if version is not None:
            versions = [v for v in versions if v.version == version]
        if not versions:
            raise HTTPException(status_code=404, detail="unknown policy page")
        return page_view(versions[-1], body=True)

    @app.put("/admin/policies/{slug}")
    async def propose_policy(
        deps: D,
        me: SettingsChanger,
        slug: Annotated[str, Path(pattern=PAGE_SLUG.pattern)],
        body: PolicyPageBody,
    ) -> dict[str, Any]:
        """A new version of a page: one owner proposes, the other approves;
        traders then accept it again before their next purchase."""
        try:
            page = PolicyPage(slug, body.title, body.body)
        except PolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await propose_setting(deps, me, page, body.reason)

    @app.get("/me/policies/pending")
    async def pending_policies(deps: D, me: Trader) -> list[dict[str, Any]]:
        """The pages in force this login has not accepted yet."""
        return [page_view(p, body=False) for p in await _settings(deps).pending_pages(me.subject)]

    @app.post("/me/policies/{slug}/accept")
    async def accept_policy(
        deps: D,
        me: Trader,
        slug: Annotated[str, Path(pattern=PAGE_SLUG.pattern)],
        body: AcceptPolicyBody,
    ) -> dict[str, Any]:
        try:
            page = await _settings(deps).accept_page(me.subject, slug, body.version, deps.clock())
        except SettingsError as exc:
            raise _settings_refused(exc) from exc
        return {**page_view(page, body=False), "accepted": True}

    @app.get("/admin/owners/stand-in")
    async def get_stand_in(deps: D, me: SettingsReader) -> dict[str, Any]:
        """The stand-in in force now (null: none) and the longest appointment."""
        found = await _settings(deps).stand_in(deps.clock())
        return {
            "stand_in": None if found is None else {"subject": found.subject, "until": found.until},
            "max_days": (await _staff_policy(deps)).stand_in_max_days,
        }

    @app.put("/admin/owners/stand-in")
    async def propose_stand_in(deps: D, me: SettingsChanger, body: StandInBody) -> dict[str, Any]:
        """One owner proposes appointing (or, with a null subject, ending) the
        stand-in; the other owner approves it. The stand-in must be an admin
        (T4), for at most `stand_in_max_days`."""
        now = deps.clock()
        setting = StandIn(body.subject, body.until if body.subject is not None else 0)
        target = None
        if body.subject is not None:
            target = (await _access(deps, Principal(body.subject, None, frozenset()))).tier
        try:
            check_appointment(setting, now, (await _staff_policy(deps)).stand_in_max_days, target)
        except Refused as exc:
            raise _settings_refused(SettingsError(exc.code, str(exc))) from exc
        return await propose_setting(deps, me, setting, body.reason)

    # --- the risk desk: restricted news events (M13) ----------------------------------

    def news_desk(deps: Deps) -> NewsDesk:
        if deps.news is None:
            raise HTTPException(status_code=503, detail="the news calendar is not available")
        return deps.news

    def news_refused(exc: NewsRefused) -> HTTPException:
        status = 404 if exc.code == "unknown_calendar" else 422
        return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})

    @app.get("/staff/news")
    async def staff_news(deps: D, me: Viewer) -> dict[str, Any]:
        """The calendar files (which events are in the engine now), the
        events with no fixed time, and the desk's latest actions."""
        desk = news_desk(deps)
        live = {e["event_id"] for e in await deps.live.news()}
        return {
            "calendars": [calendar_view(c, live) for c in desk.calendars],
            "impacts": await desk.impacts(),  # marked ones; any other is high
            "actions": [action_view(a) for a in await desk.history(100)],
            # sources the collector cannot read: add their events by hand
            "source_alerts": [
                alert_view(a)
                for a in (await deps.source_alerts.open_alerts() if deps.source_alerts else [])
            ],
        }

    @app.post("/staff/news/events/{event_id}/impact")
    async def mark_impact(
        deps: D,
        me: NewsDesker,
        event_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")],
        body: ImpactBody,
    ) -> dict[str, Any]:
        """The risk desk marks a release high, medium or low impact for the
        calendar. One person, as adding a news window (decided 8 Nov 2026);
        the restriction does not change."""
        who = Author(me.subject, me.email, body.reason)
        try:
            await news_desk(deps).mark_impact(event_id, body.impact, who, deps.clock())
        except NewsRefused as exc:
            raise news_refused(exc) from exc
        return {"event_id": event_id, "impact": body.impact}

    @app.post("/staff/news/calendars/{month}/publish")
    async def publish_news(
        deps: D,
        me: NewsDesker,
        month: Annotated[str, Path(pattern=r"^\d{4}-\d{2}$")],
        body: NewsReasonBody,
    ) -> list[dict[str, Any]]:
        """Send the month's events whose windows have not started; repeats
        are refused by the engine (already scheduled) and change nothing."""
        try:
            done = await news_desk(deps).publish(
                month, Author(me.subject, me.email, body.reason), deps.clock()
            )
        except NewsRefused as exc:
            raise news_refused(exc) from exc
        return [action_view(a) for a in done]

    @app.post("/staff/news/events", status_code=201)
    async def manual_news(deps: D, me: NewsDesker, body: ManualNewsBody) -> dict[str, Any]:
        """A window for an event that is not in the calendar."""
        scope = EventScope(body.scope)
        try:
            done = await news_desk(deps).manual(
                body.event_id,
                body.title,
                scope,
                body.currency,
                body.ts,
                Author(me.subject, me.email, body.reason),
                deps.clock(),
            )
        except NewsRefused as exc:
            raise news_refused(exc) from exc
        return action_view(done)

    @app.post("/staff/news/published", status_code=201)
    async def news_published(deps: D, me: NewsDesker, body: PublishedNewsBody) -> dict[str, Any]:
        """An event with no fixed time was just published: no trades on its
        currency's symbols for the next 2 minutes."""
        try:
            done = await news_desk(deps).published(
                body.event_id,
                body.title,
                body.currency,
                Author(me.subject, me.email, body.reason),
                deps.clock(),
            )
        except NewsRefused as exc:
            raise news_refused(exc) from exc
        return action_view(done)

    @app.post("/admin/news/events/{event_id}/cancel")
    async def cancel_news(
        deps: D,
        me: NewsCanceller,
        event_id: Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")],
        body: NewsReasonBody,
    ) -> dict[str, Any]:
        """Remove a window that has not started (the engine refuses later)."""
        done = await news_desk(deps).cancel(
            event_id, Author(me.subject, me.email, body.reason), deps.clock()
        )
        return action_view(done)

    @app.get("/admin/settings/changes")
    async def setting_changes(
        deps: D, me: SettingsReader, limit: Annotated[int, Query(ge=1, le=1_000)] = 100
    ) -> list[dict[str, Any]]:
        """Every admin change, latest first, with who, why and when it took effect."""
        return [change_view(s) for s in await _settings(deps).history(limit)]

    # --- the staff directory (back office 5a) -----------------------------------------

    def directory(deps: Deps) -> Directory:
        if deps.staff is None:
            raise HTTPException(status_code=503, detail="the staff directory is not available")
        return deps.staff

    async def staff_step(step: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await step()
        except StaffRefused as exc:
            raise _staff_refused(exc) from exc

    @app.get("/staff/me")
    async def staff_me(deps: D, me: Employee) -> dict[str, Any]:
        """My staff page: who I am, my access and every grant, my to-do
        list, duties and open notifications, and what waits for my approval."""
        now = deps.clock()
        access = await _access(deps, me)
        out: dict[str, Any] = {"access": access_view(access), "member": None, "grants": []}
        out["todo"] = await build_todo(deps, me, access)
        out["training"] = await training_view(deps, access, deps.clock())
        out["duties"] = await deps.desk.duties_view(access) if deps.desk is not None else []
        out["notifications"] = (
            [notification_view(n) for n in await deps.desk.notifications(access, limit=50)]
            if deps.desk is not None
            else []
        )
        if deps.staff is None:
            return {**out, "awaiting_approval": [], "graduations": []}
        actor = _actor(me)
        async with deps.staff.store.read() as ops:
            member = await ops.member_by_subject(me.subject)
            if member is not None:
                out["member"] = member_view(member, now)
                out["grants"] = [grant_view(g, now) for g in await ops.grants_of(member.sid)]
        out["awaiting_approval"] = [
            grant_view(g, now) for g in await deps.staff.awaiting(actor, now)
        ]
        out["graduations"] = [member_view(m, now) for m in await deps.staff.graduations(actor, now)]
        return out

    @app.get("/staff/members")
    async def staff_members(
        deps: D,
        me: DirectoryReader,
        status: Literal["active", "left", "graduated"] | None = None,
    ) -> list[dict[str, Any]]:
        now = deps.clock()
        async with directory(deps).store.read() as ops:
            return [member_view(m, now) for m in await ops.members(status)]

    @app.get("/staff/members/{sid}")
    async def staff_member(
        deps: D, me: Employee, sid: Annotated[str, Path(pattern=r"^(STF|APP)[0-9]{8}$")]
    ) -> dict[str, Any]:
        """One member with grants and history: the directory, or oneself."""
        access = await _access(deps, me)
        if access.sid != sid and not access.has("staff.directory"):
            raise _forbidden("staff.directory")
        now = deps.clock()
        async with directory(deps).store.read() as ops:
            member = await ops.member(sid)
            if member is None:
                raise HTTPException(status_code=404, detail="unknown member")
            grants = await ops.grants_of(sid)
            history = await ops.member_history(sid)
        return {
            **member_view(member, now),
            "grants": [grant_view(g, now) for g in grants],
            "history": [{"kind": e.kind, "ts": e.ts, "detail": dict(e.detail)} for e in history],
        }

    @app.post("/staff/members", status_code=201)
    async def add_member(deps: D, me: Employee, body: MemberBody) -> dict[str, Any]:
        """A joiner (T3 or above)."""
        now = deps.clock()
        staff = directory(deps)
        member, grants = await staff_step(
            lambda: staff.join(
                _actor(me),
                subject=body.subject,
                name=body.name,
                email=body.email,
                kind=body.kind,
                team=body.team,
                lead=body.lead,
                mentor=body.mentor,
                ends_at=body.ends_at,
                tier=body.tier,
                roles=body.roles,
                reason=body.reason,
                now=now,
                sections=dict(body.sections.items()),
            )
        )
        return {**member_view(member, now), "grants": [grant_view(g, now) for g in grants]}

    SidPath = Annotated[str, Path(pattern=r"^(STF|APP)[0-9]{8}$")]
    GrtPath = Annotated[str, Path(pattern=r"^GRT[0-9]{8}$")]

    @app.post("/staff/members/{sid}/move")
    async def move_member(deps: D, me: Employee, sid: SidPath, body: MoveBody) -> dict[str, Any]:
        now, staff = deps.clock(), directory(deps)
        member = await staff_step(
            lambda: staff.move(_actor(me), sid, body.team, body.lead, body.reason, now)
        )
        return member_view(member, now)

    @app.post("/staff/members/{sid}/leave")
    async def leave_member(
        deps: D, me: Employee, sid: SidPath, body: DecisionBody
    ) -> dict[str, Any]:
        """A leaver: access ends now, and every grant with it; the login is
        disabled at Keycloak and its sessions ended (`GET /staff/cutoffs`)."""
        now, staff = deps.clock(), directory(deps)
        member = await staff_step(lambda: staff.leave(_actor(me), sid, body.reason, now))
        if deps.cutoffs is not None:  # the login now, not at the next round
            try:
                await deps.cutoffs.advance(now)
            except Exception:  # the background round retries
                logging.getLogger(__name__).exception("leaver cut-off at once failed")
        return member_view(member, now)

    @app.post("/staff/members/{sid}/recommend")
    async def recommend_member(
        deps: D, me: Employee, sid: SidPath, body: DecisionBody
    ) -> dict[str, Any]:
        """The mentor recommends the apprentice for graduation."""
        now, staff = deps.clock(), directory(deps)
        member = await staff_step(lambda: staff.recommend(_actor(me), sid, body.reason, now))
        return member_view(member, now)

    @app.post("/staff/members/{sid}/graduate", status_code=201)
    async def graduate_member(
        deps: D, me: Employee, sid: SidPath, body: GraduateBody
    ) -> dict[str, Any]:
        """An admin approves a recommended apprentice: a new T1 staff member
        on its own login (an approval: step-up)."""
        await _step_up(deps, me)
        now, staff = deps.clock(), directory(deps)
        member = await staff_step(
            lambda: staff.graduate(_actor(me), sid, body.subject, body.email, body.reason, now)
        )
        return member_view(member, now)

    @app.get("/staff/grants")
    async def staff_grants(
        deps: D,
        me: DirectoryReader,
        status: Literal["requested", "approved", "denied", "cancelled", "revoked", "superseded"]
        | None = "requested",
    ) -> list[dict[str, Any]]:
        now = deps.clock()
        async with directory(deps).store.read() as ops:
            return [grant_view(g, now) for g in await ops.grants(status)]

    @app.post("/staff/grants", status_code=201)
    async def request_grant(deps: D, me: Employee, body: GrantBody) -> dict[str, Any]:
        """Ask for a tier, role, admin power or acting cover for someone else."""
        now, staff = deps.clock(), directory(deps)
        grant = await staff_step(
            lambda: staff.request(
                _actor(me),
                body.sid,
                body.grant,
                body.value,
                body.starts_at,
                body.ends_at,
                body.reason,
                now,
            )
        )
        return grant_view(grant, now)

    @app.post("/staff/grants/{grt}/approve")
    async def approve_grant(
        deps: D, me: Employee, grt: GrtPath, body: DecisionBody
    ) -> dict[str, Any]:
        await _step_up(deps, me)
        now, staff = deps.clock(), directory(deps)
        grant = await staff_step(lambda: staff.approve(_actor(me), grt, body.reason, now))
        return grant_view(grant, now)

    @app.post("/staff/grants/{grt}/deny")
    async def deny_grant(deps: D, me: Employee, grt: GrtPath, body: DecisionBody) -> dict[str, Any]:
        now, staff = deps.clock(), directory(deps)
        grant = await staff_step(lambda: staff.deny(_actor(me), grt, body.reason, now))
        return grant_view(grant, now)

    @app.post("/staff/grants/{grt}/cancel")
    async def cancel_grant(
        deps: D, me: Employee, grt: GrtPath, body: DecisionBody
    ) -> dict[str, Any]:
        now, staff = deps.clock(), directory(deps)
        grant = await staff_step(lambda: staff.cancel(_actor(me), grt, body.reason, now))
        return grant_view(grant, now)

    @app.post("/staff/grants/{grt}/revoke")
    async def revoke_grant(
        deps: D, me: Employee, grt: GrtPath, body: DecisionBody
    ) -> dict[str, Any]:
        now, staff = deps.clock(), directory(deps)
        grant = await staff_step(lambda: staff.revoke(_actor(me), grt, body.reason, now))
        return grant_view(grant, now)

    # --- the staff page (back office 5b) ----------------------------------------------

    def desk(deps: Deps) -> StaffDesk:
        if deps.desk is None:
            raise HTTPException(status_code=503, detail="the staff page is not available")
        return deps.desk

    def desk_refused(exc: DeskError) -> HTTPException:
        status = 404 if str(exc).startswith("unknown") else 422
        return HTTPException(status_code=status, detail={"code": "refused", "message": str(exc)})

    @app.get("/staff/me/todo")
    async def my_todo(deps: D, me: Employee) -> list[dict[str, Any]]:
        return await build_todo(deps, me, await _staff_access(deps, me))

    @app.post("/staff/me/duties/{sheet}/ack")
    async def acknowledge_duties(
        deps: D, me: Employee, sheet: Annotated[str, Path(pattern=r"^[a-z]{2,20}$")]
    ) -> dict[str, Any]:
        """I have read the version in force of one of my duty sheets."""
        access = await _staff_access(deps, me)
        try:
            version = await desk(deps).acknowledge(access, sheet, deps.clock())
        except DeskError as exc:
            raise desk_refused(exc) from exc
        return {"sheet": sheet, "version": version}

    @app.post("/staff/me/notes", status_code=201)
    async def add_note(deps: D, me: Employee, body: TodoNoteBody) -> dict[str, Any]:
        """A note of my own on my to-do list, optionally with a due time."""
        if deps.staff is None:
            raise HTTPException(status_code=404, detail="no staff directory on this server")
        now = deps.clock()
        if body.due_at is not None and body.due_at <= now:
            raise HTTPException(status_code=422, detail="due_at must be in the future")
        async with deps.staff.store.read() as ops:
            note_id = await ops.add_note(me.subject, body.text, body.due_at, now)
        return {"id": note_id, "text": body.text, "due_at": body.due_at, "ts": now}

    @app.post("/staff/me/notes/{note_id}/done")
    async def finish_note(
        deps: D, me: Employee, note_id: Annotated[int, Path(ge=1)]
    ) -> dict[str, Any]:
        """Tick off one of my notes (it stays on record)."""
        if deps.staff is None:
            raise HTTPException(status_code=404, detail="no staff directory on this server")
        async with deps.staff.store.read() as ops:
            if not await ops.finish_note(me.subject, note_id, deps.clock()):
                raise HTTPException(status_code=404, detail="no such open note of yours")
        return {"id": note_id, "done": True}

    @app.get("/staff/notifications")
    async def my_notifications(
        deps: D,
        me: Employee,
        include_done: Annotated[bool, Query(alias="all")] = False,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        """Notifications reaching me now (by my permissions, or to me):
        open and not acknowledged, or every one with `all`."""
        access = await _staff_access(deps, me)
        found = await desk(deps).notifications(access, include_done=include_done, limit=limit)
        return [notification_view(n) for n in found]

    @app.post("/staff/notifications/{notification}/ack")
    async def acknowledge_notification(
        deps: D, me: Employee, notification: Annotated[int, Path(ge=1)]
    ) -> dict[str, Any]:
        access = await _staff_access(deps, me)
        try:
            await desk(deps).acknowledge_notification(access, notification, deps.clock())
        except DeskError as exc:
            raise desk_refused(exc) from exc
        return {"id": notification, "acknowledged": True}

    RefPath = Annotated[str, Path(pattern=r"^(CAS|PO|ONB|PRG|ACC|CIF|GRT|STF|APP)[0-9]{8}$")]

    @app.get("/staff/threads")
    async def threads(deps: D, me: Viewer) -> list[dict[str, Any]]:
        """The records discussed most recently."""
        return await desk(deps).threads()

    @app.get("/staff/threads/{ref}")
    async def thread(deps: D, me: Viewer, ref: RefPath) -> list[dict[str, Any]]:
        return [message_view(m) for m in await desk(deps).thread(ref)]

    @app.post("/staff/threads/{ref}", status_code=201)
    async def post_message(deps: D, me: Staff, ref: RefPath, body: MessageBody) -> dict[str, Any]:
        """An internal message on one record (never sent outside)."""
        access = await _staff_access(deps, me)
        try:
            message = await desk(deps).post(access, ref, body.body, deps.clock())
        except DeskError as exc:
            raise desk_refused(exc) from exc
        return message_view(message)

    # --- leavers' logins (5d) ---------------------------------------------------------

    def cutoffs(deps: Deps) -> Cutoffs:
        if deps.cutoffs is None:
            raise HTTPException(status_code=503, detail="cut-offs are not available")
        return deps.cutoffs

    async def cutoff_limits(deps: Deps) -> tuple[int, int]:
        staff = await _staff_policy(deps)
        return staff.leaver_cutoff_alert_minutes * 60_000, staff.leaver_cutoff_max_hours * 3_600_000

    @app.get("/staff/cutoffs")
    async def staff_cutoffs(deps: D, me: DirectoryReader) -> list[dict[str, Any]]:
        """Every ended login: pending, late (past the alert time), overdue
        (past the hard limit) or done, and how."""
        alert, hard = await cutoff_limits(deps)
        now = deps.clock()
        return [cutoff_view(c, now, alert, hard) for c in await cutoffs(deps).all()]

    @app.post("/staff/cutoffs/{sid}/done")
    async def confirm_cutoff(
        deps: D,
        me: Employee,
        sid: Annotated[str, Path(pattern=r"^(STF|APP)[0-9]{8}$")],
        body: DecisionBody,
    ) -> dict[str, Any]:
        """Confirm a login disabled by hand at Keycloak (T3 or above)."""
        await _need(deps, me, "staff.manage")
        try:
            found = await cutoffs(deps).confirm(sid, me.subject, deps.clock())
        except IdentityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        alert, hard = await cutoff_limits(deps)
        return cutoff_view(found, deps.clock(), alert, hard)

    # --- apprentices and training (5c) ------------------------------------------------

    @app.post("/staff/members/{sid}/training", status_code=201)
    async def record_training(
        deps: D,
        me: Employee,
        sid: Annotated[str, Path(pattern=r"^(STF|APP)[0-9]{8}$")],
        body: TrainingBody,
    ) -> dict[str, Any]:
        """A pass of a section's course, recorded by an assessor (T2 or above
        in that section, or an admin; never the trainee)."""
        now, staff = deps.clock(), directory(deps)
        passed = await staff_step(
            lambda: staff.record_training(_actor(me), sid, body.section, body.note, now)
        )
        return {
            "sid": passed.sid,
            "section": passed.section,
            "course": passed.course,
            "version": passed.version,
            "passed_at": passed.passed_at,
            "assessor": passed.assessor,
        }

    # --- pre-employment screening [7 Nov 2026] --------------------------------------------

    def screening_view(sid: str, sc: Screening) -> dict[str, Any]:
        return {
            "sid": sid,
            "status": sc.status,
            "valid_until": sc.valid_until,
            "missing": list(sc.missing),
            "checks": [
                {
                    "id": c.id,
                    "title": c.title,
                    "text": c.text,
                    "result": None if c.id not in sc.round else sc.round[c.id].result,
                    "reference": None if c.id not in sc.round else sc.round[c.id].reference,
                    "recorded_by": None if c.id not in sc.round else sc.round[c.id].recorded_by,
                    "recorded_at": None if c.id not in sc.round else sc.round[c.id].ts,
                }
                for c in screening_checks()
            ],
            "last_decision": None
            if sc.last_decision is None
            else {
                "decision": sc.last_decision.decision,
                "note": sc.last_decision.note,
                "decided_by": sc.last_decision.decided_by,
                "ts": sc.last_decision.ts,
                "valid_until": sc.last_decision.valid_until,
            },
        }

    @app.get("/staff/members/{sid}/screening")
    async def get_screening(
        deps: D, me: Employee, sid: Annotated[str, Path(pattern=r"^(STF|APP)[0-9]{8}$")]
    ) -> dict[str, Any]:
        """Restricted: T3 and above (or the person)."""
        staff = directory(deps)
        sc = await staff_step(lambda: staff.screening(_actor(me), sid, deps.clock()))
        return screening_view(sid, sc)

    @app.post("/staff/members/{sid}/screening/checks", status_code=201)
    async def record_screening_check(
        deps: D,
        me: Employee,
        sid: Annotated[str, Path(pattern=r"^(STF|APP)[0-9]{8}$")],
        body: ScreeningCheckBody,
    ) -> dict[str, Any]:
        """One check, recorded by a lead (T3) or above; never the person."""
        staff, now = directory(deps), deps.clock()
        await staff_step(
            lambda: staff.record_check(
                _actor(me), sid, body.check, body.result, body.reference, now
            )
        )
        return screening_view(sid, await staff_step(lambda: staff.screening(_actor(me), sid, now)))

    @app.post("/staff/members/{sid}/screening/decide")
    async def decide_screening(
        deps: D,
        me: Employee,
        sid: Annotated[str, Path(pattern=r"^(STF|APP)[0-9]{8}$")],
        body: ScreeningDecisionBody,
    ) -> dict[str, Any]:
        """An admin (T4) approves (every check passed) or rejects the round;
        never a recorder of it, never the person. A fresh second factor."""
        await _step_up(deps, me)
        staff, now = directory(deps), deps.clock()
        await staff_step(
            lambda: staff.decide_screening(_actor(me), sid, body.decision, body.note, now)
        )
        return screening_view(sid, await staff_step(lambda: staff.screening(_actor(me), sid, now)))

    def apprentices(deps: Deps) -> ApprenticeDesk:
        if deps.apprentices is None:
            raise HTTPException(status_code=503, detail="apprentice proposals are not available")
        return deps.apprentices

    @app.post("/staff/apprentice/proposals", status_code=201)
    async def apprentice_propose(
        deps: D, me: Employee, body: ApprenticeProposalBody
    ) -> dict[str, Any]:
        """An apprentice proposes; their mentor's approval carries it out."""
        access = await _need(deps, me, "apprentice.propose")
        model = NoteBody if body.action == "case.note" else CaseDecisionBody
        try:
            checked = model.model_validate(body.body).model_dump(exclude_none=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"body: {exc}") from exc
        async with directory(deps).store.read() as ops:
            member = await ops.member_by_subject(me.subject)
        if member is None or member.mentor is None:
            raise HTTPException(status_code=403, detail="only an apprentice with a mentor")
        try:
            prop = await apprentices(deps).propose(
                access, member.mentor, body.action, body.target, checked, deps.clock()
            )
        except DeskError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return apprentice_proposal_view(prop)

    @app.get("/staff/apprentice/proposals")
    async def apprentice_proposals(deps: D, me: Employee) -> list[dict[str, Any]]:
        """An apprentice's own; a mentor's mentees'; every one for admins."""
        access = await _staff_access(deps, me)
        desk_ = apprentices(deps)
        if access.kind == "apprentice":
            found = await desk_.proposals(sid=access.sid)
        elif access.tier is not None and access.tier >= Tier.ADMIN:
            found = await desk_.proposals()
        else:
            found = await desk_.proposals(mentor=access.sid or "-")
        return [apprentice_proposal_view(p) for p in found]

    async def mentor_of(deps: Deps, me: Principal, pid: int) -> tuple[Access, Any]:
        access = await _staff_access(deps, me)
        prop = await apprentices(deps).proposal(pid)
        if prop is None:
            raise HTTPException(status_code=404, detail="unknown proposal")
        admin = access.tier is not None and access.tier >= Tier.ADMIN
        if access.sid != prop.mentor and not admin:
            raise HTTPException(status_code=403, detail="the apprentice's mentor decides")
        if prop.decision is not None:
            raise HTTPException(status_code=409, detail=f"proposal {pid} is {prop.decision}")
        return access, prop

    @app.post("/staff/apprentice/proposals/{pid}/approve")
    async def approve_apprentice(
        deps: D, me: Me, pid: Annotated[int, Path(ge=1)], body: DecisionBody
    ) -> dict[str, Any]:
        """The mentor approves: the action is carried out in the mentor's
        name (who must be allowed to do it), naming the apprentice."""
        _, prop = await mentor_of(deps, me, pid)
        b, now, tag = prop.body, deps.clock(), f"[proposed by {prop.sid}]"
        try:
            if prop.action == "case.note":
                await _need(deps, me, "case.work")
                await _cases(deps).note(prop.target, me.subject, f"{tag} {b['text']}", now)
            else:
                actions = list(b.get("actions", []))
                await _need(deps, me, "case.pass" if needs_authorisation(actions) else "case.work")
                await _cases(deps).decide(
                    prop.target, me.subject, b["outcome"], actions, f"{tag} {b['reason']}", now
                )
        except CaseRefused as exc:
            raise _case_refused(exc) from exc
        try:
            await apprentices(deps).decide(pid, "approved", me.subject, body.reason, "done", now)
        except DeskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        found = await apprentices(deps).proposal(pid)
        assert found is not None
        return apprentice_proposal_view(found)

    @app.post("/staff/apprentice/proposals/{pid}/reject")
    async def reject_apprentice(
        deps: D, me: Me, pid: Annotated[int, Path(ge=1)], body: DecisionBody
    ) -> dict[str, Any]:
        await mentor_of(deps, me, pid)
        try:
            await apprentices(deps).decide(pid, "rejected", me.subject, body.reason, None,
                                           deps.clock())  # fmt: skip
        except DeskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        found = await apprentices(deps).proposal(pid)
        assert found is not None
        return apprentice_proposal_view(found)

    # --- oversight (5d) ---------------------------------------------------------------

    def oversight(deps: Deps) -> Oversight:
        if deps.oversight is None:
            raise HTTPException(status_code=503, detail="oversight is not available")
        return deps.oversight

    @app.post("/staff/members/{sid}/reinstate")
    async def reinstate_member(
        deps: D,
        me: Employee,
        sid: Annotated[str, Path(pattern=r"^(STF|APP)[0-9]{8}$")],
        body: DecisionBody,
    ) -> dict[str, Any]:
        """A dormant account back to active (an admin)."""
        await _step_up(deps, me)
        now, staff = deps.clock(), directory(deps)
        member = await staff_step(lambda: staff.reinstate(_actor(me), sid, body.reason, now))
        if deps.desk is not None:
            await deps.desk.close(sid, "reinstated", now, "dormant")
        return member_view(member, now)

    @app.post("/staff/members/{sid}/review", status_code=201)
    async def review_member(
        deps: D,
        me: Employee,
        sid: Annotated[str, Path(pattern=r"^(STF|APP)[0-9]{8}$")],
        body: ReviewBody,
    ) -> dict[str, Any]:
        """An access review: the member's lead (their team), an owner (an
        admin), or an admin (anyone below admin); never oneself."""
        access, now = await _staff_access(deps, me), deps.clock()
        async with directory(deps).store.read() as ops:
            member = await ops.member(sid)
            if member is None:
                raise HTTPException(status_code=404, detail="unknown member")
            theirs = access_of(member, await ops.grants_of(sid), now)
        tier = access.tier
        allowed = (
            member.subject != me.subject
            and tier is not None
            and (
                (member.lead is not None and member.lead == access.sid)
                or (tier == Tier.OWNER)
                or (tier >= Tier.ADMIN and (theirs.tier is None or theirs.tier < Tier.ADMIN))
            )
        )
        if not allowed:
            raise _forbidden("staff.directory")
        await oversight(deps).review(sid, me.subject, body.decision, body.note, now)
        return {"sid": sid, "decision": body.decision, "reviewed_at": now, "by": me.subject}

    @app.get("/staff/reports/anomalies")
    async def anomaly_report(
        deps: D, me: Employee, days: Annotated[int, Query(ge=1, le=90)] = 7
    ) -> dict[str, Any]:
        """Approvals out of office hours and repeated maker-checker pairs
        (admins and owners; weekly by default)."""
        access = await _staff_access(deps, me)
        if access.tier is None or access.tier < Tier.ADMIN:
            raise HTTPException(status_code=403, detail="admins and owners read the report")
        policy = oversight_policy(await _staff_policy(deps))
        return await oversight(deps).anomalies(deps.clock() - days * DAY_MS, policy)

    @app.get("/admin/system")
    async def system_info(deps: D, me: Employee) -> dict[str, Any]:
        """What is running (rule AC23): application version and build,
        Python, PostgreSQL and Valkey, the API's start and the journal head
        (admins and owners)."""
        access = await _staff_access(deps, me)
        if access.tier is None or access.tier < Tier.ADMIN:
            raise HTTPException(status_code=403, detail="admins and owners read system information")
        if deps.system is None:
            raise HTTPException(status_code=503, detail="system information not available")
        return await deps.system.info()

    @app.get("/staff/audit")
    async def audit_log(
        deps: D,
        me: Employee,
        before: Annotated[int, Query(ge=0)] = 0,
        actor: Annotated[str | None, Query(max_length=200)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        """The security audit trail, newest first, and the auditor's last
        check (admins and owners). `before`: a seq, to page back."""
        access = await _staff_access(deps, me)
        if access.tier is None or access.tier < Tier.ADMIN:
            raise HTTPException(status_code=403, detail="admins and owners read the audit trail")
        if deps.audit is None:
            raise HTTPException(status_code=404, detail="no audit trail on this server")
        head, _ = await deps.audit.head()
        records = await deps.audit.records(
            after=before, limit=limit, actor=actor, newest_first=True
        )
        names: dict[str, str] = {}
        if deps.staff is not None:
            async with deps.staff.store.read() as ops:
                for subject in {r.event.actor for r in records}:
                    member = await ops.member_by_subject(subject)
                    if member is not None:
                        names[subject] = f"{member.name} ({member.sid})"
        status = await deps.audit.status()
        return {
            "head": head,
            "status": None
            if status is None
            else {
                "checked_at": status.checked_at,
                "verified_seq": status.verified_seq,
                "anchored_seq": status.anchored_seq,
                "broken_seq": status.broken_seq,
                "broken_reason": status.broken_reason,
            },
            "records": [
                {
                    "seq": r.seq,
                    "ts": r.event.ts,
                    "actor": r.event.actor,
                    "actor_name": names.get(r.event.actor),
                    "action": r.event.action,
                    "target": r.event.target,
                    "status": r.event.status,
                    "ip": r.event.ip,
                    "request_id": r.event.request_id,
                    "hash": r.hash,
                }
                for r in records
            ],
        }

    return app
