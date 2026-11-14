"""Bad-price corrections (build plan step 1, line 7; D-14.4).

A price that should not have been applied (a spike from the feed) can fill
stops and take profits, close positions and breach accounts. Staff flag
it (symbol and price sequence) with the price it should have been; the
plan lists each account it touched and the amount that reverses its
effect; a second person approves; the engine then corrects each balance
(CorrectBalance) and reinstates each account the bad price breached
(ReinstateAccount). Positions it closed stay closed.

The amount per fill: a fill's result is linear in its price, so the result
at the good price is the recorded result scaled by the price moves:
for a close at the bad price, delta = pnl * (good - bad) / (close - open);
for an open at the bad price, delta = -pnl * (good - bad) / (close - open)
for each close of that position. Fill prices move with the mid (the
spread is kept). Exact where the quote currency is the account's (US
dollars); for a cross pair the conversion rate is taken as unchanged.
Rounding: half to even, to the cent (named here, tested).
A fill that cannot be scaled (closed at its open price, or a position
opened at the bad price still open) is listed for staff to handle.
"""

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Protocol

import asyncpg

from propfirm.journal.inputs import CorrectBalance, Input, ReinstateAccount
from propfirm.market.types import PriceUpdate
from propfirm.pgschema import check_schema
from propfirm.service.gateway import Outcome


class CorrectionRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Item:
    account_id: str
    amount_cents: int
    reinstate: bool
    fills: int  # fills at the bad price this item corrects
    manual: tuple[str, ...]  # what staff must handle by hand

    def view(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "amount_cents": self.amount_cents,
            "reinstate": self.reinstate,
            "fills": self.fills,
            "manual": list(self.manual),
        }


def scaled(pnl: int, move: int, span: int) -> int:
    """pnl * move / span, rounded half to even to the cent."""
    value = (Decimal(pnl) * move / span).quantize(Decimal(1), ROUND_HALF_EVEN)
    return int(value)


def plan(
    events: Iterable[Mapping[str, Any]], symbol: str, price_seq: int, bad: int, good: int
) -> list[Item]:
    """The correction of every account in `events` (their projected
    histories) that a bad price touched, in account order."""
    move = good - bad
    by_account: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for e in events:
        by_account[str(e["account_id"])].append(e)
    items = []
    for acc in sorted(by_account):
        history = by_account[acc]
        closes: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for e in history:
            if e.get("type") == "PositionClosed":
                closes[str(e["position_id"])].append(e)
        delta, fills, manual, bad_seqs = 0, 0, [], set()

        def at_bad(e: Mapping[str, Any]) -> bool:
            return e.get("symbol") == symbol and int(e.get("price_seq", -1)) == price_seq

        for e in history:
            kind = e.get("type")
            if kind == "PositionClosed" and at_bad(e):
                fills += 1
                bad_seqs.add(int(e["seq"]))
                span = int(e["close_price"]) - int(e["open_price"])
                if span == 0:
                    manual.append(f"{e['close_id']}: closed at its open price")
                    continue
                delta += scaled(int(e["pnl_cents"]), move, span)
            elif kind == "PositionOpened" and at_bad(e):
                fills += 1
                pid = str(e["position_id"])
                later = closes.get(pid, [])
                if not later or int(later[-1]["remaining_volume"]) != 0:
                    manual.append(f"{pid}: opened at the bad price and still open")
                for c in later:
                    span = int(c["close_price"]) - int(c["open_price"])
                    if span == 0:
                        manual.append(f"{c['close_id']}: closed at its open price")
                        continue
                    delta += scaled(int(c["pnl_cents"]), -move, span)
        if fills == 0:
            continue
        reinstate = any(
            e.get("type") == "AccountBreached" and int(e["seq"]) in bad_seqs for e in history
        )
        items.append(Item(acc, delta, reinstate, fills, tuple(manual)))
    return items


def inputs(
    cor: str, price_ts: int, items: Iterable[Item], ts: int
) -> list[CorrectBalance | ReinstateAccount]:
    """The engine inputs of an approved correction: balances first, then
    reinstatements (a breached account is reinstated once corrected)."""
    out: list[CorrectBalance | ReinstateAccount] = [
        CorrectBalance(i.account_id, i.amount_cents, cor, price_ts, ts)
        for i in items
        if i.amount_cents != 0
    ]
    out += [ReinstateAccount(i.account_id, cor, price_ts, ts) for i in items if i.reinstate]
    return out


class Submitter(Protocol):
    async def submit(self, item: Input, wait_s: float = 5.0) -> Outcome: ...


def outcome_status(outcome: Outcome) -> str:
    """done, rejected (the engine refused it) or pending (not journaled yet)."""
    if outcome.seq is None:
        return "pending"
    if any(e.get("type") == "InputRejected" for e in outcome.events):
        return "rejected"
    return "done"


class Corrections:
    def __init__(
        self,
        pool: asyncpg.Pool,
        customer_schema: str,
        journal_schema: str,
        read_schema: str,
        gateway: Submitter,
        wait_s: float = 5.0,
    ) -> None:
        self._pool = pool
        self._s = check_schema(customer_schema)
        self._j = check_schema(journal_schema)
        self._r = check_schema(read_schema)
        self._gateway = gateway
        self._wait_s = wait_s

    async def _bad_price(self, conn: Any, symbol: str, price_seq: int) -> PriceUpdate:
        """The applied update (symbol, seq), from the price store (prices live
        beside the journal, purgeable under the data licence)."""
        filled = await conn.fetchval(
            f"SELECT 1 FROM {self._r}.account_events WHERE event->>'symbol' = $1 "
            f"AND (event->>'price_seq')::bigint = $2 LIMIT 1",
            symbol,
            price_seq,
        )
        if filled is None:
            raise CorrectionRefused("no_fills", f"no fill at {symbol} price {price_seq}")
        row = await conn.fetchrow(
            f"SELECT symbol, seq, source_ts, receive_ts, price FROM {self._j}.prices "
            f"WHERE symbol = $1 AND seq = $2 ORDER BY journal_seq LIMIT 1",
            symbol,
            price_seq,
        )
        if row is None:
            raise CorrectionRefused("no_price", f"{symbol} price {price_seq} is not stored")
        return PriceUpdate(
            row["symbol"], row["seq"], row["source_ts"], row["receive_ts"], row["price"]
        )

    async def suspects(
        self, since: int, min_basis_points: int = 20, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Prices that look bad, newest first: an update that moved at least
        `min_basis_points` from the one before AND back by as much at the
        next (a spike), with how many fills used it. Staff pick one instead
        of looking up a price number (owner remark, 14 Nov 2026)."""
        rows = await self._pool.fetch(
            f"""
            WITH p AS (
                SELECT symbol, seq, receive_ts, price,
                       lag(price) OVER w AS before, lead(price) OVER w AS after
                FROM {self._j}.prices
                WHERE receive_ts >= $1
                WINDOW w AS (PARTITION BY symbol ORDER BY seq)
            )
            SELECT symbol, seq, receive_ts, price, before, after FROM p
            WHERE before IS NOT NULL AND after IS NOT NULL
              AND abs(price - before) * 10000 >= $2::bigint * before
              AND abs(after - price) * 10000 >= $2::bigint * before
              AND sign(price - before) <> sign(after - price)
            ORDER BY receive_ts DESC LIMIT $3
            """,
            since,
            min_basis_points,
            limit,
        )
        out = []
        for r in rows:
            fills = await self._pool.fetchval(
                f"SELECT count(*) FROM {self._r}.account_events WHERE event->>'symbol' = $1 "
                f"AND (event->>'price_seq')::bigint = $2",
                r["symbol"],
                r["seq"],
            )
            out.append(
                {
                    "symbol": r["symbol"],
                    "price_seq": r["seq"],
                    "ts": r["receive_ts"],
                    "price": r["price"],
                    "before": r["before"],
                    "after": r["after"],
                    # the good price, proposed: the neighbours' midpoint
                    "suggested": (r["before"] + r["after"]) // 2,
                    "fills": int(fills),
                }
            )
        return out

    async def preview(
        self, symbol: str, price_seq: int, good: int
    ) -> tuple[PriceUpdate, list[Item]]:
        async with self._pool.acquire() as conn:
            update = await self._bad_price(conn, symbol, price_seq)
            accounts = [
                r["account_id"]
                for r in await conn.fetch(
                    f"SELECT DISTINCT account_id FROM {self._r}.account_events "
                    f"WHERE event->>'symbol' = $1 AND (event->>'price_seq')::bigint = $2",
                    symbol,
                    price_seq,
                )
            ]
            events: list[dict[str, Any]] = []
            for acc in accounts:
                rows = await conn.fetch(
                    f"SELECT seq, event::text AS event FROM {self._r}.account_events "
                    f"WHERE account_id = $1 ORDER BY seq, idx",
                    acc,
                )
                events += [{"seq": r["seq"], **json.loads(r["event"])} for r in rows]
        return update, plan(events, symbol, price_seq, update.price, good)

    async def propose(
        self, symbol: str, price_seq: int, good: int, reason: str, by: str, ts: int
    ) -> dict[str, Any]:
        update, items = await self.preview(symbol, price_seq, good)
        if good == update.price:
            raise CorrectionRefused("same_price", "the good price equals the applied one")

        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            number = await conn.fetchval(f"SELECT nextval('{s}.cor_seq')")
            cor = f"COR{number:08d}"
            try:
                await conn.execute(
                    f"INSERT INTO {s}.corrections (cor, symbol, price_seq, bad_price, good_price, "
                    f"price_ts, reason, items, status, proposed_by, proposed_at) "
                    f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, 'proposed', $9, $10)",
                    cor,
                    symbol,
                    price_seq,
                    update.price,
                    good,
                    update.receive_ts,
                    reason,
                    json.dumps([i.view() for i in items]),
                    by,
                    ts,
                )
            except asyncpg.UniqueViolationError as exc:
                raise CorrectionRefused("duplicate", "this price already has a correction") from exc
        found = await self.get(cor)
        assert found is not None
        return found

    async def get(self, cor: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            f"SELECT *, items::text AS items_text, outcome::text AS outcome_text "
            f"FROM {self._s}.corrections WHERE cor = $1",
            cor,
        )
        return None if row is None else _view(row)

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT *, items::text AS items_text, outcome::text AS outcome_text "
            f"FROM {self._s}.corrections ORDER BY cor DESC LIMIT $1",
            limit,
        )
        return [_view(r) for r in rows]

    async def reject(self, cor: str, by: str, ts: int) -> dict[str, Any]:
        await self._decide(cor, by, ts, "rejected", None)
        found = await self.get(cor)
        assert found is not None
        return found

    async def approve(self, cor: str, by: str, ts: int) -> dict[str, Any]:
        """A second person approves: the engine inputs go in, each outcome is
        recorded with the correction."""
        found = await self.get(cor)
        if found is None:
            raise CorrectionRefused("unknown", f"no correction {cor}")
        self._check_decider(found, by)
        items = [
            Item(i["account_id"], i["amount_cents"], i["reinstate"], i["fills"], tuple(i["manual"]))
            for i in found["items"]
        ]
        outcomes = []
        for item in inputs(cor, int(found["price_ts"]), items, ts):
            outcome: Outcome = await self._gateway.submit(item, self._wait_s)
            outcomes.append(
                {
                    "input": type(item).__name__,
                    "account_id": item.account_id,
                    "status": outcome_status(outcome),
                    "events": [e.get("type") for e in outcome.events],
                }
            )
        await self._decide(cor, by, ts, "applied", outcomes)
        done = await self.get(cor)
        assert done is not None
        return done

    @staticmethod
    def _check_decider(found: Mapping[str, Any], by: str) -> None:
        if found["status"] != "proposed":
            raise CorrectionRefused("decided", f"{found['cor']} is {found['status']}")
        if found["proposed_by"] == by:
            raise CorrectionRefused("same_person", "a second person approves")

    async def _decide(
        self, cor: str, by: str, ts: int, status: str, outcome: list[dict[str, Any]] | None
    ) -> None:
        found = await self.get(cor)
        if found is None:
            raise CorrectionRefused("unknown", f"no correction {cor}")
        self._check_decider(found, by)
        await self._pool.execute(
            f"UPDATE {self._s}.corrections SET status = $2, decided_by = $3, decided_at = $4, "
            f"outcome = $5::jsonb WHERE cor = $1 AND status = 'proposed'",
            cor,
            status,
            by,
            ts,
            None if outcome is None else json.dumps(outcome),
        )


def _view(row: Any) -> dict[str, Any]:
    keys = (
        "cor",
        "symbol",
        "price_seq",
        "bad_price",
        "good_price",
        "price_ts",
        "reason",
        "status",
        "proposed_by",
        "proposed_at",
        "decided_by",
        "decided_at",
    )
    out = {k: row[k] for k in keys}
    out["items"] = json.loads(row["items_text"])
    out["outcome"] = None if row["outcome_text"] is None else json.loads(row["outcome_text"])
    return out
