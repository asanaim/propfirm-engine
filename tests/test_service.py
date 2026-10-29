"""Service settings, JSON views, the API contract, feeder and scheduler."""

import asyncio
from collections.abc import Sequence
from fractions import Fraction
from typing import Any

import httpx
import pytest

from propfirm.api.app import Deps, create_app
from propfirm.journal.inputs import (
    Advance,
    CancelOrder,
    Checkpoint,
    ClosePosition,
    Input,
    ModifyStops,
    PlaceOrder,
)
from propfirm.market.types import PriceUpdate
from propfirm.policy import load_policy
from propfirm.service.config import ConfigError, ServiceConfig
from propfirm.service.feeder import feed
from propfirm.service.gateway import Outcome
from propfirm.service.jsonview import view
from propfirm.service.scheduler import schedule
from propfirm.service.setup import development_setup
from propfirm.trading.orders import OrderType, Position, Side
from tests.auth_rig import FakeCustomers, bearer, verifier

ENV = {"DATABASE_URL": "postgresql://u:p@h/db", "VALKEY_URL": "redis://h:6379/0"}
NOW = 1_790_000_000_000


# --- settings and setup ---------------------------------------------------------------


def test_config_defaults_and_overrides() -> None:
    config = ServiceConfig.from_env(ENV)
    assert (config.schema, config.read_schema, config.stream) == (
        "engine",
        "engine_read",
        "engine:inputs",
    )
    assert config.live_prefix == "live:engine" and config.anchor_path is None
    other = ServiceConfig.from_env({**ENV, "PROPFIRM_BATCH": "50", "PROPFIRM_SCHEMA": "eng2"})
    assert (other.batch, other.schema) == (50, "eng2")


@pytest.mark.parametrize(
    "env",
    [
        {"VALKEY_URL": "redis://h"},
        {"DATABASE_URL": "postgresql://h"},
        {**ENV, "PROPFIRM_BATCH": "1.5"},
        {**ENV, "PROPFIRM_BATCH": "0"},
        {**ENV, "PROPFIRM_SCHEMA": "Engine; DROP"},
        {**ENV, "PROPFIRM_READ_SCHEMA": "engine"},
        {**ENV, "PROPFIRM_STREAM": "bad key with spaces"},
    ],
)
def test_bad_settings_are_refused(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        ServiceConfig.from_env(env)


def test_development_setup_has_every_symbol_and_product() -> None:
    setup = development_setup()
    assert len(setup.instruments) == 30
    assert {r.rsv_id for r in setup.rule_sets} >= {"lite-s1-v1", "instant-funded-v1"}
    assert setup.trade_blocks == () and setup.news_windows == ()


def test_json_view_is_exact_and_plain() -> None:
    position = Position(
        "p1", "A", "EUR/USD", Side.BUY, 10, 110_000, NOW, None, 120_000, Fraction(11, 10)
    )
    data = view(position)
    assert data["type"] == "Position" and data["side"] == "buy"
    assert data["margin_usd"] == "11/10"  # exact, never a float
    assert view(frozenset({"b", "a"})) == ["a", "b"]
    assert view((1, None, True)) == [1, None, True]
    with pytest.raises(TypeError):
        view(1.5)


# --- API contract ---------------------------------------------------------------------


class FakeLive:
    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, Any]] = {
            a: {"account_id": a, "status": "active"} for a in ("A", "B")
        }

    async def head(self) -> dict[str, Any] | None:
        return {"seq": 7}

    async def account(self, account_id: str) -> dict[str, Any] | None:
        return self.accounts.get(account_id)

    async def quotes(self) -> dict[str, dict[str, Any]]:
        return {"EUR/USD": {"bid": 1, "ask": 3}}


class FakeHistory:
    async def account_events(
        self, account_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        return [{"seq": after_seq + 1, "account_id": account_id, "limit": limit}]


class FakeGateway:
    def __init__(self, events: Sequence[Any] = (), seq: int | None = 9) -> None:
        self.sent: list[Input] = []
        self.events, self.seq = list(events), seq

    async def submit(self, item: Input, wait_s: float = 5.0) -> Outcome:
        self.sent.append(item)
        return Outcome("5-0", self.seq, self.events)

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome:
        return Outcome(entry_id, self.seq, self.events)


CUSTOMERS = FakeCustomers(
    cifs={"user-1": "CIF00000001", "user-2": "CIF00000002"},
    owners={"A": "CIF00000001", "B": "CIF00000002"},
)


def call(
    gateway: FakeGateway,
    method: str,
    url: str,
    body: object = None,
    policy: Any = None,
    *,
    user: str | None = "user-1",
    roles: tuple[str, ...] = ("trader",),
) -> httpx.Response:
    deps = Deps(
        FakeLive(),
        FakeHistory(),
        gateway,
        verifier(),
        CUSTOMERS,
        policy=policy,
        clock=lambda: NOW,
        new_id=lambda: "gen1",
    )
    app = create_app(deps)
    headers = bearer(user, roles) if user else {}

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            return await client.request(method, url, json=body, headers=headers)

    return asyncio.run(go())


def test_reads() -> None:
    g = FakeGateway(events=[{"type": "PositionOpened", "account_id": "A"}])
    assert call(g, "GET", "/health").json() == {"status": "ok", "journal_seq": 7}
    assert call(g, "GET", "/accounts/A").json()["status"] == "active"
    assert call(g, "GET", "/accounts/Z").status_code == 404
    assert call(g, "GET", "/quotes").json()["EUR/USD"]["ask"] == 3
    assert call(g, "GET", "/accounts/A/history?after=4&limit=9").json()[0]["seq"] == 5
    assert call(g, "GET", "/inputs/5-0").json()["seq"] == 9
    assert g.sent == []


def test_market_order_becomes_a_place_order_input() -> None:
    g = FakeGateway(events=[{"type": "PositionOpened", "position_id": "gen1"}])
    body = {
        "symbol": "EUR/USD",
        "side": "buy",
        "type": "market",
        "volume": 10,
        "stop_loss": 109_000,
    }
    response = call(g, "POST", "/accounts/A/orders", body)
    assert response.status_code == 200
    assert response.json() == {
        "entry_id": "5-0",
        "seq": 9,
        "status": "done",
        "events": [{"type": "PositionOpened", "position_id": "gen1"}],
    }
    (item,) = g.sent
    assert isinstance(item, PlaceOrder) and item.ts == NOW
    request = item.request
    assert (request.order_id, request.account_id, request.side, request.order_type) == (
        "gen1",
        "A",
        Side.BUY,
        OrderType.MARKET,
    )
    assert (request.volume, request.stop_loss, request.level) == (10, 109_000, None)


def test_engine_rejection_is_a_200_with_status_rejected() -> None:
    g = FakeGateway(events=[{"type": "OrderRejected", "reason": "news_window"}])
    body = {"symbol": "EUR/USD", "side": "sell", "type": "limit", "volume": 5, "level": 111_000}
    response = call(g, "POST", "/accounts/A/orders", {**body, "order_id": "mine-1"})
    assert response.status_code == 200 and response.json()["status"] == "rejected"
    assert isinstance(g.sent[0], PlaceOrder) and g.sent[0].request.order_id == "mine-1"


def test_not_yet_journaled_is_202_pending() -> None:
    g = FakeGateway(seq=None)
    response = call(g, "DELETE", "/accounts/A/orders/o1")
    assert response.status_code == 202
    assert response.json() == {"entry_id": "5-0", "status": "pending"}
    assert g.sent == [CancelOrder("A", "o1", NOW)]


def test_close_and_stops() -> None:
    g = FakeGateway()
    call(g, "POST", "/accounts/A/positions/p1/close", {"volume": 5})
    call(g, "POST", "/accounts/A/positions/p1/close", {})
    call(g, "PUT", "/accounts/A/positions/p1/stops", {"stop_loss": 1, "take_profit": None})
    assert g.sent == [
        ClosePosition("A", "p1", NOW, 5),
        ClosePosition("A", "p1", NOW, None),
        ModifyStops("A", "p1", 1, None, NOW),
    ]


GOOD = {"symbol": "EUR/USD", "side": "buy", "type": "market", "volume": 10}


@pytest.mark.parametrize(
    ("url", "body"),
    [
        ("/accounts/A/orders", {**GOOD, "volume": 10.0}),  # float
        ("/accounts/A/orders", {**GOOD, "volume": "10"}),  # string
        ("/accounts/A/orders", {**GOOD, "volume": True}),  # bool
        ("/accounts/A/orders", {**GOOD, "side": "long"}),
        ("/accounts/A/orders", {**GOOD, "leverage": 500}),  # unknown field
        ("/accounts/A/orders", {**GOOD, "level": 110_000}),  # level on a market order
        ("/accounts/A/orders", {**GOOD, "type": "limit"}),  # limit without level
        ("/accounts/A/orders", {**GOOD, "order_id": "bad id!"}),
        ("/accounts/A!/orders", GOOD),  # bad account id
        ("/accounts/A/positions/p1/stops", {"stop_loss": 0.5, "take_profit": None}),
    ],
)
def test_requests_the_api_refuses(url: str, body: dict[str, Any]) -> None:
    g = FakeGateway()
    method = "PUT" if url.endswith("/stops") else "POST"
    assert call(g, method, url, body).status_code == 422
    assert g.sent == []


# --- feeder and scheduler -------------------------------------------------------------


class Recorder:
    def __init__(self, stop: asyncio.Event | None = None, after: int = 0) -> None:
        self.items: list[Input] = []
        self._stop, self._after = stop, after

    async def publish(self, item: Input, entry_id: str = "*") -> str:
        self.items.append(item)
        if self._stop is not None and len(self.items) >= self._after:
            self._stop.set()
        return f"{len(self.items)}-0"


def test_feeder_sends_updates_in_order_and_stops() -> None:
    updates = [PriceUpdate("EUR/USD", n, NOW + n, NOW + n, 110_000 + n) for n in range(1, 6)]

    async def go() -> tuple[int, list[Input]]:
        stop = asyncio.Event()
        writer = Recorder(stop, after=3)
        sent = await feed(writer, iter(updates), stop, clock=lambda: NOW + 100)  # type: ignore[arg-type]
        return sent, writer.items

    sent, items = asyncio.run(go())
    assert sent == 3 and items == updates[:3]


def test_scheduler_ticks_and_checkpoints() -> None:
    times = iter(range(NOW, NOW + 10_000, 1_000))

    async def go() -> tuple[tuple[int, int], list[Input]]:
        stop = asyncio.Event()
        writer = Recorder(stop, after=7)
        result = await schedule(writer, stop, 1, 2_500, clock=lambda: next(times))  # type: ignore[arg-type]
        return result, writer.items

    (ticks, checkpoints), items = asyncio.run(go())
    # first checkpoint due at NOW + 2.5 s (the clock read at start), then every 2.5 s
    a = [Advance(NOW + k * 1_000) for k in range(1, 7)]
    assert items == [*a[:3], Checkpoint(NOW + 3_000), *a[3:], Checkpoint(NOW + 6_000)]
    assert (ticks, checkpoints) == (6, 2)


def test_catalogue_and_access_come_from_the_policy() -> None:
    policy = load_policy()
    products = call(FakeGateway(), "GET", "/catalogue", policy=policy).json()
    lite = products[0]
    assert (lite["id"], lite["name"], lite["profit_split_percent"]) == ("lite", "Lite", "80")
    assert lite["sizes"][1] == {"size_cents": 1_000_000, "price_cents": 4_400}
    s1 = lite["stages"][0]
    assert s1 == {
        "stage": "s1",
        "rsv_id": "lite-s1-v1",
        "profit_target_percent": "6",
        "max_loss_percent": "6",
        "max_loss_kind": "static",
        "daily_loss_percent": "3",
        "min_trading_days": 4,
        "consistency_percent": None,
        "consistency_at_pass": False,
    }
    instant = products[-1]["stages"][0]
    assert (instant["max_loss_kind"], instant["consistency_percent"]) == ("trailing", "50")
    access = call(FakeGateway(), "GET", "/access", policy=policy).json()
    assert access == {"country_gating": False, "blocked_countries": ["BD", "IN"], "min_age": 18}
    assert call(FakeGateway(), "GET", "/catalogue").status_code == 503  # no policy loaded


# --- access control -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "/quotes"),
        ("GET", "/me"),
        ("GET", "/accounts/A"),
        ("GET", "/accounts/A/history"),
        ("POST", "/accounts/A/orders"),
        ("DELETE", "/accounts/A/orders/o1"),
        ("GET", "/inputs/5-0"),
    ],
)
def test_signed_out_requests_are_refused(method: str, url: str) -> None:
    g = FakeGateway()
    body = GOOD if method == "POST" else None
    response = call(g, method, url, body, user=None)
    assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
    assert g.sent == []


def test_public_endpoints_need_no_login() -> None:
    policy = load_policy()
    for url in ("/health", "/catalogue", "/access"):
        assert call(FakeGateway(), "GET", url, policy=policy, user=None).status_code == 200


def test_someone_elses_account_looks_like_no_account() -> None:
    g = FakeGateway(events=[{"type": "PositionOpened", "account_id": "B"}])
    assert call(g, "GET", "/accounts/B").status_code == 404
    assert call(g, "GET", "/accounts/B/history").status_code == 404
    assert call(g, "POST", "/accounts/B/orders", GOOD).status_code == 404
    assert call(g, "POST", "/accounts/B/positions/p1/close", {}).status_code == 404
    assert call(g, "GET", "/inputs/5-0").status_code == 404  # its events are B's
    assert call(g, "GET", "/accounts/B", user="user-2").status_code == 200
    assert call(g, "GET", "/accounts/A", user="nobody").status_code == 404  # no CIF
    assert g.sent == []


def test_staff_read_every_account_but_trade_none() -> None:
    g = FakeGateway(events=[{"type": "PositionOpened", "account_id": "B"}])
    staff = ("trader", "staff")
    assert call(g, "GET", "/accounts/B", user="staff-1", roles=staff).status_code == 200
    assert call(g, "GET", "/inputs/5-0", user="staff-1", roles=staff).status_code == 200
    placed = call(g, "POST", "/accounts/B/orders", GOOD, user="staff-1", roles=staff)
    assert placed.status_code == 404 and g.sent == []
