"""Erasure of a customer's personal data (rule AC27; D-15.5).

Records are never deleted (D-15.4). Hard deletion means one thing only: a
customer's personal data overwritten in place, so the records stay (the
ledger, orders, payouts, cases, the audit trail) but no longer name a
person. It is a decision for the two owners: one proposes, the other
approves, and the erasure runs at approval, in the same transaction.

It is refused while the data must still be kept:
- the relationship ended less than `[retention] aml_years` ago (D35: at
  least five years after the relationship ends, FATF R.11); the end is
  the latest activity on record (every dated customer record, and each
  account's last trading day);
- an account still holds capital (active, or passed and waiting);
- a payout is not settled, or a case is open (a case is the legal hold).
Both checks run at proposal and again at approval.

What is overwritten: name, email, date of birth and login subject; KYC
session ids, links, names and vendor detail; screening detail; sign-in
devices and places; payout wallet addresses; email copies sent; contract
texts (their hashes stay as proof); newsletter records of the address.
Country of residence and of documents stay: alone they name nobody, and
they keep the firm's statistics and tax records true.

Not covered here: the identity provider's login (Keycloak) is removed by
an owner by hand (the result lists it), and backups age out on their own
schedule.

The append-only guard (`append_only()`) lets an UPDATE through only inside
an erasure's own transaction, which names the erasure; DELETE stays
refused everywhere.
"""

import calendar
import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from propfirm.pgschema import check_schema

DAY_MS = 86_400_000
# an account holding capital: open, or passed and waiting for its next stage
HOLDING = ("active", "passed")
# payouts still moving money
OPEN_PAYOUTS = ("requested", "approved", "debited", "sent")
ERASED_NAME = "Erased"
ERASED_BIRTH = dt.date(1900, 1, 1)

# every dated record of a customer: (table, timestamp columns)
DATED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cif", ("created_at",)),
    ("cif_events", ("ts",)),
    ("orders", ("created_at", "paid_at", "provisioned_at")),
    ("accounts", ("created_at",)),
    ("payouts", ("requested_at", "debited_at", "paid_at")),
    ("progressions", ("created_at", "decided_at")),
    ("onboardings", ("created_at",)),
    ("kyc_cases", ("created_at",)),
    ("screenings", ("ts",)),
    ("cases", ("opened_at", "closed_at")),
    ("chargebacks", ("received_at", "recorded_at", "decided_at")),
    ("alerts", ("ts",)),
    ("notifications", ("created_at",)),
    ("sign_ins", ("at",)),
    ("step_ups", ("at",)),
    ("wallets", ("added_at",)),
    ("referrals", ("created_at",)),
)


class ErasureRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Accounts(Protocol):
    """The engine's live view of an account."""

    async def account(self, account_id: str) -> dict[str, Any] | None: ...


class Retention(Protocol):
    async def current(self) -> Any: ...


def retained_until(last_ms: int, years: int) -> int:
    """The first moment (ms UTC) after `years` full calendar years from
    `last_ms`. Rounded up: 29 February moves to 1 March, never to the 28th,
    so data is never released a day early."""
    t = dt.datetime.fromtimestamp(last_ms / 1000, tz=dt.UTC)
    year = t.year + years
    if t.month == 2 and t.day == 29 and not calendar.isleap(year):
        moved = t.replace(year=year, month=3, day=1)
    else:
        moved = t.replace(year=year)
    return int(moved.timestamp() * 1000)


@dataclass(frozen=True, slots=True)
class Erasure:
    id: int
    cif: str
    reason: str
    proposed_by: str
    proposed_at: int
    status: str  # proposed, approved (and run), rejected, cancelled
    decided_by: str | None
    decided_at: int | None
    decision_reason: str | None
    erased: dict[str, int] | None  # rows overwritten per table, once run


class Erasures:
    def __init__(
        self, pool: asyncpg.Pool, schema: str, settings: Retention, live: Accounts
    ) -> None:
        self._pool = pool
        self._s = check_schema(schema)
        self._settings = settings
        self._live = live

    async def blocks(self, conn: Any, cif: str, now: int) -> list[str]:
        """Why the customer's data must still be kept; empty: erasable."""
        s = self._s
        if await conn.fetchval(f"SELECT 1 FROM {s}.cif WHERE cif = $1", cif) is None:
            return ["no customer by that number"]
        if await conn.fetchval(
            f"SELECT 1 FROM {s}.erasure_runs r JOIN {s}.erasures e ON e.id = r.erasure "
            f"WHERE e.cif = $1",
            cif,
        ):
            return ["already erased"]
        found: list[str] = []
        last = 0
        for table, columns in DATED:
            biggest = ", ".join(f"max({c})" for c in columns)
            row = await conn.fetchrow(f"SELECT {biggest} FROM {s}.{table} WHERE cif = $1", cif)
            last = max([last, *(v for v in row.values() if v is not None)])
        for r in await conn.fetch(f"SELECT acc FROM {s}.accounts WHERE cif = $1", cif):
            view = await self._live.account(r["acc"])
            if view is None:
                continue
            if view["status"] in HOLDING:
                found.append(f"account {r['acc']} is {view['status']}")
            days = [*view.get("trading_day_ids", []), *(d for d, _ in view.get("day_pnl", []))]
            if days:
                last = max(last, max(days) + DAY_MS)
        if await conn.fetchval(
            f"SELECT 1 FROM {s}.payouts WHERE cif = $1 AND status = ANY($2::text[])",
            cif,
            list(OPEN_PAYOUTS),
        ):
            found.append("a payout is not settled")
        if await conn.fetchval(
            f"SELECT 1 FROM {s}.cases WHERE cif = $1 AND status <> 'closed'", cif
        ):
            found.append("a case is open (legal hold)")
        years = (await self._settings.current()).retention.aml_years
        until = retained_until(last, years)
        if now < until:
            found.append(f"kept until {until} ({years} years after the last activity)")
        return found

    async def propose(self, cif: str, by: str, reason: str, now: int) -> int:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"erasure:{cif}")
            if await conn.fetchval(
                f"SELECT 1 FROM {s}.erasures e WHERE e.cif = $1 AND NOT EXISTS "
                f"(SELECT 1 FROM {s}.erasure_decisions d WHERE d.erasure = e.id)",
                cif,
            ):
                raise ErasureRefused("pending", "an erasure of this customer is waiting")
            found = await self.blocks(conn, cif, now)
            if found:
                raise ErasureRefused("retained", "; ".join(found))
            eid: int = await conn.fetchval(
                f"INSERT INTO {s}.erasures (cif, reason, proposed_by, proposed_at) "
                f"VALUES ($1, $2, $3, $4) RETURNING id",
                cif,
                reason,
                by,
                now,
            )
            return eid

    async def decide(
        self, eid: int, kind: str, by: str, reason: str, now: int
    ) -> dict[str, int] | None:
        """approve (and run), reject, or cancel (its proposer only)."""
        if kind not in ("approved", "rejected", "cancelled"):
            raise ValueError("unknown decision")
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT cif, proposed_by FROM {s}.erasures WHERE id = $1 FOR UPDATE", eid
            )
            if row is None:
                raise ErasureRefused("unknown", "no erasure by that number")
            if await conn.fetchval(f"SELECT 1 FROM {s}.erasure_decisions WHERE erasure = $1", eid):
                raise ErasureRefused("decided", "the erasure is already decided")
            maker = str(row["proposed_by"])
            if kind == "cancelled" and by != maker:
                raise ErasureRefused("not_maker", "only its proposer cancels it")
            if kind != "cancelled" and by == maker:
                raise ErasureRefused("same_person", "the other owner decides an erasure")
            cif = str(row["cif"])
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"erasure:{cif}")
            if kind == "approved":
                found = await self.blocks(conn, cif, now)
                if found:
                    raise ErasureRefused("retained", "; ".join(found))
            await conn.execute(
                f"INSERT INTO {s}.erasure_decisions (erasure, kind, decided_by, decided_at, "
                f"reason) VALUES ($1, $2, $3, $4, $5)",
                eid,
                kind,
                by,
                now,
                reason,
            )
            if kind != "approved":
                return None
            erased = await self._run(conn, eid, cif)
            await conn.execute(
                f"INSERT INTO {s}.erasure_runs (erasure, ran_at, erased) VALUES ($1, $2, $3)",
                eid,
                now,
                json.dumps(erased, sort_keys=True),
            )
            return erased

    async def _run(self, conn: Any, eid: int, cif: str) -> dict[str, int]:
        """Overwrite the customer's personal data; rows touched per table."""
        s = self._s
        # names this erasure to the append-only guard, for this transaction only
        await conn.execute("SELECT set_config('propfirm.erasure', $1, true)", str(eid))
        email = await conn.fetchval(f"SELECT lower(email) FROM {s}.cif WHERE cif = $1", cif)
        marker = f"erased:{cif}"
        steps: tuple[tuple[str, str, tuple[Any, ...]], ...] = (
            (
                "cif",
                f"UPDATE {s}.cif SET email = $2, full_name = $3, date_of_birth = $4, "
                f"subject = $5, status = 'blocked' WHERE cif = $1",
                (f"{cif.lower()}@erased.invalid", ERASED_NAME, ERASED_BIRTH, marker),
            ),
            (
                "cif_events",
                f"UPDATE {s}.cif_events SET subject = $2, detail = '{{}}'::jsonb WHERE cif = $1",
                (marker,),
            ),
            (
                "kyc_cases",
                f"UPDATE {s}.kyc_cases SET session_id = 'erased:' || kyc, url = '' WHERE cif = $1",
                (),
            ),
            (
                "kyc_results",
                f"UPDATE {s}.kyc_results r SET full_name = $2, date_of_birth = $3, "
                f"detail = '{{}}'::jsonb FROM {s}.kyc_cases k WHERE k.kyc = r.kyc AND k.cif = $1",
                (ERASED_NAME, ERASED_BIRTH),
            ),
            (
                "screenings",
                f"UPDATE {s}.screenings SET detail = '{{}}'::jsonb WHERE cif = $1",
                (),
            ),
            (
                "sign_ins",
                f"UPDATE {s}.sign_ins t SET device = 'erased:' || n.k, country = NULL, "
                f"lat = NULL, lon = NULL FROM (SELECT at, device, row_number() OVER "
                f"(ORDER BY at, device) AS k FROM {s}.sign_ins WHERE cif = $1) n "
                f"WHERE t.cif = $1 AND t.at = n.at AND t.device = n.device",
                (),
            ),
            (
                "wallets",
                f"UPDATE {s}.wallets SET address = 'erased', detail = '{{}}'::jsonb WHERE cif = $1",
                (),
            ),
            (
                "notifications",
                f"UPDATE {s}.notifications SET recipient = '', subject = '', body = '', "
                f"detail = '{{}}'::jsonb WHERE cif = $1",
                (),
            ),
            (
                "contracts",
                f"UPDATE {s}.contracts c SET body = '' FROM {s}.onboardings o "
                f"WHERE o.onb = c.onb AND o.cif = $1",
                (),
            ),
        )
        erased: dict[str, int] = {}
        for table, sql, args in steps:
            erased[table] = _count(await conn.execute(sql, cif, *args))
        if email:
            for table in ("subscriber_events", "campaign_audience", "campaign_results"):
                done = await conn.execute(
                    f"UPDATE {s}.{table} SET email = $2 WHERE lower(email) = $1", email, marker
                )
                erased[table] = _count(done)
        erased["identity_login"] = 0  # the identity provider's login: removed by hand
        return erased

    async def erasures(self) -> list[Erasure]:
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT e.*, d.kind, d.decided_by, d.decided_at, d.reason AS decision_reason, "
            f"r.erased FROM {s}.erasures e "
            f"LEFT JOIN {s}.erasure_decisions d ON d.erasure = e.id "
            f"LEFT JOIN {s}.erasure_runs r ON r.erasure = e.id ORDER BY e.id DESC"
        )
        return [
            Erasure(
                id=r["id"],
                cif=r["cif"],
                reason=r["reason"],
                proposed_by=r["proposed_by"],
                proposed_at=r["proposed_at"],
                status=r["kind"] or "proposed",
                decided_by=r["decided_by"],
                decided_at=r["decided_at"],
                decision_reason=r["decision_reason"],
                erased=None if r["erased"] is None else json.loads(r["erased"]),
            )
            for r in rows
        ]


def _count(status: str) -> int:
    """Rows touched, from a command tag such as 'UPDATE 3'."""
    return int(status.rsplit(" ", 1)[-1])
