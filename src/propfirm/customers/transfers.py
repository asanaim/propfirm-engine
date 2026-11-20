"""Payouts sent by hand (build plan step 4, line 4; D-14.25).

The `manual` payout rail sends nothing itself: after the engine has
debited the account, a member of staff sends the money from the firm's
own wallet or exchange account and records the transaction id. The
payout is paid when the transfer is recorded. The payout was already
approved under the payout policy; recording what was sent is one act,
kept append-only with who did it.
"""

from collections.abc import Mapping
from typing import Any, Protocol

import asyncpg

from propfirm.customers.providers import (
    PayoutOrder,
    PayoutProvider,
    PayoutStatus,
    SandboxPayouts,
)


class TransferRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ManualPayouts:
    """The payout rail of a `manual` withdrawal method."""

    adapter = "manual"

    def __init__(self, pool: asyncpg.Pool, schema: str, name: str) -> None:
        self._pool = pool
        self._s = schema
        self.name = name

    async def send(self, order: PayoutOrder) -> str:
        return f"{self.name}-{order.payout}"  # the same every time: idempotent

    async def status(self, ref: str) -> PayoutStatus:
        po = ref.removeprefix(f"{self.name}-")
        row = await self._pool.fetchrow(
            f"SELECT tx_ref, recorded_by, recorded_at FROM {self._s}.payout_transfers "
            f"WHERE po = $1",
            po,
        )
        if row is None:
            return PayoutStatus("pending", {})
        return PayoutStatus("paid", {"tx": row["tx_ref"], "recorded_by": row["recorded_by"]})


class Transfers:
    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._s = schema

    async def waiting(self, limit: int = 200) -> list[dict[str, Any]]:
        """Payouts sent through a manual method with no transfer recorded."""
        rows = await self._pool.fetch(
            f"SELECT p.po, p.acc, p.cif, p.method_id, p.trader_amount_cents, p.charge_cents, "
            f"p.currency, p.provider_ref, w.network, w.address FROM {self._s}.payouts p "
            f"JOIN {self._s}.wallets w ON w.wal = p.wal WHERE p.status = 'sent' "
            f"AND p.provider_ref = p.method_id || '-' || p.po AND NOT EXISTS "
            f"(SELECT 1 FROM {self._s}.payout_transfers t WHERE t.po = p.po) "
            f"ORDER BY p.po LIMIT $1",
            limit,
        )
        return [
            {**dict(r), "net_cents": int(r["trader_amount_cents"]) - int(r["charge_cents"])}
            for r in rows
        ]

    async def record(self, po: str, tx_ref: str, by: str, now: int) -> dict[str, Any]:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT po, status, method_id, provider_ref FROM {s}.payouts "
                f"WHERE po = $1 FOR UPDATE",
                po,
            )
            if row is None:
                raise TransferRefused("unknown", f"no payout {po}")
            manual = row["method_id"] is not None and row["provider_ref"] == (
                f"{row['method_id']}-{po}"
            )
            if row["status"] != "sent" or not manual:
                raise TransferRefused(
                    "not_waiting", "this payout is not waiting to be sent by hand"
                )
            if await conn.fetchval(f"SELECT 1 FROM {s}.payout_transfers WHERE po = $1", po):
                raise TransferRefused("recorded", "a transfer is recorded already")
            await conn.execute(
                f"INSERT INTO {s}.payout_transfers (po, tx_ref, recorded_by, recorded_at) "
                f"VALUES ($1, $2, $3, $4)",
                po,
                tx_ref,
                by,
                now,
            )
        return {"po": po, "tx_ref": tx_ref, "recorded_by": by, "recorded_at": now}


class NoPayoutRail:
    """Production with no withdrawal method on: nothing sends, so a payout
    request is refused until an owner attaches one (D-14.25)."""

    name = "none"

    async def send(self, order: PayoutOrder) -> str:
        raise WithdrawalUnavailable("no withdrawal method is on")

    async def status(self, ref: str) -> PayoutStatus:
        return PayoutStatus("pending", {})


class WithdrawalUnavailable(Exception):
    """No rail can send through this method now."""


class MethodSource(Protocol):
    async def methods(self) -> Mapping[str, Any]: ...


class WithdrawalRails:
    """The payout rail of each withdrawal method (D-14.25): `manual` (sent
    by hand, recorded with its transaction) and `sandbox` (development).
    Automatic NOWPayments payouts are not offered: their payout API needs a
    second-factor code for every batch, which is a person's act, so at
    launch staff send by hand from the NOWPayments dashboard or a wallet."""

    def __init__(
        self, methods: MethodSource, pool: asyncpg.Pool, schema: str, *, sandbox: bool
    ) -> None:
        self._methods = methods
        self._pool = pool
        self._s = schema
        self._sandbox = sandbox

    async def enabled(self) -> list[Any]:
        """Withdrawal methods traders may choose now."""
        return [
            m
            for m in (await self._methods.methods()).values()
            if m.kind == "withdrawal" and m.enabled
        ]

    async def method(self, method_id: str) -> Any:
        method = (await self._methods.methods()).get(method_id)
        if method is None or method.kind != "withdrawal":
            raise WithdrawalUnavailable(f"no withdrawal method {method_id}")
        return method

    async def for_method(self, method_id: str) -> PayoutProvider:
        method = await self.method(method_id)
        if method.adapter == "manual":
            return ManualPayouts(self._pool, self._s, method_id)
        if method.adapter == "sandbox" and self._sandbox:
            return SandboxPayouts()
        raise WithdrawalUnavailable(f"{method_id}: no payout rail for {method.adapter}")
