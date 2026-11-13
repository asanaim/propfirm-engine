"""The trader directory for staff (rule AC19, 12 Nov 2026): traders listed by
status, and one trader's whole file.

Read only: every figure comes from the customer records the engine and the
API already keep (registrations, orders, accounts, identity checks, cases,
payouts, the email outbox). Segments are computed, never stored, so they
cannot drift from the records.

Segments, as the spec's list maps onto what pipridge records:
  all           every registered trader
  trading       has at least one account
  funded        has a funded account
  no_account    registered, nothing bought yet
  kyc_pending   an identity check in progress (verification, checks, review)
  kyc_declined  the latest identity check declined or the onboarding rejected
  open_case     a case not closed
  blocked       the registration is blocked
The spec's "email unverified" and "mobile unverified" have no records here:
Keycloak verifies email at sign-up, and no phone number is collected. Login
history is kept by Keycloak, not here; support tickets do not exist yet (AC21).
"""

import datetime as dt
from typing import Any

import asyncpg

from propfirm.pgschema import check_schema

SEGMENTS = (
    "all",
    "trading",
    "funded",
    "no_account",
    "kyc_pending",
    "kyc_declined",
    "open_case",
    "blocked",
)

# one row per trader with what the segments need
_SUMMARY = """
WITH acc AS (
    SELECT cif, count(*) AS accounts, count(*) FILTER (WHERE stage = 'funded') AS funded
    FROM {s}.accounts GROUP BY cif
), onb AS (
    SELECT DISTINCT ON (cif) cif, status AS onboarding
    FROM {s}.onboardings ORDER BY cif, created_at DESC, onb DESC
), cas AS (
    SELECT cif, cas FROM {s}.cases WHERE status <> 'closed'
), po AS (
    -- sum() of bigint is numeric: cast back, or the API sends it as text
    SELECT cif, coalesce(sum(trader_amount_cents) FILTER (WHERE status = 'paid'), 0)::bigint
           AS paid_cents
    FROM {s}.payouts GROUP BY cif
)
SELECT c.cif, c.full_name, c.email, c.residence_country, c.status, c.created_at,
       coalesce(acc.accounts, 0) AS accounts, coalesce(acc.funded, 0) AS funded,
       onb.onboarding, cas.cas AS open_case, coalesce(po.paid_cents, 0) AS paid_cents
FROM {s}.cif c
LEFT JOIN acc USING (cif) LEFT JOIN onb USING (cif) LEFT JOIN cas USING (cif)
LEFT JOIN po USING (cif)
"""

_WHERE = {
    "all": "TRUE",
    "trading": "accounts > 0",
    "funded": "funded > 0",
    "no_account": "accounts = 0",
    "kyc_pending": "onboarding IN ('verification', 'checks', 'review')",
    "kyc_declined": "onboarding IN ('verification_declined', 'rejected')",
    "open_case": "open_case IS NOT NULL",
    "blocked": "status = 'blocked'",
}


def _row(r: asyncpg.Record) -> dict[str, Any]:
    return dict(r)


class TraderDirectory:
    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool, self._s = pool, check_schema(schema)
        self._summary = _SUMMARY.format(s=self._s)

    async def counts(self) -> dict[str, int]:
        """How many traders each segment holds."""
        parts = ", ".join(
            "count(*) FILTER (WHERE " + _WHERE[name] + ") AS " + name for name in SEGMENTS
        )
        row = await self._pool.fetchrow(f"SELECT {parts} FROM ({self._summary}) t")
        return {name: int(row[name]) for name in SEGMENTS} if row else dict.fromkeys(SEGMENTS, 0)

    async def traders(
        self,
        segment: str = "all",
        q: str | None = None,
        limit: int = 200,
        offset: int = 0,
        *,
        cif_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Traders in a segment, newest first; `q` matches a CIF, a name or an
        email (case ignored), or only a CIF when `cif_only`."""
        if segment not in _WHERE:
            raise ValueError(f"unknown segment {segment!r}")
        args: list[Any] = [limit, offset]
        where = _WHERE[segment]
        if q:
            args.append(f"%{q.strip().lower()}%")
            where += (
                " AND lower(cif) LIKE $3"
                if cif_only
                else " AND (lower(cif) LIKE $3 OR lower(full_name) LIKE $3 OR lower(email) LIKE $3)"
            )
        rows = await self._pool.fetch(
            f"SELECT * FROM ({self._summary}) t WHERE {where} "
            "ORDER BY created_at DESC, cif DESC LIMIT $1 OFFSET $2",
            *args,
        )
        return [_row(r) for r in rows]

    async def trader(self, cif: str) -> dict[str, Any] | None:
        """One trader's file: the registration, accounts, purchases, identity
        checks, cases, payouts and the emails sent (subjects only: the bodies
        name the wallet), each newest first."""
        s = self._s
        found = await self._pool.fetch(f"SELECT * FROM ({self._summary}) t WHERE cif = $1", cif)
        if not found:
            return None

        async def rows(sql: str) -> list[dict[str, Any]]:
            return [_row(r) for r in await self._pool.fetch(sql.format(s=s), cif)]

        return {
            "trader": _row(found[0]),
            "accounts": await rows(
                "SELECT acc, product_id, stage, size_cents, rsv_id, created_at "
                "FROM {s}.accounts WHERE cif = $1 ORDER BY created_at DESC, acc DESC"
            ),
            "orders": await rows(
                "SELECT ord, product_id, size_cents, price_cents, status, provider, acc, "
                "created_at, paid_at FROM {s}.orders WHERE cif = $1 "
                "ORDER BY created_at DESC, ord DESC"
            ),
            "onboardings": await rows(
                "SELECT onb, status, kyc_attempts, acc, created_at FROM {s}.onboardings "
                "WHERE cif = $1 ORDER BY created_at DESC, onb DESC"
            ),
            "cases": await rows(
                "SELECT cas, status, severity, outcome, opened_at, closed_at FROM {s}.cases "
                "WHERE cif = $1 ORDER BY opened_at DESC, cas DESC"
            ),
            "payouts": await rows(
                "SELECT po, acc, trader_amount_cents, status, requested_at, paid_at "
                "FROM {s}.payouts WHERE cif = $1 ORDER BY requested_at DESC, po DESC"
            ),
            "notifications": await rows(
                "SELECT n.ntf, n.kind, n.subject, n.created_at, "
                "(SELECT e.kind FROM {s}.notification_events e WHERE e.ntf = n.ntf "
                " ORDER BY e.id DESC LIMIT 1) AS delivery "
                "FROM {s}.notifications n WHERE n.cif = $1 ORDER BY n.created_at DESC, n.ntf DESC"
            ),
            "events": await rows(
                "SELECT kind, ts FROM {s}.cif_events WHERE cif = $1 ORDER BY id DESC LIMIT 50"
            ),
        }


# --- reports (rule AC20) ---------------------------------------------------------------

TX_TYPES = ("purchase", "payout")
TX_STATUSES = (
    # purchases
    "pending",
    "paid",
    "provisioned",
    "failed",
    # payouts ("paid" and "failed" are shared)
    "requested",
    "approved",
    "debited",
    "sent",
    "denied",
    "cancelled",
)

# one row per money movement: a purchase (money in) or a payout (money out)
_TRANSACTIONS = """
SELECT ord AS ref, 'purchase' AS type, cif, acc, price_cents AS amount_cents, 'in' AS direction,
       status, product_id || coalesce(' code ' || discount_code, '') AS remark,
       created_at AS at, paid_at AS settled_at
FROM {s}.orders
UNION ALL
SELECT po, 'payout', cif, acc, trader_amount_cents, 'out',
       status, coalesce(failure, ''), requested_at, paid_at
FROM {s}.payouts
"""

# money counted as moved: a purchase once paid, a payout once paid
_SETTLED = (
    "(type = 'purchase' AND status IN ('paid', 'provisioned'))"
    " OR (type = 'payout' AND status = 'paid')"
)
_TOTALS = (
    "SELECT count(*) AS count, "
    f"coalesce(sum(amount_cents) FILTER (WHERE direction = 'in' AND ({_SETTLED})), 0) AS in_cents, "
    f"coalesce(sum(amount_cents) FILTER (WHERE direction = 'out' AND ({_SETTLED})), 0) AS out_cents"
)


class TxFilter:
    """The transaction log's filters; every value travels as a $n parameter."""

    def __init__(
        self,
        type: str | None = None,
        status: str | None = None,
        cif: str | None = None,
        remark: str | None = None,
        since: int | None = None,
        until: int | None = None,
    ) -> None:
        if type is not None and type not in TX_TYPES:
            raise ValueError(f"unknown type {type!r}")
        if status is not None and status not in TX_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        self.type, self.status, self.cif = type, status, cif
        self.remark, self.since, self.until = remark, since, until

    def where(self, first: int = 1) -> tuple[str, list[Any]]:
        """A WHERE clause built from constant text, and its arguments,
        numbered from $`first`."""
        parts: list[str] = []
        args: list[Any] = []

        def add(clause: str, value: Any) -> None:
            args.append(value)
            parts.append(clause.replace("$?", "$" + str(first + len(args) - 1)))

        if self.type is not None:
            add("type = $?", self.type)
        if self.status is not None:
            add("status = $?", self.status)
        if self.cif is not None:
            add("cif = $?", self.cif)
        if self.remark:
            add("lower(remark) LIKE $?", f"%{self.remark.strip().lower()}%")
        if self.since is not None:
            add("at >= $?", self.since)
        if self.until is not None:
            add("at < $?", self.until)
        return (" AND ".join(parts) or "TRUE"), args


class Reports:
    """Staff reports over the customer records (rule AC20): the transaction
    log and the email log. Read only."""

    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool, self._s = pool, check_schema(schema)
        self._tx = _TRANSACTIONS.format(s=self._s)

    async def transactions(self, f: TxFilter, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """Matching rows, newest first, and totals over all matching rows:
        the count, and money in and out counted once settled."""
        where, args = f.where(first=3)
        rows = await self._pool.fetch(
            f"SELECT * FROM ({self._tx}) t WHERE {where} "
            "ORDER BY at DESC, ref DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
            *args,
        )
        where, args = f.where()
        total = await self._pool.fetchrow(
            f"{_TOTALS} FROM ({self._tx}) t WHERE {where}",
            *args,
        )
        assert total is not None
        return {
            "rows": [dict(r) for r in rows],
            "totals": {k: int(total[k]) for k in ("count", "in_cents", "out_cents")},
        }

    async def emails(
        self, delivery: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Emails sent to traders, newest first, with the latest delivery
        state (sent, failed, or none yet); never the body."""
        s = self._s
        where = "TRUE" if delivery is None else "coalesce(delivery, 'waiting') = $3"
        args: list[Any] = [limit, offset] + ([] if delivery is None else [delivery])
        rows = await self._pool.fetch(
            "SELECT * FROM (SELECT n.ntf, n.cif, n.kind, n.subject, n.created_at, "
            f"(SELECT e.kind FROM {s}.notification_events e WHERE e.ntf = n.ntf "
            f"ORDER BY e.id DESC LIMIT 1) AS delivery FROM {s}.notifications n) t "
            f"WHERE {where} ORDER BY created_at DESC, ntf DESC LIMIT $1 OFFSET $2",
            *args,
        )
        return [dict(r) for r in rows]


# --- export ---------------------------------------------------------------------------

EXPORT_COLUMNS = (
    "ref",
    "type",
    "cif",
    "acc",
    "amount",
    "direction",
    "status",
    "remark",
    "at",
    "settled_at",
)


def cents_text(cents: int) -> str:
    """123456 -> "1234.56"; integer arithmetic, no rounding."""
    sign = "-" if cents < 0 else ""
    whole, rest = divmod(abs(cents), 100)
    return f"{sign}{whole}.{rest:02d}"


def utc_text(ms: int | None) -> str:
    """Epoch ms -> "2026-11-12T08:05:00Z" (whole seconds; the milliseconds
    are cut, not rounded); empty for none."""
    if ms is None:
        return ""
    t = dt.datetime.fromtimestamp(ms // 1000, tz=dt.UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_cell(text: str) -> str:
    """A cell a spreadsheet will not run as a formula (CSV injection): text
    starting with = + - @ or a tab or carriage return gets a leading '."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_rows(rows: list[dict[str, Any]]) -> list[list[str]]:
    """Transaction rows as CSV cells, header first."""
    out = [list(EXPORT_COLUMNS)]
    for r in rows:
        out.append(
            [
                r["ref"],
                r["type"],
                r["cif"],
                r["acc"] or "",
                cents_text(int(r["amount_cents"])),
                r["direction"],
                r["status"],
                safe_cell(r["remark"] or ""),
                utc_text(r["at"]),
                utc_text(r["settled_at"]),
            ]
        )
    return out
