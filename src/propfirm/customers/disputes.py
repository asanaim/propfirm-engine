"""Refunds and chargebacks (build plan step 3, line 7; D-14.22).

Refund: money the firm returns on a paid order. One member of staff with
the settings change right proposes it (an amount and a reason), a
different one approves or rejects it. On approval the books post it
(`refund:<RFD>`), and when the order is refunded in full its account, if
still active, ends (the engine closes its positions and locks it,
`ExpireAccount`: not a breach) and any referral commission on the order
is taken back. A part refund (goodwill, an add-on) leaves the account
alone.

Chargeback: the customer's bank took a payment back through the
processor. Staff record it until the payment adapters report disputes
(step 4). Recording it, at once and without a second person, because a
restriction never waits:
- raises a high-severity fraud alert on the customer's case, which holds
  every payout, funded review and contract while the case is open;
- ends the order's account if active (positions closed, locked);
- takes back any referral commission on the order;
- refuses new purchases by the customer while it is open or lost.
A different member of staff records the outcome: won (the money came
back; purchases allowed again) or lost. The case is decided on its own
four-eyes path; lifting a case is never automatic.

What may be returned is bounded: approved and proposed refunds plus open
and lost chargebacks never exceed the price paid. A refund is refused
while a chargeback on the order is open (the money is with the bank).
"""

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from propfirm.journal.inputs import ExpireAccount
from propfirm.pgschema import Conn
from propfirm.service.gateway import Outcome

log = logging.getLogger(__name__)

PAID = ("paid", "provisioned")


class DisputeRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Gateway(Protocol):
    async def publish(self, item: ExpireAccount) -> str: ...

    async def outcome(self, entry_id: str, wait_s: float) -> Outcome: ...


class LiveAccounts(Protocol):
    async def account(self, account_id: str) -> dict[str, Any] | None: ...


class Alerts(Protocol):
    async def alert(
        self,
        cif: str,
        acc: str | None,
        source: str,
        kind: str,
        severity: str,
        detail: dict[str, Any],
        ts: int,
    ) -> tuple[str, str]: ...


class Clawbacks(Protocol):
    async def claw_back(self, ord_id: str, by: str, reason: str, now: int) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class Room:
    price_cents: int
    refunded_cents: int  # approved
    proposed_cents: int  # proposed, not decided
    charged_back_cents: int  # open or lost

    @property
    def left_cents(self) -> int:
        """What may still be returned on the order."""
        return (
            self.price_cents - self.refunded_cents - self.proposed_cents - self.charged_back_cents
        )


class Disputes:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        gateway: Gateway | None = None,
        live: LiveAccounts | None = None,
        alerts: Alerts | None = None,
        clawbacks: Clawbacks | None = None,
        wait_s: float = 5.0,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._gateway = gateway
        self._live = live
        self._alerts = alerts
        self._clawbacks = clawbacks
        self._wait_s = wait_s

    # --- reading ---------------------------------------------------------------------

    async def _paid_order(self, conn: Conn, ord_id: str) -> asyncpg.Record:
        s = self._s
        order = await conn.fetchrow(
            f"SELECT ord, cif, acc, product_id, price_cents, status FROM {s}.orders "
            f"WHERE ord = $1 FOR UPDATE",
            ord_id,
        )
        if order is None:
            raise DisputeRefused("unknown", f"no order {ord_id}")
        if order["status"] not in PAID or order["price_cents"] <= 0:
            raise DisputeRefused("not_paid", "only a paid order can be refunded or charged back")
        return order

    async def room(self, conn: Conn, ord_id: str, price_cents: int) -> Room:
        s = self._s
        row = await conn.fetchrow(
            f"SELECT "
            f"(SELECT coalesce(sum(amount_cents), 0) FROM {s}.refunds "
            f"WHERE ord = $1 AND status = 'approved') AS refunded, "
            f"(SELECT coalesce(sum(amount_cents), 0) FROM {s}.refunds "
            f"WHERE ord = $1 AND status = 'proposed') AS proposed, "
            f"(SELECT coalesce(sum(amount_cents), 0) FROM {s}.chargebacks "
            f"WHERE ord = $1 AND status IN ('open', 'lost')) AS charged",
            ord_id,
        )
        assert row is not None
        return Room(price_cents, int(row["refunded"]), int(row["proposed"]), int(row["charged"]))

    async def blocks_purchase(self, cif: str) -> bool:
        """Whether the customer has a chargeback open or lost."""
        found = await self._pool.fetchval(
            f"SELECT 1 FROM {self._s}.chargebacks WHERE cif = $1 "
            f"AND status IN ('open', 'lost') LIMIT 1",
            cif,
        )
        return found is not None

    async def refunds(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT r.*, o.cif, o.product_id, o.price_cents FROM {self._s}.refunds r "
            f"JOIN {self._s}.orders o ON o.ord = r.ord ORDER BY r.proposed_at DESC, r.rfd DESC "
            f"LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]

    async def chargebacks(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT c.*, o.product_id, o.price_cents FROM {self._s}.chargebacks c "
            f"JOIN {self._s}.orders o ON o.ord = c.ord ORDER BY c.recorded_at DESC, c.cbk DESC "
            f"LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]

    # --- refunds, two people ---------------------------------------------------------

    async def propose_refund(
        self, ord_id: str, amount_cents: int, reason: str, by: str, now: int
    ) -> dict[str, Any]:
        if amount_cents <= 0:
            raise DisputeRefused("invalid", "a refund is more than 0")
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            order = await self._paid_order(conn, ord_id)
            if await conn.fetchval(
                f"SELECT 1 FROM {s}.chargebacks WHERE ord = $1 AND status = 'open'", ord_id
            ):
                raise DisputeRefused("disputed", "a chargeback on this order is open")
            room = await self.room(conn, ord_id, int(order["price_cents"]))
            if amount_cents > room.left_cents:
                raise DisputeRefused(
                    "too_much", f"at most {room.left_cents} cents may still be returned"
                )
            number = await conn.fetchval(f"SELECT nextval('{s}.rfd_seq')")
            row = await conn.fetchrow(
                f"INSERT INTO {s}.refunds (rfd, ord, amount_cents, reason, proposed_by, "
                f"proposed_at, status) VALUES ($1, $2, $3, $4, $5, $6, 'proposed') RETURNING *",
                f"RFD{int(number):08d}",
                ord_id,
                amount_cents,
                reason,
                by,
                now,
            )
        assert row is not None
        return dict(row)

    async def decide_refund(
        self, rfd: str, approve: bool, reason: str, by: str, now: int
    ) -> dict[str, Any]:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {s}.refunds WHERE rfd = $1 FOR UPDATE", rfd)
            if row is None:
                raise DisputeRefused("unknown", f"no refund {rfd}")
            if row["status"] != "proposed":
                raise DisputeRefused("decided", "this refund was decided already")
            if row["proposed_by"] == by:
                raise DisputeRefused("same_person", "a refund is decided by someone else")
            order = await self._paid_order(conn, row["ord"])
            await conn.execute(
                f"UPDATE {s}.refunds SET status = $2, decided_by = $3, decided_at = $4, "
                f"decision_reason = $5 WHERE rfd = $1",
                rfd,
                "approved" if approve else "rejected",
                by,
                now,
                reason,
            )
            room = await self.room(conn, row["ord"], int(order["price_cents"]))
        full = approve and room.refunded_cents >= room.price_cents
        ended = None
        if full:
            ended = await self._end(order["acc"], now)
            await self._claw_back(row["ord"], by, f"refunded ({rfd})", now)
            if ended is not None:
                await self._pool.execute(
                    f"UPDATE {s}.refunds SET ended_acc = $2 WHERE rfd = $1", rfd, ended
                )
        found = await self._pool.fetchrow(f"SELECT * FROM {s}.refunds WHERE rfd = $1", rfd)
        assert found is not None
        return dict(found)

    # --- chargebacks -----------------------------------------------------------------

    async def record_chargeback(
        self,
        ord_id: str,
        amount_cents: int,
        fee_cents: int,
        provider_case: str,
        reason: str,
        received_at: int,
        by: str,
        now: int,
    ) -> dict[str, Any]:
        if amount_cents <= 0 or fee_cents < 0:
            raise DisputeRefused("invalid", "a chargeback is more than 0; its fee 0 or more")
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            order = await self._paid_order(conn, ord_id)
            room = await self.room(conn, ord_id, int(order["price_cents"]))
            # a chargeback is not ours to refuse: only what is left after
            # approved refunds and other chargebacks bounds it
            if amount_cents > room.price_cents - room.refunded_cents - room.charged_back_cents:
                raise DisputeRefused("too_much", "more than the order has left to take back")
            number = await conn.fetchval(f"SELECT nextval('{s}.cbk_seq')")
            cbk = f"CBK{int(number):08d}"
            await conn.execute(
                f"INSERT INTO {s}.chargebacks (cbk, ord, cif, amount_cents, fee_cents, "
                f"provider_case, reason, received_at, recorded_by, recorded_at, status) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'open')",
                cbk,
                ord_id,
                order["cif"],
                amount_cents,
                fee_cents,
                provider_case,
                reason,
                received_at,
                by,
                now,
            )
        cas = None
        if self._alerts is not None:
            cas, _ = await self._alerts.alert(
                order["cif"],
                order["acc"],
                "chargeback",
                "fraud",
                "high",
                {"key": cbk, "cbk": cbk, "ord": ord_id, "amount_cents": amount_cents},
                now,
            )
        ended = await self._end(order["acc"], now)
        await self._claw_back(ord_id, by, f"charged back ({cbk})", now)
        await self._pool.execute(
            f"UPDATE {s}.chargebacks SET cas = $2, ended_acc = $3 WHERE cbk = $1", cbk, cas, ended
        )
        found = await self._pool.fetchrow(f"SELECT * FROM {s}.chargebacks WHERE cbk = $1", cbk)
        assert found is not None
        return dict(found)

    async def decide_chargeback(
        self, cbk: str, outcome: str, reason: str, by: str, now: int
    ) -> dict[str, Any]:
        if outcome not in ("won", "lost"):
            raise DisputeRefused("invalid", "the outcome is won or lost")
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT * FROM {s}.chargebacks WHERE cbk = $1 FOR UPDATE", cbk
            )
            if row is None:
                raise DisputeRefused("unknown", f"no chargeback {cbk}")
            if row["status"] != "open":
                raise DisputeRefused("decided", "this chargeback was decided already")
            if row["recorded_by"] == by:
                raise DisputeRefused("same_person", "the outcome is recorded by someone else")
            found = await conn.fetchrow(
                f"UPDATE {s}.chargebacks SET status = $2, decided_by = $3, decided_at = $4, "
                f"decision_reason = $5 WHERE cbk = $1 RETURNING *",
                cbk,
                outcome,
                by,
                now,
                reason,
            )
        assert found is not None
        return dict(found)

    # --- effects ---------------------------------------------------------------------

    async def _end(self, acc: str | None, now: int) -> str | None:
        """End the account if it is active; its id when the engine did."""
        if acc is None or self._gateway is None or self._live is None:
            return None
        view = await self._live.account(acc)
        if view is None or view.get("status") != "active":
            return None
        entry = await self._gateway.publish(ExpireAccount(acc, now))
        outcome = await self._gateway.outcome(entry, self._wait_s)
        if any(e.get("type") == "AccountExpired" for e in outcome.events):
            return acc
        log.warning("disputes: %s not ended: %s", acc, outcome.events)
        return None

    async def _claw_back(self, ord_id: str, by: str, reason: str, now: int) -> None:
        if self._clawbacks is None:
            return
        try:
            await self._clawbacks.claw_back(ord_id, by, reason, now)
        except Exception as exc:  # none accrued, or taken already: nothing to do
            if getattr(exc, "code", None) not in ("unknown", "taken"):
                raise
