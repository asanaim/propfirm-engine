"""The trading API.

Reads come from derived state (live state in Valkey, history tables);
every change is an input submitted to the engine, and the response carries
the events the engine recorded for it. A rejection is an engine decision,
returned with status "rejected" and HTTP 200; HTTP errors are kept for
requests the API itself refuses (422) or cannot find (404). When the
engine has not journaled the input within the wait, the answer is 202 with
the entry id, and the outcome can be fetched later.

Prices and levels are integer points, volumes integer hundredths of a lot,
times integer UTC milliseconds; floats are refused (CLAUDE.md).

Not yet here [P2 step 2]: login (Keycloak) and account ownership. Until
then the API must only listen on localhost.
"""

import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt, StringConstraints

from propfirm.journal.inputs import CancelOrder, ClosePosition, Input, ModifyStops, PlaceOrder
from propfirm.service.feeder import now_ms
from propfirm.service.gateway import Outcome
from propfirm.trading.orders import OrderRequest, OrderType, Side

ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
Ident = Annotated[str, Path(pattern=ID_PATTERN)]
REJECTIONS = frozenset({"OrderRejected", "InputRejected"})


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


@dataclass(frozen=True, slots=True)
class Deps:
    live: LiveSource
    history: HistorySource
    gateway: Submitter
    clock: Callable[[], int] = now_ms
    new_id: Callable[[], str] = lambda: uuid.uuid4().hex
    wait_s: float = 5.0


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


def _deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    return deps


D = Annotated[Deps, Depends(_deps)]


def create_app(
    deps: Deps | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """`deps` directly (tests), or set on `app.state.deps` by `lifespan`."""
    app = FastAPI(title="propfirm engine API", version="0.1.0", lifespan=lifespan)
    app.state.deps = deps

    async def submit(deps: Deps, build: Callable[[int], Input]) -> JSONResponse:
        try:
            item = build(deps.clock())
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _answer(await deps.gateway.submit(item, deps.wait_s))

    @app.get("/health")
    async def health(deps: D) -> dict[str, Any]:
        head = await deps.live.head()
        return {"status": "ok", "journal_seq": head["seq"] if head else None}

    @app.get("/quotes")
    async def quotes(deps: D) -> dict[str, dict[str, Any]]:
        return await deps.live.quotes()

    @app.get("/accounts/{account_id}")
    async def account(deps: D, account_id: Ident) -> dict[str, Any]:
        found = await deps.live.account(account_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown account")
        return found

    @app.get("/accounts/{account_id}/history")
    async def history(
        deps: D,
        account_id: Ident,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
    ) -> list[dict[str, Any]]:
        return await deps.history.account_events(account_id, after, limit)

    @app.get("/inputs/{entry_id}")
    async def outcome(
        deps: D, entry_id: Annotated[str, Path(pattern=r"^[0-9]+-[0-9]+$")]
    ) -> JSONResponse:
        return _answer(await deps.gateway.outcome(entry_id, 0))

    @app.post("/accounts/{account_id}/orders")
    async def place(deps: D, account_id: Ident, body: OrderBody) -> JSONResponse:
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
    async def cancel(deps: D, account_id: Ident, order_id: Ident) -> JSONResponse:
        return await submit(deps, lambda ts: CancelOrder(account_id, order_id, ts))

    @app.post("/accounts/{account_id}/positions/{position_id}/close")
    async def close(
        deps: D, account_id: Ident, position_id: Ident, body: CloseBody
    ) -> JSONResponse:
        return await submit(
            deps, lambda ts: ClosePosition(account_id, position_id, ts, body.volume)
        )

    @app.put("/accounts/{account_id}/positions/{position_id}/stops")
    async def stops(
        deps: D, account_id: Ident, position_id: Ident, body: StopsBody
    ) -> JSONResponse:
        return await submit(
            deps,
            lambda ts: ModifyStops(account_id, position_id, body.stop_loss, body.take_profit, ts),
        )

    return app
