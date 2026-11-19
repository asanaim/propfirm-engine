"""Referral ledger (D-14.15).

- Each trader has one code (8 letters and digits), made on first request.
- A new customer may enter a code once, at registration; the attribution
  never changes. Refused: an unknown code, the customer's own, a referrer
  not in good standing, and anyone resident in a refused country (C8).
- Commission: `commission` of the price the referred customer paid for
  their first paid account (not a free trial; repeat accounts earn none,
  CONFIRMED), rounded down to the cent, accrued once per referred customer
  and available after the holdback. The ledger is append-only: a
  clawback is a new negative entry linked to the same order.
- Paying commission out is not built here; it comes with the affiliate
  programme (KYC or KYB first, M27).
"""

import secrets
from dataclasses import dataclass
from typing import Any

import asyncpg

from propfirm.policy import PolicySource

DAY_MS = 86_400_000
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I


class ReferralRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Balance:
    accrued_cents: int  # every entry
    available_cents: int  # entries past their holdback
    referred: int  # customers attributed


class Referrals:
    def __init__(self, pool: asyncpg.Pool, schema: str, policy: PolicySource) -> None:
        self._pool = pool
        self._s = schema
        self._policy = policy

    # --- codes and attribution -------------------------------------------------------------

    async def code_for(self, cif: str, now: int) -> str:
        """The trader's referral code, made on first request."""
        s = self._s
        found = await self._pool.fetchval(
            f"SELECT code FROM {s}.referral_codes WHERE cif = $1", cif
        )
        if found is not None:
            return str(found)
        for _ in range(5):  # a clash among 32**8 codes is rare; retry
            code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
            made = await self._pool.fetchval(
                f"INSERT INTO {s}.referral_codes (cif, code, created_at) VALUES ($1, $2, $3) "
                f"ON CONFLICT DO NOTHING RETURNING code",
                cif,
                code,
                now,
            )
            if made is not None:
                return str(made)
            again = await self._pool.fetchval(
                f"SELECT code FROM {s}.referral_codes WHERE cif = $1", cif
            )
            if again is not None:  # made by a concurrent request
                return str(again)
        raise RuntimeError("no free referral code")

    async def attribute(self, cif: str, code: str, now: int) -> str:
        """Record that `code`'s owner referred `cif` (once); return the
        referrer's CIF. ReferralRefused otherwise."""
        policy = (await self._policy.current()).referrals
        if not policy.on:
            raise ReferralRefused("off", "referrals are off")
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            referrer = await conn.fetchrow(
                f"SELECT c.cif, c.status, c.residence_country FROM {s}.referral_codes r "
                f"JOIN {s}.cif c ON c.cif = r.cif WHERE r.code = $1",
                code.upper(),
            )
            if referrer is None:
                raise ReferralRefused("unknown_code", "this referral code is not valid")
            if referrer["cif"] == cif:
                raise ReferralRefused("own_code", "a trader cannot refer themselves")
            if referrer["status"] != "registered":
                raise ReferralRefused("unknown_code", "this referral code is not valid")
            referred = await conn.fetchrow(
                f"SELECT residence_country FROM {s}.cif WHERE cif = $1", cif
            )
            countries = policy.refused_countries
            if (
                referred is None
                or {
                    referrer["residence_country"],
                    referred["residence_country"],
                }
                & countries
            ):
                raise ReferralRefused("country", "referrals are not offered in this country")
            done = await conn.fetchval(
                f"INSERT INTO {s}.referrals (cif, referrer_cif, code, created_at) "
                f"VALUES ($1, $2, $3, $4) ON CONFLICT (cif) DO NOTHING RETURNING referrer_cif",
                cif,
                referrer["cif"],
                code.upper(),
                now,
            )
            if done is None:
                raise ReferralRefused("already_referred", "this customer was referred already")
            return str(done)

    # --- the ledger ------------------------------------------------------------------------

    async def accrue(self, now: int) -> int:
        """Accrue commission on each referred customer's first paid account
        (provisioned, not a free trial) not yet in the ledger; return how
        many entries were made. Idempotent: one accrual per order."""
        policy = (await self._policy.current()).referrals
        if not policy.on:
            return 0
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT DISTINCT ON (o.cif) o.ord, o.cif, o.price_cents, o.paid_at, r.referrer_cif "
            f"FROM {s}.orders o JOIN {s}.referrals r ON r.cif = o.cif "
            f"WHERE o.status IN ('paid', 'provisioned') AND o.rsv_id NOT LIKE '%-trial' "
            f"ORDER BY o.cif, o.created_at, o.ord"
        )
        made = 0
        share = policy.commission
        for row in rows:
            amount = row["price_cents"] * share.numerator // share.denominator
            if amount <= 0:
                continue  # nothing paid: no commission
            paid_at = row["paid_at"] if row["paid_at"] is not None else now
            done = await self._pool.fetchval(
                f"INSERT INTO {s}.referral_entries (referrer_cif, referred_cif, ord, kind, "
                f"amount_cents, available_at, ts) VALUES ($1, $2, $3, 'accrued', $4, $5, $6) "
                f"ON CONFLICT (ord, kind) DO NOTHING RETURNING id",
                row["referrer_cif"],
                row["cif"],
                row["ord"],
                amount,
                paid_at + policy.holdback_days * DAY_MS,
                now,
            )
            made += done is not None
        return made

    async def claw_back(self, ord_id: str, by: str, reason: str, now: int) -> dict[str, Any]:
        """Take back the commission on `ord_id` (refund, fraud): a new
        negative entry. ReferralRefused if there is none or it was taken."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            accrued = await conn.fetchrow(
                f"SELECT * FROM {s}.referral_entries WHERE ord = $1 AND kind = 'accrued' "
                f"FOR UPDATE",
                ord_id,
            )
            if accrued is None:
                raise ReferralRefused("unknown", "no commission on this order")
            row = await conn.fetchrow(
                f"INSERT INTO {s}.referral_entries (referrer_cif, referred_cif, ord, kind, "
                f"amount_cents, available_at, by_login, reason, ts) "
                f"VALUES ($1, $2, $3, 'clawback', $4, $5, $6, $7, $8) "
                f"ON CONFLICT (ord, kind) DO NOTHING RETURNING *",
                accrued["referrer_cif"],
                accrued["referred_cif"],
                ord_id,
                -accrued["amount_cents"],
                now,
                by,
                reason,
                now,
            )
            if row is None:
                raise ReferralRefused("taken", "the commission was taken back already")
            return dict(row)

    async def balance(self, cif: str, now: int) -> Balance:
        s = self._s
        row = await self._pool.fetchrow(
            f"SELECT coalesce(sum(amount_cents), 0) AS accrued, "
            f"coalesce(sum(amount_cents) FILTER (WHERE available_at <= $2), 0) AS available "
            f"FROM {s}.referral_entries WHERE referrer_cif = $1",
            cif,
            now,
        )
        referred = await self._pool.fetchval(
            f"SELECT count(*) FROM {s}.referrals WHERE referrer_cif = $1", cif
        )
        assert row is not None
        return Balance(int(row["accrued"]), int(row["available"]), int(referred))

    async def entries(self, cif: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Ledger entries, newest first; of one referrer with `cif`."""
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT id, referrer_cif, referred_cif, ord, kind, amount_cents, available_at, "
            f"by_login, reason, ts FROM {s}.referral_entries "
            f"WHERE $1::text IS NULL OR referrer_cif = $1 ORDER BY id DESC LIMIT $2",
            cif,
            limit,
        )
        return [dict(r) for r in rows]
