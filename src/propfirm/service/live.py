"""The engine's current state, published to Valkey for the API.

After each batch (at most every `publish_ms`) the engine process writes:

    {prefix}:head            journal seq and hash, engine time
    {prefix}:account:{id}    one account: status, balance, equity, margin,
                             floors, target, trading days, positions,
                             pending orders, and the numbers of its own
                             rule-set version (it may be older than the
                             catalogue's)
    {prefix}:quotes          hash: symbol -> current bid/ask, frozen, blocked

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
        "daily_floor_cents": daily_floor,
        "max_floor_cents": max_floor,
        "target_balance_cents": sup.target(account_id),  # None: no target (funded)
        "rules": rule_set_view(sup.rule_set(account_id)),  # this account's own version
        "trading_days": sup.trading_days(account_id),
        "positions": view(engine.positions(account_id)),
        "pending_orders": view(engine.pending_orders(account_id)),
        "engine_ts": engine.now,
        "journal_seq": machine.seq,
    }


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
