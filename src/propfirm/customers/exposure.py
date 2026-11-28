"""Exposure desk (build plan step 5, line 4; D-14.32; strategic review v3, D4).

The engine holds every position, so scenario liability is computed by
exact revaluation of each funded account, not by notional:

    dL_k = sum_i s_i [ (E_i^k - I_i - V_i)+ - (E_i - I_i - V_i)+ ]

E_i^k is the account's equity after scenario k, floored where its own loss
limit would close it; s_i its split, I_i + V_i the payout baseline and
violations (the payout rules' own). A positive dL is what the firm would owe
more. The worst case is rounded up to the cent.

Scenarios: each symbol with funded positions moves by plus and minus 1 and
3 daily standard deviations of the last 60 trading days (rounded up to a
basis point); with fewer than 20 days of history, by its asset class's
stress move instead. Blocs: every USD pair 1.5 % with gold 3 % the same
way as the other USD pairs (USD up: EUR/USD and XAU/USD down, USD/JPY up);
all crypto 10 %; all oil 6 %; each both ways.

Limits, on the free reserve R - L_c (the reserve less certain liability):
one symbol's worst case at most 25 %, a bloc's at most 50 %. At 80 % of a
limit the risk desk is alerted. At 100 % new orders on that symbol and side
get halved lot caps for classes B and C; at 150 %, quartered for all
classes; existing positions are untouched. A cut returns to the baseline
after 24 hours continuously under 60 %. The side cut is the one that wins
in the scenario (a rise: buy).

Cut levels are recorded in `exposure_moves` (append-only), each with the
settings version it was decided under, and read back by replaying them.
The engine applies the cuts in force to new orders (`risk_sync`, D-14.34).
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import pairwise
from math import isqrt
from typing import Any, Protocol

import asyncpg

from propfirm.market.symbols import AssetClass
from propfirm.trading.instruments import InstrumentSpec, conversion_route

log = logging.getLogger(__name__)

BP = Fraction(1, 10_000)
NONE, HALVE, QUARTER = "none", "halve", "quarter"
RANK = {NONE: 0, HALVE: 1, QUARTER: 2}
CLASSES_CUT = {NONE: (), HALVE: ("B", "C"), QUARTER: ("A", "B", "C")}
HOUR_MS = 3_600_000


# --- revaluation --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    sign: int  # +1 buy, -1 sell
    volume: int  # hundredths of a lot
    open_price: int  # points
    exit_price: int  # points: where it would close now


@dataclass(frozen=True, slots=True)
class Book:
    """One funded account as the desk sees it."""

    acc: str
    split: Fraction  # the trader's share, 0 to 1
    threshold_cents: int  # I + V: profit above this is owed at the split
    equity_cents: int
    floor_cents: int  # the equity at which its loss limit closes it
    positions: tuple[Position, ...]


def pnl_usd(
    p: Position,
    exit_price: Fraction,
    specs: Mapping[str, InstrumentSpec],
    rates: Mapping[str, Fraction],
) -> Fraction:
    """Exact P&L in USD of closing `p` at `exit_price` (points), converted
    at `rates` (prices, not points) the way the engine converts."""
    spec = specs[p.symbol]
    quote: Fraction = (exit_price - p.open_price) * p.sign * p.volume * spec.contract_size
    amount: Fraction = quote / (10**spec.symbol.digits * 100)
    if spec.quote_currency == "USD":
        return amount
    route, multiply = conversion_route(spec.quote_currency, specs)
    rate: Fraction = rates[route]
    usd: Fraction = amount * rate if multiply else amount / rate
    return usd


def owed(book: Book, equity: Fraction) -> Fraction:
    return book.split * max(Fraction(0), equity - book.threshold_cents)


def scenario_equity(
    book: Book,
    moves: Mapping[str, Fraction],
    specs: Mapping[str, InstrumentSpec],
    rates: Mapping[str, Fraction],
) -> Fraction:
    """The account's equity (cents) after every symbol in `moves` moved by
    its fraction, floored where the loss limit would close it."""
    moved = {s: r * (1 + moves.get(s, Fraction(0))) for s, r in rates.items()}
    change = Fraction(0)
    for p in book.positions:
        m = moves.get(p.symbol, Fraction(0))
        before = pnl_usd(p, Fraction(p.exit_price), specs, rates)
        after = pnl_usd(p, p.exit_price * (1 + m), specs, moved)
        change += (after - before) * 100
    return max(Fraction(book.floor_cents), book.equity_cents + change)


def scenario_loss(
    books: Iterable[Book],
    moves: Mapping[str, Fraction],
    specs: Mapping[str, InstrumentSpec],
    rates: Mapping[str, Fraction],
) -> Fraction:
    """dL_k: what the firm would owe more after the scenario (cents)."""
    total = Fraction(0)
    for b in books:
        total += owed(b, scenario_equity(b, moves, specs, rates)) - owed(
            b, Fraction(b.equity_cents)
        )
    return total


def ceil_cents(x: Fraction) -> int:
    return -(-x.numerator // x.denominator)


# --- scenarios ------------------------------------------------------------------------


def sigma_bp(closes: Sequence[int]) -> int | None:
    """The sample standard deviation of daily returns, in basis points,
    rounded up (exact: integer square root of the variance); None with
    fewer than two returns."""
    returns = [Fraction(b, a) - 1 for a, b in pairwise(closes) if a > 0]
    n = len(returns)
    if n < 2:
        return None
    mean = sum(returns, Fraction(0)) / n
    var = sum(((r - mean) ** 2 for r in returns), Fraction(0)) / (n - 1)
    scaled = var * 10**8  # basis points squared
    p, q = scaled.numerator, scaled.denominator
    root = isqrt(p // q)
    while root * root * q < p:
        root += 1
    return root


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    kind: str  # "symbol" or "bloc"
    group: str  # the symbol, or the bloc's name
    moves: Mapping[str, Fraction] = field(hash=False)


def symbol_scenarios(symbol: str, sigma: int | None, stress_bp: int) -> list[Scenario]:
    """Plus and minus 1 and 3 sigma; plus and minus the stress move when
    the history is too short."""
    steps = [(f"{k} sd", k * sigma) for k in (1, 3)] if sigma else [("stress", stress_bp)]
    out = []
    for label, bp in steps:
        for sign, word in ((1, "+"), (-1, "-")):
            out.append(
                Scenario(f"{symbol} {word}{label}", "symbol", symbol, {symbol: sign * bp * BP})
            )
    return out


def bloc_scenarios(specs: Mapping[str, InstrumentSpec], policy: Any) -> list[Scenario]:
    """USD with gold, crypto, oil; each both ways."""
    usd: dict[str, Fraction] = {}
    for name, spec in specs.items():
        if spec.symbol.asset_class == AssetClass.FOREX and "USD" in name.split("/"):
            # USD up: CUR/USD falls, USD/CUR rises
            usd[name] = (1 if name.startswith("USD/") else -1) * policy.bloc_usd_bp * BP
        elif name == "XAU/USD":
            usd[name] = -policy.bloc_gold_bp * BP
    blocs: list[tuple[str, dict[str, Fraction]]] = [("USD", usd)]
    for label, cls, bp in (
        ("crypto", AssetClass.CRYPTO, policy.bloc_crypto_bp),
        ("oil", AssetClass.OIL, policy.bloc_oil_bp),
    ):
        blocs.append((label, {n: bp * BP for n, s in specs.items() if s.symbol.asset_class == cls}))
    out = []
    for label, moves in blocs:
        if not moves:
            continue
        out.append(Scenario(f"{label} up", "bloc", label, dict(moves)))
        out.append(Scenario(f"{label} down", "bloc", label, {s: -m for s, m in moves.items()}))
    return out


# --- limits and cuts ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Result:
    scenario: Scenario
    loss_cents: int  # rounded up; may be negative (the firm gains)


@dataclass(frozen=True, slots=True)
class Group:
    """A symbol or a bloc: its worst scenario against its limit."""

    kind: str
    name: str
    worst: Result
    limit_cents: int | None  # None: the reserve is off or empty

    @property
    def ratio(self) -> Fraction | None:
        if self.limit_cents is None:
            return None
        if self.limit_cents <= 0:
            return Fraction(10**9) if self.worst.loss_cents > 0 else Fraction(0)
        return Fraction(max(0, self.worst.loss_cents), self.limit_cents)


def groups(results: Iterable[Result], free_cents: int | None, policy: Any) -> list[Group]:
    worst: dict[tuple[str, str], Result] = {}
    for r in results:
        key = (r.scenario.kind, r.scenario.group)
        if key not in worst or r.loss_cents > worst[key].loss_cents:
            worst[key] = r
    out = []
    for (kind, name), r in sorted(worst.items()):
        pct = policy.symbol_limit_percent if kind == "symbol" else policy.bloc_limit_percent
        limit = None if free_cents is None else free_cents * pct // 100
        out.append(Group(kind, name, r, limit))
    return out


def level_of(ratio: Fraction | None, policy: Any) -> str:
    if ratio is None:
        return NONE
    if ratio * 100 >= policy.quarter_percent:
        return QUARTER
    if ratio * 100 >= policy.halve_percent:
        return HALVE
    return NONE


def pressure(found: Iterable[Group]) -> dict[tuple[str, str], Fraction]:
    """The highest ratio bearing on each (symbol, side): a group's worst
    scenario bears on every symbol it moves, on the side that wins."""
    out: dict[tuple[str, str], Fraction] = {}
    for g in found:
        ratio = g.ratio
        if ratio is None:
            continue
        for symbol, m in g.worst.scenario.moves.items():
            if m == 0:
                continue
            key = (symbol, "buy" if m > 0 else "sell")
            out[key] = max(out.get(key, Fraction(0)), ratio)
    return out


@dataclass(frozen=True, slots=True)
class Cut:
    level: str
    since: int
    under_since: int | None = None  # under the restore line since


def step(cut: Cut | None, ratio: Fraction, now: int, policy: Any) -> tuple[Cut | None, str | None]:
    """The next state of one (symbol, side) and the event to record:
    raise (to a worse level), under, over, restore; None: nothing."""
    level = NONE if cut is None else cut.level
    want = level_of(ratio, policy)
    if RANK[want] > RANK[level]:
        return Cut(want, now), "raise"
    if cut is None:
        return None, None
    if ratio * 100 < policy.restore_percent:
        if cut.under_since is None:
            return Cut(cut.level, cut.since, now), "under"
        if now - cut.under_since >= policy.restore_hours * HOUR_MS:
            return None, "restore"
        return cut, None
    if cut.under_since is not None:
        return Cut(cut.level, cut.since), "over"
    return cut, None


def replay(moves: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], Cut]:
    """The cuts in force from the recorded moves, oldest first."""
    cuts: dict[tuple[str, str], Cut] = {}
    for m in moves:
        key = (str(m["symbol"]), str(m["side"]))
        at = int(m["at"])
        event = m["event"]
        if event == "raise":
            cuts[key] = Cut(str(m["level"]), at)
        elif event == "restore":
            cuts.pop(key, None)
        elif key in cuts:
            c = cuts[key]
            cuts[key] = Cut(c.level, c.since, at if event == "under" else None)
    return cuts


# --- the desk -------------------------------------------------------------------------


class PolicyNow(Protocol):
    async def current(self) -> Any: ...

    async def version(self) -> int: ...


class Live(Protocol):
    async def account(self, account_id: str) -> dict[str, Any] | None: ...

    async def quotes(self) -> dict[str, dict[str, Any]]: ...


class Bars(Protocol):
    async def bars(
        self, symbol: str, interval: str, limit: int = 300, before: int | None = None
    ) -> list[dict[str, int]]: ...


class Eligibilities(Protocol):
    async def eligibility(self, acc: str, now: int) -> Any: ...


class CoverageSource(Protocol):
    async def coverage(self, now: int) -> Any: ...


class Notifier(Protocol):
    async def notify(
        self,
        audience: str,
        kind: str,
        ref: str,
        title: str,
        body: str,
        ts: int,
        urgent: bool = False,
    ) -> int: ...

    async def close(self, ref: str, reason: str, ts: int, kind: str | None = None) -> int: ...


@dataclass(frozen=True, slots=True)
class Desk:
    as_of: int
    free_reserve_cents: int | None
    accounts: int
    unread: tuple[str, ...]
    groups: tuple[Group, ...]
    cuts: Mapping[tuple[str, str], Cut]
    pressure: Mapping[tuple[str, str], Fraction]
    levels: tuple[str, ...]  # the level each group's ratio calls for, in order


class Exposure:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        policy: PolicyNow,
        live: Live,
        bars: Bars,
        payouts: Eligibilities,
        reserve: CoverageSource | None,
        notifier: Notifier | None = None,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._policy = policy
        self._live = live
        self._bars = bars
        self._payouts = payouts
        self._reserve = reserve
        self._notifier = notifier
        self._last = -1
        self._alerted: frozenset[str] = frozenset()

    async def _books(self, now: int) -> tuple[list[Book], list[str]]:
        rows = await self._pool.fetch(
            f"SELECT acc FROM {self._s}.accounts WHERE stage = 'funded' ORDER BY acc"
        )
        books, unread = [], []
        for r in rows:
            acc = str(r["acc"])
            view = await self._live.account(acc)
            if view is None or view.get("status") != "active" or not view.get("positions"):
                continue
            try:
                e = await self._payouts.eligibility(acc, now)
            except Exception:  # one broken account must not hide the others
                log.exception("exposure: eligibility of %s", acc)
                unread.append(acc)
                continue
            books.append(
                Book(
                    acc,
                    Fraction(e.split_percent) / 100,
                    e.baseline_cents + e.violations_cents,
                    int(view["equity_cents"]),
                    max(int(view["daily_floor_cents"]), int(view["max_floor_cents"])),
                    tuple(
                        Position(
                            str(p["symbol"]),
                            1 if p["side"] == "buy" else -1,
                            int(p["volume"]),
                            int(p["open_price"]),
                            int(p["current_price"]),
                        )
                        for p in view["positions"]
                    ),
                )
            )
        return books, unread

    async def desk(self, now: int) -> Desk:
        current = await self._policy.current()
        policy = current.exposure
        specs = {i.symbol.name: i for i in current.trading.instruments}
        books, unread = await self._books(now)
        quotes = await self._live.quotes()
        rates = {
            s: Fraction(int(q["mid"]), 10 ** specs[s].symbol.digits)
            for s, q in quotes.items()
            if q.get("mid") is not None and s in specs
        }
        held = sorted({p.symbol for b in books for p in b.positions})
        scenarios: list[Scenario] = []
        for symbol in held:
            try:
                bars = await self._bars.bars(symbol, "1d", policy.sigma_days + 1, None)
            except Exception:  # no history: the stress move, never no scenario
                log.exception("exposure: daily bars of %s", symbol)
                bars = []
            closes = [int(b["close"]) for b in bars]
            sigma = sigma_bp(closes) if len(closes) > policy.min_days else None
            stress = policy.stress_bp[specs[symbol].symbol.asset_class.value]
            scenarios += symbol_scenarios(symbol, sigma, stress)
        if held:
            scenarios += bloc_scenarios({s: specs[s] for s in specs if s in rates}, policy)
        results = []
        for sc in scenarios:
            try:
                loss = scenario_loss(books, sc.moves, specs, rates)
            except KeyError:  # a conversion rate is missing: no price yet
                log.warning("exposure: no rate for scenario %s", sc.name)
                continue
            results.append(Result(sc, ceil_cents(loss)))
        free: int | None = None
        if self._reserve is not None:
            c = await self._reserve.coverage(now)
            if c is not None:
                free = c.reserve_cents - c.certain_cents
        found = groups(results, free, policy)
        return Desk(
            now,
            free,
            len(books),
            tuple(unread),
            tuple(found),
            await self.cuts(),
            pressure(found),
            tuple(level_of(g.ratio, policy) for g in found),
        )

    async def moves(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT seq, at, symbol, side, event, level, ratio, scenario, policy_version "
            f"FROM {self._s}.exposure_moves ORDER BY seq DESC LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]

    async def cuts(self) -> dict[tuple[str, str], Cut]:
        rows = await self._pool.fetch(
            f"SELECT at, symbol, side, event, level FROM {self._s}.exposure_moves ORDER BY seq"
        )
        return replay([dict(r) for r in rows])

    async def watch(self, now: int) -> list[tuple[str, str, str]]:
        """Recompute; record cut events; alert the risk desk. Returns the
        events recorded, as (symbol, side, event)."""
        policy = (await self._policy.current()).exposure
        if not policy.on:
            return []
        if self._last >= 0 and now - self._last < policy.every_minutes * 60_000:
            return []
        self._last = now
        d = await self.desk(now)
        version = await self._policy.version()
        recorded: list[tuple[str, str, str]] = []
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"{s}.exposure")
            rows = await conn.fetch(
                f"SELECT at, symbol, side, event, level FROM {s}.exposure_moves ORDER BY seq"
            )
            cuts = replay([dict(r) for r in rows])
            for key in sorted(set(cuts) | set(d.pressure)):
                ratio = d.pressure.get(key, Fraction(0))
                after, event = step(cuts.get(key), ratio, now, policy)
                if event is None:
                    continue
                worst = max(
                    (
                        g
                        for g in d.groups
                        if g.ratio is not None and key[0] in g.worst.scenario.moves
                    ),
                    key=lambda g: g.ratio or 0,
                    default=None,
                )
                await conn.execute(
                    f"INSERT INTO {s}.exposure_moves (at, symbol, side, event, level, ratio, "
                    f"scenario, policy_version) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    now,
                    key[0],
                    key[1],
                    event,
                    (after or cuts[key]).level,
                    percent_text(ratio),
                    None if worst is None else worst.worst.scenario.name,
                    version,
                )
                recorded.append((key[0], key[1], event))
        await self._alert(d, policy, now, recorded)
        return recorded

    async def _alert(
        self, d: Desk, policy: Any, now: int, recorded: list[tuple[str, str, str]]
    ) -> None:
        if self._notifier is None:
            return
        hot = frozenset(
            g.name
            for g in d.groups
            if g.ratio is not None and g.ratio * 100 >= policy.alert_percent
        )
        if not hot:
            if self._alerted:
                await self._notifier.close(
                    "EXPOSURE", "exposure under the alert line", now, "exposure"
                )
            self._alerted = hot
            return
        if hot <= self._alerted and not any(e == "raise" for _, _, e in recorded):
            return
        self._alerted = hot
        lines = [
            f"{g.name}: worst {g.worst.scenario.name} {g.worst.loss_cents / 100:,.2f} USD, "
            f"{percent_text(g.ratio or Fraction(0))} of its limit"
            for g in d.groups
            if g.name in hot
        ]
        cuts = [f"{s} {side} {e}" for s, side, e in recorded if e == "raise"]
        body = "; ".join(lines) + (". Lot cap cuts: " + ", ".join(cuts) if cuts else "")
        await self._notifier.notify(
            "settings.change",
            "exposure",
            "EXPOSURE",
            "Exposure near its limit" if not cuts else "Exposure limit reached",
            body,
            now,
            urgent=bool(cuts),
        )


def percent_text(ratio: Fraction) -> str:
    """A ratio as a percent, rounded down to one decimal: 0.8049 -> "80.4 %"."""
    tenths = ratio * 1000
    whole = tenths.numerator // tenths.denominator
    return f"{whole // 10}.{whole % 10} %"
