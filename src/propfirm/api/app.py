"""The platform API: customers, checkout, trading and admin settings.

Views, by Keycloak realm role (the portal's tokens carry `trader`, the back
office's carry `staff` and `admin`):
- public: /health; /catalogue (products, sizes, prices, split, discounts,
  and every stage's profit target, daily loss limit, drawdown limit,
  trading days and consistency rule); /access (the minimum age);
- signed in, any role: /quotes (Twelve Data licence: display to logged-in
  users only);
- trader: their own CIF, orders, accounts (an account shows its own
  target and limits, and a funded account its payable profit and payout
  eligibility), progressions and funded onboardings; buying; their
  identity check, contract and signature; trading their own accounts.
  Someone else's account answers 404, so ids cannot be probed;
- employee (role `staff`): reads every account, its history and inputs;
  reviews passed stages that lead to funding, and identity checks whose
  result differs from the customer record (approve or deny, never their
  own); integrity, fraud and compliance cases: raises alerts, takes, notes,
  decides; a decision with actions needs a second employee (never on their
  own customer record); trades none; no settings;
- admin: what an employee reads, and the admin-only settings: the
  country-gating switch and list (view and change); changing each stage's
  profit target, daily loss limit and drawdown limit; the change history.
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
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, StrictInt, StringConstraints

from propfirm.api.auth import AuthError, Principal, TokenVerifier, bearer_token
from propfirm.customers.cases_store import CaseRecord, CaseRefused
from propfirm.customers.casework import Casework
from propfirm.customers.checkout import Checkout, CheckoutError, Registration
from propfirm.customers.funding import Onboarding, Signature
from propfirm.customers.kyc import KycDecision, KycError, SandboxKyc, alpha2, didit_webhook_valid
from propfirm.customers.lifecycle import Lifecycle
from propfirm.customers.lifecycle_store import DecisionError, ProgressionRecord
from propfirm.customers.onboarding_store import OnboardingRecord, OnboardingStateError
from propfirm.customers.payouts import Eligibility, FundedAccount, eligibility
from propfirm.customers.store import (
    AccountRecord,
    AlreadyRegisteredError,
    CifRecord,
    OrderRecord,
)
from propfirm.journal.inputs import CancelOrder, ClosePosition, Input, ModifyStops, PlaceOrder
from propfirm.policy import (
    Policy,
    PolicyError,
    PolicySource,
    ProductPolicy,
    parse_countries,
    parse_percent,
    percent_text,
    rule_set_view,
)
from propfirm.service.feeder import now_ms
from propfirm.service.gateway import Outcome
from propfirm.service.projector import INTERVALS
from propfirm.settings.overlay import CountryGating, stage_limits, to_json
from propfirm.settings.service import Settings, SettingsError
from propfirm.settings.store import ChangeStatus
from propfirm.trading.orders import OrderRequest, OrderType, Side

ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
Ident = Annotated[str, Path(pattern=ID_PATTERN)]
REJECTIONS = frozenset({"OrderRejected", "InputRejected"})
TRADER_ROLES = frozenset({"trader"})
STAFF_ROLES = frozenset({"staff", "admin"})  # staff: employees
ADMIN_ROLES = frozenset({"admin"})


class LiveSource(Protocol):
    async def head(self) -> dict[str, Any] | None: ...

    async def account(self, account_id: str) -> dict[str, Any] | None: ...

    async def quotes(self) -> dict[str, dict[str, Any]]: ...


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
    didit_webhook_secret: str | None = None
    sandbox: bool = False  # sandbox payments endpoint; never in production
    country_header: str | None = None  # set by a trusted proxy (GeoIP), else None
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


class OrderBody(_Body):
    symbol: str
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop"]
    volume: StrictInt
    level: StrictInt | None = None
    stop_loss: StrictInt | None = None
    take_profit: StrictInt | None = None
    order_id: Annotated[str, StringConstraints(pattern=ID_PATTERN)] | None = None


class CloseBody(_Body):
    volume: StrictInt | None = None


class StopsBody(_Body):
    stop_loss: StrictInt | None
    take_profit: StrictInt | None


class RegisterBody(_Body):
    full_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    date_of_birth: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    residence_country: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]


PRODUCT_PATTERN = r"^[a-z][a-z0-9-]{0,30}$"
Percent = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class PurchaseBody(_Body):
    product_id: Annotated[str, StringConstraints(pattern=PRODUCT_PATTERN)]
    size_cents: StrictInt
    code: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9-]{3,32}$")] | None = None


class DecisionBody(_Body):
    reason: Reason


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


class StageLimitsBody(_Body):
    profit_target_percent: Percent | None  # null: no target (funded stages)
    daily_loss_percent: Percent
    max_loss_percent: Percent  # the drawdown limit
    max_loss_kind: Literal["static", "trailing"]
    reason: Reason


# --- views ----------------------------------------------------------------------------


def product_view(p: ProductPolicy) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "profit_split_percent": percent_text(p.profit_split),
        "code_discount_max_percent": percent_text(p.code_discount_max),
        "repeat_discount_percent": [percent_text(d) for d in p.repeat_discounts],
        "fee_refund_with_payout": p.fee_refund_with_payout or None,
        "sizes": [{"size_cents": s, "price_cents": c} for s, c in p.prices_cents],
        "stages": [rule_set_view(r) for r in p.rule_sets],
    }


def change_view(status: ChangeStatus) -> dict[str, Any]:
    change = status.change
    kind, value = to_json(change.setting)
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
    }


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
    "other_text": 422,
    "name": 422,
}


def _onboarding_refused(exc: OnboardingStateError) -> HTTPException:
    return HTTPException(
        status_code=ONBOARDING_ERRORS[exc.code], detail={"code": exc.code, "message": str(exc)}
    )


def eligibility_view(e: Eligibility) -> dict[str, Any]:
    return {
        "acc": e.acc,
        "as_of": e.as_of,
        "eligible": e.eligible,
        "basis": e.basis,
        "measured_cents": e.measured_cents,
        "baseline_cents": e.baseline_cents,
        "violations_cents": e.violations_cents,
        "payable_cents": e.payable_cents,
        "split_percent": e.split_percent,
        "trader_share_cents": e.trader_share_cents,
        "firm_share_cents": e.firm_share_cents,
        "fee_refund_cents": e.fee_refund_cents,
        "trader_amount_cents": e.trader_amount_cents,
        "period_start": e.period_start,
        "trading_days": e.trading_days,
        "next_pay_date": e.next_pay_date.isoformat(),
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in e.checks],
    }


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


async def _principal(deps: D, authorization: Annotated[str | None, Header()] = None) -> Principal:
    try:
        return await deps.verifier.verify(bearer_token(authorization))
    except AuthError as exc:
        raise HTTPException(
            status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}
        ) from exc


Me = Annotated[Principal, Depends(_principal)]


def _role(me: Principal, roles: frozenset[str]) -> None:
    if not me.roles & roles:
        raise HTTPException(status_code=403, detail="not in this login's view")


async def _trader(me: Me) -> Principal:
    _role(me, TRADER_ROLES)
    return me


async def _admin(me: Me) -> Principal:
    _role(me, ADMIN_ROLES)
    return me


async def _staff(me: Me) -> Principal:
    _role(me, STAFF_ROLES)
    return me


Trader = Annotated[Principal, Depends(_trader)]
Staff = Annotated[Principal, Depends(_staff)]
Admin = Annotated[Principal, Depends(_admin)]
PrgId = Annotated[str, Path(pattern=r"^PRG[0-9]{8}$")]
STATUSES = Literal[
    "provisioning", "provisioned", "failed", "review", "approved", "denied", "blocked"
]
OnbId = Annotated[str, Path(pattern=r"^ONB[0-9]{8}$")]
CasId = Annotated[str, Path(pattern=r"^CAS[0-9]{8}$")]
CASE_STATUSES = Literal["open", "decision_pending", "actioning", "closed"]
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


def _onboarding(deps: Deps) -> Onboarding:
    if deps.onboarding is None:
        raise HTTPException(status_code=503, detail="funded onboarding is not available")
    return deps.onboarding


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
    if me.roles & STAFF_ROLES:
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
    }.get(exc.code, 422)
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


# --- the app --------------------------------------------------------------------------


def create_app(
    deps: Deps | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """`deps` directly (tests), or set on `app.state.deps` by `lifespan`."""
    app = FastAPI(title="propfirm platform API", version="0.2.0", lifespan=lifespan)
    app.state.deps = deps

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

    @app.get("/catalogue")
    async def catalogue(deps: D) -> list[dict[str, Any]]:
        """Products in force: sizes, prices, terms, targets and loss limits."""
        return [product_view(p) for p in (await _policy(deps)).products]

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
        form = Registration(body.full_name, dob, body.residence_country)
        try:
            cif = await _checkout(deps).register(
                me.subject, me.email, form, deps.today(), deps.clock(), ip_country
            )
        except AlreadyRegisteredError as exc:
            raise HTTPException(status_code=409, detail="already registered") from exc
        except CheckoutError as exc:
            raise _refused(exc) from exc
        return cif_view(cif)

    @app.post("/orders", status_code=201)
    async def purchase(deps: D, me: Trader, body: PurchaseBody) -> dict[str, Any]:
        cif = await _cif(deps, me)
        try:
            order, pay_url = await _checkout(deps).place_order(
                cif, body.product_id, body.size_cents, body.code, deps.clock()
            )
        except CheckoutError as exc:
            raise _refused(exc) from exc
        return {**order_view(order), "pay_url": pay_url}

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
        record = await deps.customers.account(account_id)
        live = await deps.live.account(account_id)
        if record is None or live is None:
            raise HTTPException(status_code=404, detail="unknown account")
        policy = await _policy(deps)
        order = await deps.customers.order(record.ord)
        cif = await deps.customers.cif(record.cif)
        assert order is not None and cif is not None
        try:
            product = policy.product(record.product_id)
        except KeyError as exc:
            raise HTTPException(status_code=409, detail="product not in the policy") from exc
        tier2 = await deps.onboarding.store.approved_case(cif.cif) if deps.onboarding else None
        rules = live["rules"]
        account = FundedAccount(
            acc=account_id,
            product=product,
            stage=rules["stage"],
            consistency=parse_percent(rules["consistency_percent"] or "", "consistency", True),
            funded_at=record.created_at,
            baseline_cents=record.size_cents,
            fee_paid_cents=order.price_cents,
            payouts_paid=0,  # payouts come with step 3d
            last_payout_at=None,
            status=live["status"],
            locked=live["locked"],
            balance_cents=live["balance_cents"],
            equity_cents=live["equity_cents"],
            open_positions=len(live["positions"]),
            pending_orders=len(live["pending_orders"]),
            trading_day_ids=live.get("trading_day_ids", []),
            day_pnl=[(d, c) for d, c in live.get("day_pnl", [])],
            cif_status=cif.status,
            tier2_approved=tier2 is not None,
            residence_country=cif.residence_country,
            document_country=tier2.document_country if tier2 else None,
            open_case=await deps.cases.has_open_case(cif.cif) if deps.cases else False,
            violations_cents=await deps.cases.removed_profit(account_id) if deps.cases else 0,
        )
        return eligibility_view(eligibility(policy, account, deps.clock()))

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
        if not me.roles & STAFF_ROLES:
            _role(me, TRADER_ROLES)
            if not accounts:
                raise HTTPException(status_code=404, detail="unknown input")
            for acc in accounts:
                await _owns(deps, me, str(acc))
        return _answer(result)

    @app.post("/accounts/{account_id}/orders")
    async def place(deps: D, me: Trader, account_id: Ident, body: OrderBody) -> JSONResponse:
        await _owns(deps, me, account_id)

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
        me: Staff,
        status: STATUSES = "review",
        limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
    ) -> list[dict[str, Any]]:
        """Progressions in one status; by default the funded reviews waiting."""
        found = await _lifecycle(deps).store.with_status(status, limit)
        return [progression_view(p) for p in found]

    @app.get("/staff/progressions/{prg}")
    async def progression(deps: D, me: Staff, prg: PrgId) -> dict[str, Any]:
        """One progression with its full history."""
        store = _lifecycle(deps).store
        record = await store.progression(prg)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown progression")
        history = [
            {"kind": e.kind, "ts": e.ts, "detail": dict(e.detail)} for e in await store.history(prg)
        ]
        return {**progression_view(record), "history": history}

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
                "open_case": 409,
            }[exc.code]
            raise HTTPException(
                status_code=status, detail={"code": exc.code, "message": str(exc)}
            ) from exc
        return progression_view(record)

    @app.post("/staff/progressions/{prg}/approve")
    async def approve(deps: D, me: Staff, prg: PrgId, body: DecisionBody) -> dict[str, Any]:
        """Approve a funded review: funded onboarding follows (step 3b)."""
        result: dict[str, Any] = await decide(deps, me, prg, True, body.reason)
        return result

    @app.post("/staff/progressions/{prg}/deny")
    async def deny(deps: D, me: Staff, prg: PrgId, body: DecisionBody) -> dict[str, Any]:
        result: dict[str, Any] = await decide(deps, me, prg, False, body.reason)
        return result

    # --- employees: identity reviews ---------------------------------------------------

    @app.get("/staff/onboardings")
    async def onboardings(
        deps: D,
        me: Staff,
        status: ONB_STATUSES = "review",
        limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
    ) -> list[dict[str, Any]]:
        """Onboardings in one status; by default the identity reviews waiting."""
        found = await _onboarding(deps).store.with_status(status, limit)
        return [onboarding_view(o) for o in found]

    @app.get("/staff/onboardings/{onb}")
    async def onboarding(deps: D, me: Staff, onb: OnbId) -> dict[str, Any]:
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
        return {**onboarding_view(record), "verified": verified, "history": history}

    async def decide_onboarding(
        deps: Deps, me: Principal, onb: str, approve: bool, reason: str
    ) -> dict[str, Any]:
        try:
            record = await _onboarding(deps).decide(
                onb, approve, me.subject, me.email, reason, deps.clock()
            )
        except OnboardingStateError as exc:
            raise _onboarding_refused(exc) from exc
        return onboarding_view(record)

    @app.post("/staff/onboardings/{onb}/approve")
    async def approve_onboarding(
        deps: D, me: Staff, onb: OnbId, body: DecisionBody
    ) -> dict[str, Any]:
        """Accept a verified identity that differs from the customer record."""
        return await decide_onboarding(deps, me, onb, True, body.reason)

    @app.post("/staff/onboardings/{onb}/deny")
    async def deny_onboarding(deps: D, me: Staff, onb: OnbId, body: DecisionBody) -> dict[str, Any]:
        return await decide_onboarding(deps, me, onb, False, body.reason)

    # --- employees: integrity, fraud and compliance cases --------------------------

    @app.post("/staff/cases", status_code=201)
    async def raise_alert(deps: D, me: Staff, body: AlertBody) -> dict[str, Any]:
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

    @app.get("/staff/cases")
    async def list_cases(
        deps: D,
        me: Staff,
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
    async def get_case(deps: D, me: Staff, cas: CasId) -> dict[str, Any]:
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
    async def assign_case(deps: D, me: Staff, cas: CasId) -> dict[str, Any]:
        """Take the case."""
        result: dict[str, Any] = await case_step(
            deps, lambda c: c.assign(cas, me.subject, deps.clock())
        )
        return result

    @app.post("/staff/cases/{cas}/notes")
    async def note_case(deps: D, me: Staff, cas: CasId, body: NoteBody) -> dict[str, Any]:
        result: dict[str, Any] = await case_step(
            deps, lambda c: c.note(cas, me.subject, body.text, deps.clock())
        )
        return result

    @app.post("/staff/cases/{cas}/decide")
    async def decide_case(deps: D, me: Staff, cas: CasId, body: CaseDecisionBody) -> dict[str, Any]:
        """cleared or warning closes the case; actioned proposes actions that
        a second employee must confirm."""
        actions = [a.model_dump(exclude_none=True) for a in body.actions]
        result: dict[str, Any] = await case_step(
            deps,
            lambda c: c.decide(cas, me.subject, body.outcome, actions, body.reason, deps.clock()),
        )
        return result

    @app.post("/staff/cases/{cas}/confirm")
    async def confirm_case(deps: D, me: Staff, cas: CasId, body: DecisionBody) -> dict[str, Any]:
        """The second employee confirms the proposed actions; they are carried out."""
        result: dict[str, Any] = await case_step(
            deps, lambda c: c.confirm(cas, me.subject, body.reason, deps.clock())
        )
        return result

    @app.post("/staff/cases/{cas}/reject")
    async def reject_case(deps: D, me: Staff, cas: CasId, body: DecisionBody) -> dict[str, Any]:
        """The second employee sends the proposed actions back."""
        result: dict[str, Any] = await case_step(
            deps, lambda c: c.reject(cas, me.subject, body.reason, deps.clock())
        )
        return result

    @app.post("/staff/cases/{cas}/reopen")
    async def reopen_case(deps: D, me: Staff, cas: CasId, body: DecisionBody) -> dict[str, Any]:
        """Reopen a closed case on new evidence (an appeal)."""
        result: dict[str, Any] = await case_step(
            deps, lambda c: c.reopen(cas, me.subject, body.reason, deps.clock())
        )
        return result

    # --- admin: settings --------------------------------------------------------------

    @app.get("/admin/access")
    async def admin_access(deps: D, me: Admin) -> dict[str, Any]:
        """The country-gating switch and list in force, and the minimum age."""
        gate = (await _policy(deps)).access
        return {
            "country_gating": gate.country_gating,
            "blocked_countries": sorted(gate.blocked_countries),
            "min_age": gate.min_age,
        }

    @app.put("/admin/access/country-gating")
    async def set_country_gating(deps: D, me: Admin, body: CountryGatingBody) -> dict[str, Any]:
        settings = _settings(deps)
        try:
            countries = parse_countries(body.blocked_countries, "blocked_countries")
        except PolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        setting = CountryGating(body.country_gating, countries)
        status = await settings.set_country_gating(
            me.subject, me.email, setting, body.reason, deps.clock()
        )
        return change_view(status)

    @app.put("/admin/products/{product_id}/stages/{stage}/limits")
    async def set_stage_limits(
        deps: D,
        me: Admin,
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
            )
            status, rule_set = await settings.set_stage_limits(
                me.subject, me.email, setting, body.reason, deps.clock()
            )
        except PolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SettingsError as exc:
            http_status = {"unknown": 404, "engine_refused": 502}.get(exc.code, 422)
            raise HTTPException(
                status_code=http_status, detail={"code": exc.code, "message": str(exc)}
            ) from exc
        applied = status.applied_at is not None
        return JSONResponse(
            {
                "change": change_view(status),
                "rsv_id": rule_set.rsv_id,
                "status": "applied" if applied else "pending",
            },
            status_code=200 if applied else 202,
        )

    @app.get("/admin/settings/changes")
    async def setting_changes(
        deps: D, me: Admin, limit: Annotated[int, Query(ge=1, le=1_000)] = 100
    ) -> list[dict[str, Any]]:
        """Every admin change, latest first, with who, why and when it took effect."""
        return [change_view(s) for s in await _settings(deps).history(limit)]

    return app
