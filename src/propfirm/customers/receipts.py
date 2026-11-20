"""Receipts of manual payments (build plan step 4, line 3; D-14.24).

An order placed with a manual method (a bank transfer, say) stays pending
until the money is seen. One member of staff with the settings change
right records the receipt: the amount and the bank's reference. A
different one confirms it, and only then is the order paid and its
account opened, exactly as when a provider reports a payment. The amount
must be the order's price to the cent; anything else (a part payment, the
wrong amount) is settled with the customer outside and recorded again.
"""

from typing import Any, Protocol

import asyncpg

from propfirm.customers.rails import RailUnavailable
from propfirm.customers.store import OrderRecord


class ReceiptRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Paying(Protocol):
    async def payment_received(self, provider: str, provider_ref: str, ts: int) -> OrderRecord: ...


class Adapters(Protocol):
    async def method(self, method_id: str) -> Any: ...


class Receipts:
    def __init__(
        self, pool: asyncpg.Pool, schema: str, checkout: Paying, methods: Adapters
    ) -> None:
        self._pool = pool
        self._s = schema
        self._checkout = checkout
        self._methods = methods

    async def receipts(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT r.*, o.cif, o.price_cents, o.provider, o.status AS order_status "
            f"FROM {self._s}.receipts r JOIN {self._s}.orders o ON o.ord = r.ord "
            f"ORDER BY r.recorded_at DESC, r.rcp DESC LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]

    async def awaiting(self, limit: int = 200) -> list[dict[str, Any]]:
        """Pending orders of manual methods with no receipt recorded."""
        rows = await self._pool.fetch(
            f"SELECT o.ord, o.cif, o.provider, o.price_cents, o.created_at FROM {self._s}.orders o "
            f"WHERE o.status = 'pending' AND o.price_cents > 0 AND NOT EXISTS (SELECT 1 FROM "
            f"{self._s}.receipts r WHERE r.ord = o.ord AND r.status IN ('recorded', 'confirmed')) "
            f"ORDER BY o.created_at, o.ord LIMIT $1",
            limit * 5,
        )
        out = []
        for r in rows:
            if await self._adapter(str(r["provider"])) == "manual":
                out.append(dict(r))
            if len(out) >= limit:
                break
        return out

    async def _adapter(self, method_id: str) -> str | None:
        """The rail a method names; None for no method (the sandbox's
        default orders, a method no longer attached)."""
        try:
            return str((await self._methods.method(method_id)).adapter)
        except RailUnavailable:
            return None

    async def record(
        self,
        ord_id: str,
        amount_cents: int,
        reference: str,
        received_at: int,
        by: str,
        now: int,
    ) -> dict[str, Any]:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            order = await conn.fetchrow(
                f"SELECT ord, provider, price_cents, status FROM {s}.orders "
                f"WHERE ord = $1 FOR UPDATE",
                ord_id,
            )
            if order is None:
                raise ReceiptRefused("unknown", f"no order {ord_id}")
            if await self._adapter(str(order["provider"])) != "manual":
                raise ReceiptRefused("not_manual", "this order is not paid by hand")
            if order["status"] != "pending":
                raise ReceiptRefused("not_pending", f"the order is {order['status']}")
            if amount_cents != order["price_cents"]:
                raise ReceiptRefused(
                    "amount_differs",
                    f"the order is {order['price_cents']} cents, not {amount_cents}",
                )
            if await conn.fetchval(
                f"SELECT 1 FROM {s}.receipts WHERE ord = $1 "
                f"AND status IN ('recorded', 'confirmed')",
                ord_id,
            ):
                raise ReceiptRefused("recorded", "a receipt for this order is recorded already")
            number = await conn.fetchval(f"SELECT nextval('{s}.rcp_seq')")
            row = await conn.fetchrow(
                f"INSERT INTO {s}.receipts (rcp, ord, amount_cents, reference, received_at, "
                f"recorded_by, recorded_at, status) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7, 'recorded') RETURNING *",
                f"RCP{int(number):08d}",
                ord_id,
                amount_cents,
                reference,
                received_at,
                by,
                now,
            )
        assert row is not None
        return dict(row)

    async def decide(
        self, rcp: str, confirm: bool, reason: str, by: str, now: int
    ) -> dict[str, Any]:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT r.*, o.provider, o.provider_ref FROM {s}.receipts r "
                f"JOIN {s}.orders o ON o.ord = r.ord WHERE r.rcp = $1 FOR UPDATE OF r",
                rcp,
            )
            if row is None:
                raise ReceiptRefused("unknown", f"no receipt {rcp}")
            if row["status"] != "recorded":
                raise ReceiptRefused("decided", "this receipt was decided already")
            if row["recorded_by"] == by:
                raise ReceiptRefused("same_person", "a receipt is confirmed by someone else")
            await conn.execute(
                f"UPDATE {s}.receipts SET status = $2, decided_by = $3, decided_at = $4, "
                f"decision_reason = $5 WHERE rcp = $1",
                rcp,
                "confirmed" if confirm else "rejected",
                by,
                now,
                reason,
            )
        if confirm:  # paid as a provider's notice would: the account opens
            await self._checkout.payment_received(row["provider"], row["provider_ref"], now)
        found = await self._pool.fetchrow(f"SELECT * FROM {s}.receipts WHERE rcp = $1", rcp)
        assert found is not None
        return dict(found)
