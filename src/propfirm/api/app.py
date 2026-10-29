"""The platform API: customers, checkout and trading.

Access:
- public: /health, /catalogue, /access;
- signed in (Keycloak access token): everything else. Quotes are shown to
  signed-in users only (Twelve Data licence: display to logged-in users);
- an account's data and trading belong to the CIF that bought it; staff
  and admin may read any account but trade none. Someone else's account
  answers 404, so account ids cannot be probed.

Trading: every change is an input submitted to the engine, and the response
carries the events the engine recorded for it. An engine rejection is
HTTP 200 with status "rejected"; 422 is for requests the API itself
refuses; 202 means not journaled yet (fetch /inputs/{entry_id} later).

Prices and levels are integer points, volumes integer hundredths of a lot,
money integer cents, times integer UTC milliseconds; floats are refused.
"""

import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from fractions import Fraction
from typing import Annotated, Any, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt, StringConstraints

from propfirm.api.auth import AuthError, Principal, TokenVerifier, bearer_token
from propfirm.customers.checkout import Checkout, CheckoutError, Registration
from propfirm.customers.store import (
    AccountRecord,
    AlreadyRegisteredError,
    CifRecord,
    OrderRecord,
)
from propfirm.journal.inputs import CancelOrder, ClosePosition, Input, ModifyStops, PlaceOrder
from propfirm.policy import Policy, ProductPolicy
from propfirm.service.feeder import now_ms
from propfirm.service.gateway import Outcome
from propfirm.trading.orders import OrderRequest, OrderType, Side

ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
Ident = Annotated[str, Path(pattern=ID_PATTERN)]
REJECTIONS = frozenset({"OrderRejected", "InputRejected"})
STAFF_ROLES = frozenset({"staff", "admin"})


class LiveSource(Protocol):
    async def head(self) -> dict[str, Any] | None: ...

    async def account(self, account_id: str) -> dict[str, Any] | None: ...

    async def quotes(self) -> dict[str, dict[str, Any]]: ...


class HistorySource(Protocol):
    async def account_events(
        self, account_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]: ...


class Submitter(Protocol):
    async def submit(self, item: Input, wait_s: float = 5.0) -> Outcome: ...

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome: ...


class Customers(Protocol):
    async def cif_by_subject(self, subject: str) -> CifRecord | None: ...

    async def account_owner(self, acc: str) -> str | None: ...

    async def accounts_for(self, cif: str) -> list[AccountRecord]: ...

    async def orders_for(self, cif: str) -> list[OrderRecord]: ...

    async def order(self, ord_id: str) -> OrderRecord | None: ...


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
    policy: Policy | None = None
    sandbox: bool = False  # sandbox payments endpoint; never in production
    country_header: str | None = None  # set by a trusted proxy (GeoIP), else None
    clock: Callable[[], int] = now_ms
    today: Callable[[], date] = _today
    new_id: Callable[[], str] = lambda: uuid.uuid4().hex
    wait_s: float = 5.0


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


class PurchaseBody(_Body):
    product_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,30}$")]
    size_cents: StrictInt
    code: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9-]{3,32}$")] | None = None


# --- views ----------------------------------------------------------------------------


def percent_text(share: Fraction | None) -> str | None:
    """A share as exact percentage text: 7/200 -> "3.5"; never a float."""
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


def product_view(p: ProductPolicy) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "profit_split_percent": percent_text(p.profit_split),
        "code_discount_max_percent": percent_text(p.code_discount_max),
        "repeat_discount_percent": [percent_text(d) for d in p.repeat_discounts],
        "fee_refund_with_payout": p.fee_refund_with_payout or None,
        "sizes": [{"size_cents": s, "price_cents": c} for s, c in p.prices_cents],
        "stages": [
            {
                "stage": r.stage.value,
                "rsv_id": r.rsv_id,
                "profit_target_percent": percent_text(r.profit_target),
                "max_loss_percent": percent_text(r.max_loss),
                "max_loss_kind": r.max_loss_kind.value,
                "daily_loss_percent": percent_text(r.daily_loss),
                "min_trading_days": r.min_trading_days,
                "consistency_percent": percent_text(r.consistency),
                "consistency_at_pass": r.consistency_at_pass,
            }
            for r in p.rule_sets
        ],
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
        "created_at": a.created_at,
    }


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


def _policy(deps: Deps) -> Policy:
    if deps.policy is None:
        raise HTTPException(status_code=503, detail="policy not loaded")
    return deps.policy


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
        """Products as the policy file defines them (prices, rules)."""
        return [product_view(p) for p in _policy(deps).products]

    @app.get("/access")
    async def access(deps: D) -> dict[str, Any]:
        """The country-gating switch, the countries it would refuse, the minimum age."""
        gate = _policy(deps).access
        return {
            "country_gating": gate.country_gating,
            "blocked_countries": sorted(gate.blocked_countries),
            "min_age": gate.min_age,
        }

    # --- customer ---------------------------------------------------------------------

    @app.get("/me")
    async def me(deps: D, me: Me) -> dict[str, Any]:
        cif = await _cif(deps, me)
        accounts = await deps.customers.accounts_for(cif.cif)
        orders = await deps.customers.orders_for(cif.cif)
        return {
            **cif_view(cif),
            "accounts": [account_record_view(a) for a in accounts],
            "orders": [order_view(o) for o in orders],
        }

    @app.post("/me", status_code=201)
    async def register(deps: D, me: Me, body: RegisterBody, request: Request) -> dict[str, Any]:
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
    async def purchase(deps: D, me: Me, body: PurchaseBody) -> dict[str, Any]:
        cif = await _cif(deps, me)
        try:
            order, pay_url = await _checkout(deps).place_order(
                cif, body.product_id, body.size_cents, body.code, deps.clock()
            )
        except CheckoutError as exc:
            raise _refused(exc) from exc
        return {**order_view(order), "pay_url": pay_url}

    @app.get("/orders/{ord_id}")
    async def get_order(deps: D, me: Me, ord_id: Ident) -> dict[str, Any]:
        cif = await _cif(deps, me)
        order = await deps.customers.order(ord_id)
        if order is None or order.cif != cif.cif:
            raise HTTPException(status_code=404, detail="unknown order")
        return order_view(order)

    @app.post("/dev/payments/{ord_id}/complete")
    async def sandbox_pay(deps: D, me: Me, ord_id: Ident) -> dict[str, Any]:
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

    @app.get("/accounts/{account_id}")
    async def account(deps: D, me: Me, account_id: Ident) -> dict[str, Any]:
        await _may_read(deps, me, account_id)
        found = await deps.live.account(account_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown account")
        return found

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
            if not accounts:
                raise HTTPException(status_code=404, detail="unknown input")
            for acc in accounts:
                await _owns(deps, me, str(acc))
        return _answer(result)

    @app.post("/accounts/{account_id}/orders")
    async def place(deps: D, me: Me, account_id: Ident, body: OrderBody) -> JSONResponse:
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
    async def cancel(deps: D, me: Me, account_id: Ident, order_id: Ident) -> JSONResponse:
        await _owns(deps, me, account_id)
        return await submit(deps, lambda ts: CancelOrder(account_id, order_id, ts))

    @app.post("/accounts/{account_id}/positions/{position_id}/close")
    async def close(
        deps: D, me: Me, account_id: Ident, position_id: Ident, body: CloseBody
    ) -> JSONResponse:
        await _owns(deps, me, account_id)
        return await submit(
            deps, lambda ts: ClosePosition(account_id, position_id, ts, body.volume)
        )

    @app.put("/accounts/{account_id}/positions/{position_id}/stops")
    async def stops(
        deps: D, me: Me, account_id: Ident, position_id: Ident, body: StopsBody
    ) -> JSONResponse:
        await _owns(deps, me, account_id)
        return await submit(
            deps,
            lambda ts: ModifyStops(account_id, position_id, body.stop_loss, body.take_profit, ts),
        )

    return app
