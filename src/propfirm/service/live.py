"""The engine's current state, published to Valkey for the API.

After each batch (at most every `publish_ms`) the engine process writes:

    {prefix}:head            journal seq and hash, engine time
    {prefix}:account:{id}    one account: status, balance, equity, margin,
                             floors and limits, the next reset and the
                             daily floor from then, target, trading days,
                             positions, pending orders, trailing orders
                             (current level, its last move and when), and
                             the numbers of its own
                             rule-set version (it may be older than the
                             catalogue's)
    {prefix}:quotes          hash: symbol -> current bid/ask, frozen, blocked
    {prefix}:news            restricted events not over yet (the calendar)

This is a cache, never a record: the journal decides and keeps everything,
and a restart republishes it. It holds quotes and fill prices, which come
from licensed prices, so it is flushed with every licensed-data purge.

Limit [DEFAULT]: every account is republished on each publish; fine for
P2 volumes, to be narrowed to changed accounts before load test 6.
"""

import json
from typing import Any

import valkey.asyncio as avalkey

from propfirm.journal.machine import Machine
from propfirm.policy import rule_set_view
from propfirm.service.jsonview import view


def _dumps(data: object) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def account_view(machine: Machine, account_id: str) -> dict[str, Any]:
    sup = machine.supervisor
    engine = sup.engine
    daily_floor, max_floor = sup.floors(account_id)
    daily_limit, max_limit = sup.limits(account_id)
    next_reset, next_daily_floor = sup.next_reset(account_id)
    equity = engine.equity(account_id)
    used = engine.used_margin(account_id)
    return {
        "account_id": account_id,
        "rsv_id": sup.rule_set(account_id).rsv_id,
        "status": sup.status(account_id).value,
        "locked": engine.is_locked(account_id),
        "balance_cents": engine.balance(account_id),
        "equity_cents": equity,
        "used_margin_cents": used,
        "free_margin_cents": equity - used,
        "initial_balance_cents": sup.initial_balance(account_id),
        "daily_floor_cents": daily_floor,
        "max_floor_cents": max_floor,
        "daily_loss_limit_cents": daily_limit,
        "max_loss_limit_cents": max_limit,
        # before the 17:00 New York reset: the floor from then if equity stays
        "next_reset_ts": next_reset,
        "next_daily_floor_cents": next_daily_floor,
        "consistency_ok": sup.consistency_ok(account_id),
        "target_balance_cents": sup.target(account_id),  # None: no target (funded)
        "rules": rule_set_view(sup.rule_set(account_id)),  # this account's own version
        "trading_days": sup.trading_days(account_id),
        "trading_day_ids": list(sup.trading_day_ids(account_id)),
        "day_pnl": [[day, cents] for day, cents in sup.day_pnl(account_id)],
        "positions": _positions(machine, account_id),
        "pending_orders": view(engine.pending_orders(account_id)),
        "trailing_orders": _trailing(machine, account_id),
        "engine_ts": engine.now,
        "journal_seq": machine.seq,
    }


def _positions(machine: Machine, account_id: str) -> list[dict[str, Any]]:
    """Open positions with the price each would close at now and its P&L."""
    engine = machine.supervisor.engine
    floating = {pid: (price, pnl) for pid, price, pnl in engine.floating(account_id)}
    out = []
    for position in engine.positions(account_id):
        data = view(position)
        assert isinstance(data, dict)
        price, pnl = floating[position.position_id]
        out.append({**data, "current_price": price, "pnl_cents": pnl})
    return out


def _trailing(machine: Machine, account_id: str) -> list[dict[str, Any]]:
    """Working trailing orders, in placement order: each with its current
    level and the engine time and price update id of its last move (or of
    its placement, `moved_at` equal to `placed_at`)."""
    out = []
    for order, placed_at in machine.supervisor.engine.trailing_orders(account_id):
        data = view(order)
        assert isinstance(data, dict)
        out.append({**data, "placed_at": placed_at})
    return out


def news_view(machine: Machine) -> list[dict[str, Any]]:
    """Restricted events not over yet: no trade opens or closes on the
    listed symbols inside the window (M13)."""
    engine = machine.supervisor.engine
    return [
        {
            "event_id": event_id,
            "start_ts": window.start_ts,
            "end_ts": window.end_ts,
            "symbols": sorted(window.symbols),
        }
        for event_id, window in engine.calendar()
        if window.end_ts > engine.now
    ]


def quote_views(machine: Machine) -> dict[str, dict[str, Any]]:
    engine = machine.supervisor.engine
    out: dict[str, dict[str, Any]] = {}
    for symbol in sorted(engine.symbols):
        quote = engine.quote(symbol)
        out[symbol] = {
            "symbol": symbol,
            "bid": quote.bid if quote else None,
            "ask": quote.ask if quote else None,
            "mid": quote.mid if quote else None,
            "seq": quote.seq if quote else None,
            "source_ts": quote.source_ts if quote else None,
            "receive_ts": quote.receive_ts if quote else None,
            "digits": engine.symbol_specs[symbol].digits,
            "frozen": engine.is_frozen(symbol),
            "blocked": engine.is_blocked(symbol),
        }
    return out


class LivePublisher:
    def __init__(self, client: avalkey.Valkey, prefix: str) -> None:
        self._client = client
        self._prefix = prefix

    async def publish(self, machine: Machine) -> None:
        p = self._prefix
        pipe = self._client.pipeline(transaction=True)
        head = {
            "seq": machine.seq,
            "hash": machine.head,
            "engine_ts": machine.supervisor.engine.now,
        }
        pipe.set(f"{p}:head", _dumps(head))
        for account_id in machine.supervisor.accounts():
            pipe.set(f"{p}:account:{account_id}", _dumps(account_view(machine, account_id)))
        quotes = {symbol: _dumps(q) for symbol, q in quote_views(machine).items()}
        pipe.hset(f"{p}:quotes", mapping=quotes)
        pipe.set(f"{p}:news", json.dumps(news_view(machine), sort_keys=True, separators=(",", ":")))
        await pipe.execute()

    async def flush(self) -> int:
        """Delete everything published (licensed-data purge); return keys deleted."""
        deleted = 0
        async for key in self._client.scan_iter(match=f"{self._prefix}:*"):
            deleted += int(await self._client.delete(key))
        return deleted


class LiveReader:
    def __init__(self, client: avalkey.Valkey, prefix: str) -> None:
        self._client = client
        self._prefix = prefix

    async def head(self) -> dict[str, Any] | None:
        return _loads(await self._client.get(f"{self._prefix}:head"))

    async def account(self, account_id: str) -> dict[str, Any] | None:
        return _loads(await self._client.get(f"{self._prefix}:account:{account_id}"))

    async def news(self) -> list[dict[str, Any]]:
        raw = await self._client.get(f"{self._prefix}:news")
        if raw is None:
            return []
        data = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
        return data if isinstance(data, list) else []

    async def quotes(self) -> dict[str, dict[str, Any]]:
        key = f"{self._prefix}:quotes"
        raw: dict[Any, Any] = await self._client.hgetall(key)  # type: ignore[misc]
        out: dict[str, dict[str, Any]] = {}
        for symbol, value in raw.items():
            loaded = _loads(value)
            if loaded is not None:
                out[symbol.decode() if isinstance(symbol, bytes) else str(symbol)] = loaded
        return out


def _loads(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    data = json.loads(text)
    return data if isinstance(data, dict) else None
