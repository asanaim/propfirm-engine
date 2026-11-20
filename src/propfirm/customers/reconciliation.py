"""Reconciliation (build plan step 3, line 6; D-14.21): a processor's
statement matched line by line to the firm's own records.

A member of staff with the settings change right uploads a statement as
CSV for one provider and one period, saying whether it covers money in
(sales), money out (payouts) or both. Columns, header first:

    date,reference,direction,amount,currency[,fee]

- date: ISO 8601 with a time zone (`2026-11-20T09:30:00Z`); never a local
  time without one.
- reference: the provider's id; for money in the order's provider
  reference, for money out the payout's.
- direction: `in` or `out`.
- amount, fee: dollars with at most two decimals (`55.00`); never rounded
  here, so a third decimal is refused. The fee defaults to 0.
- currency: as the statement gives it; stored, not converted. Amounts are
  compared in cents as written, so a USDT line is compared at par.

Every line is read before anything is stored; a statement with any bad
line is refused whole, with the line numbers. A line outside the period,
or in a direction the statement does not cover, is a bad line. The same
file twice for one provider is refused.

Each line gets one status:

- matched: the record exists, is settled and has this amount;
- amount_differs: it exists and is settled, with another amount;
- not_settled: it exists but is not paid (an order) or not sent or paid
  (a payout);
- unmatched: no record with this reference;
- repeated: the reference appeared on an earlier line in the same
  direction.

Missing: records the statement should list and does not, read when the
run is shown: paid orders of this provider in the period (money in), and
payouts paid in the period (money out). Runs are append-only; to match
again, upload again.
"""

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg

HEADER = ("date", "reference", "direction", "amount", "currency")
MAX_BYTES = 1_000_000
MAX_LINES = 5_000
STATUSES = ("matched", "amount_differs", "not_settled", "unmatched", "repeated")
COVERS = ("in", "out", "both")
_MONEY = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,2})?")
_CURRENCY = re.compile(r"[A-Z0-9]{2,10}")
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class StatementRefused(Exception):
    def __init__(self, code: str, message: str, problems: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.problems = problems


@dataclass(frozen=True, slots=True)
class StatementLine:
    line: int  # 1 is the first line after the header
    occurred_at: int  # ms UTC
    reference: str
    direction: str
    amount_cents: int
    fee_cents: int
    currency: str


def money_cents(text: str) -> int:
    """'55.5' -> 5550. Exact; more than two decimals is refused, never
    rounded."""
    value = text.strip()
    if not _MONEY.fullmatch(value):
        raise ValueError(f"not an amount in dollars with at most two decimals: {text!r}")
    whole, _, part = value.partition(".")
    return int(whole) * 100 + int(part.ljust(2, "0") or "0")


def utc_ms(text: str) -> int:
    """ISO 8601 with a time zone -> ms UTC; a time without a zone is
    refused, because it cannot be placed. Below a millisecond is dropped
    (floor), the engine's own time unit."""
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError as exc:
        raise ValueError(f"not an ISO 8601 time: {text!r}") from exc
    if moment.tzinfo is None:
        raise ValueError(f"no time zone: {text!r}")
    delta = moment - EPOCH  # exact: no float on the way
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def parse_statement(
    text: str, covers: str, period_from: int, period_to: int
) -> tuple[StatementLine, ...]:
    """Every line of `text`, or StatementRefused naming the bad lines (at
    most 20)."""
    if covers not in COVERS:
        raise StatementRefused("bad_request", "covers is in, out or both")
    if len(text.encode()) > MAX_BYTES:
        raise StatementRefused("too_large", f"a statement is at most {MAX_BYTES} bytes")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        raise StatementRefused("empty", "the statement is empty")
    header = tuple(c.strip().lower() for c in rows[0])
    if header not in (HEADER, (*HEADER, "fee")):
        raise StatementRefused(
            "bad_header", "the header is date,reference,direction,amount,currency[,fee]"
        )
    body = rows[1:]
    if not body:
        raise StatementRefused("empty", "the statement has no lines")
    if len(body) > MAX_LINES:
        raise StatementRefused("too_large", f"a statement is at most {MAX_LINES} lines")
    allowed = ("in", "out") if covers == "both" else (covers,)
    out: list[StatementLine] = []
    problems: list[str] = []
    for n, row in enumerate(body, start=1):
        try:
            if len(row) != len(header):
                raise ValueError(f"{len(row)} columns, not {len(header)}")
            cells = dict(zip(header, (c.strip() for c in row), strict=True))
            occurred = utc_ms(cells["date"])
            if not period_from <= occurred < period_to:
                raise ValueError("outside the period")
            reference = cells["reference"]
            if not reference or len(reference) > 200:
                raise ValueError("a reference of 1 to 200 characters is needed")
            direction = cells["direction"].lower()
            if direction not in allowed:
                raise ValueError(f"direction {direction!r} is not covered ({covers})")
            amount = money_cents(cells["amount"])
            if amount <= 0:
                raise ValueError("the amount is more than 0")
            fee = money_cents(cells["fee"]) if cells.get("fee") else 0
            currency = cells["currency"].upper()
            if not _CURRENCY.fullmatch(currency):
                raise ValueError(f"not a currency code: {cells['currency']!r}")
        except ValueError as exc:
            problems.append(f"line {n}: {exc}")
            continue
        out.append(StatementLine(n, occurred, reference, direction, amount, fee, currency))
    if problems:
        raise StatementRefused(
            "bad_lines", f"{len(problems)} bad lines; nothing was stored", tuple(problems[:20])
        )
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Match:
    status: str
    record: str | None  # ORD or PO
    expected_cents: int | None


def match(
    line: StatementLine,
    found: tuple[str, str, int] | None,
    seen: set[tuple[str, str]],
) -> Match:
    """The status of `line` given the record its reference names, as
    (id, status, amount), or None."""
    key = (line.direction, line.reference)
    if key in seen:
        return Match("repeated", None if found is None else found[0], None)
    seen.add(key)
    if found is None:
        return Match("unmatched", None, None)
    record, status, amount = found
    settled = ("paid", "provisioned") if line.direction == "in" else ("sent", "paid")
    if status not in settled:
        return Match("not_settled", record, amount)
    if amount != line.amount_cents:
        return Match("amount_differs", record, amount)
    return Match("matched", record, amount)


class Reconciliations:
    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._s = schema

    async def upload(
        self,
        provider: str,
        covers: str,
        period_from: int,
        period_to: int,
        filename: str,
        text: str,
        by: str,
        now: int,
    ) -> dict[str, Any]:
        if not _PROVIDER.fullmatch(provider):
            raise StatementRefused("bad_request", "provider: lower case letters, digits, - or _")
        if period_to <= period_from:
            raise StatementRefused("bad_request", "the period ends after it starts")
        lines = parse_statement(text, covers, period_from, period_to)
        digest = hashlib.sha256(text.encode()).hexdigest()
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            if await conn.fetchval(
                f"SELECT run FROM {s}.recon_runs WHERE provider = $1 AND sha256 = $2",
                provider,
                digest,
            ):
                raise StatementRefused("duplicate", "this statement was uploaded already")
            number = await conn.fetchval(f"SELECT nextval('{s}.rec_seq')")
            run = f"REC{int(number):08d}"
            await conn.execute(
                f"INSERT INTO {s}.recon_runs (run, provider, covers, period_from, period_to, "
                f"filename, sha256, uploaded_by, uploaded_at) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                run,
                provider,
                covers,
                period_from,
                period_to,
                filename[:200],
                digest,
                by,
                now,
            )
            refs_in = [x.reference for x in lines if x.direction == "in"]
            refs_out = [x.reference for x in lines if x.direction == "out"]
            orders = {
                r["provider_ref"]: (r["ord"], r["status"], int(r["price_cents"]))
                for r in await conn.fetch(
                    f"SELECT ord, status, price_cents, provider_ref FROM {s}.orders "
                    f"WHERE provider = $1 AND provider_ref = ANY($2::text[])",
                    provider,
                    refs_in,
                )
            }
            payouts = {
                r["provider_ref"]: (r["po"], r["status"], int(r["trader_amount_cents"]))
                for r in await conn.fetch(
                    f"SELECT po, status, trader_amount_cents, provider_ref FROM {s}.payouts "
                    f"WHERE provider_ref = ANY($1::text[])",
                    refs_out,
                )
            }
            seen: set[tuple[str, str]] = set()
            rows = []
            for x in lines:
                found = (orders if x.direction == "in" else payouts).get(x.reference)
                m = match(x, found, seen)
                rows.append(
                    (
                        run,
                        x.line,
                        x.occurred_at,
                        x.reference,
                        x.direction,
                        x.amount_cents,
                        x.fee_cents,
                        x.currency,
                        m.status,
                        m.record,
                        m.expected_cents,
                    )
                )
            await conn.executemany(
                f"INSERT INTO {s}.recon_lines (run, line, occurred_at, reference, direction, "
                f"amount_cents, fee_cents, currency, status, record, expected_cents) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
                rows,
            )
        return await self.summary(run)

    async def summary(self, run: str) -> dict[str, Any]:
        found = await self.runs(run)
        if not found:
            raise StatementRefused("unknown", f"no run {run}")
        return found[0]

    async def runs(self, run: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Runs, newest first, with counts by status and totals."""
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT r.*, count(l.line) AS lines, "
            f"coalesce(sum(l.amount_cents) FILTER (WHERE l.direction = 'in'), 0) AS in_cents, "
            f"coalesce(sum(l.amount_cents) FILTER (WHERE l.direction = 'out'), 0) AS out_cents, "
            f"coalesce(sum(l.fee_cents), 0) AS fee_cents "
            f"FROM {s}.recon_runs r LEFT JOIN {s}.recon_lines l ON l.run = r.run "
            f"WHERE $1::text IS NULL OR r.run = $1 "
            f"GROUP BY r.run ORDER BY r.uploaded_at DESC, r.run DESC LIMIT $2",
            run,
            limit,
        )
        counts = await self._pool.fetch(
            f"SELECT run, status, count(*) AS n FROM {s}.recon_lines "
            f"WHERE run = ANY($1::text[]) GROUP BY run, status",
            [r["run"] for r in rows],
        )
        by_run: dict[str, dict[str, int]] = {}
        for c in counts:
            by_run.setdefault(c["run"], {})[c["status"]] = int(c["n"])
        out = []
        for r in rows:
            statuses = by_run.get(r["run"], {})
            out.append(
                {
                    "run": r["run"],
                    "provider": r["provider"],
                    "covers": r["covers"],
                    "period_from": r["period_from"],
                    "period_to": r["period_to"],
                    "filename": r["filename"],
                    "sha256": r["sha256"],
                    "uploaded_by": r["uploaded_by"],
                    "uploaded_at": r["uploaded_at"],
                    "lines": int(r["lines"]),
                    "in_cents": int(r["in_cents"]),
                    "out_cents": int(r["out_cents"]),
                    "fee_cents": int(r["fee_cents"]),
                    "counts": {k: statuses.get(k, 0) for k in STATUSES},
                }
            )
        return out

    async def detail(self, run: str) -> dict[str, Any]:
        """The run, its lines and the records it is missing."""
        head = await self.summary(run)
        s = self._s
        lines = await self._pool.fetch(
            f"SELECT line, occurred_at, reference, direction, amount_cents, fee_cents, currency, "
            f"status, record, expected_cents FROM {s}.recon_lines WHERE run = $1 ORDER BY line",
            run,
        )
        missing: list[dict[str, Any]] = []
        if head["covers"] in ("in", "both"):
            missing += [
                {
                    "kind": "order",
                    "record": r["ord"],
                    "reference": r["provider_ref"],
                    "amount_cents": int(r["price_cents"]),
                    "at": r["paid_at"],
                }
                for r in await self._pool.fetch(
                    f"SELECT o.ord, o.provider_ref, o.price_cents, o.paid_at FROM {s}.orders o "
                    f"WHERE o.provider = $1 AND o.status IN ('paid', 'provisioned') "
                    f"AND o.price_cents > 0 AND o.paid_at >= $2 AND o.paid_at < $3 "
                    f"AND NOT EXISTS (SELECT 1 FROM {s}.recon_lines l WHERE l.run = $4 "
                    f"AND l.direction = 'in' AND l.reference = o.provider_ref) "
                    f"ORDER BY o.paid_at, o.ord",
                    head["provider"],
                    head["period_from"],
                    head["period_to"],
                    run,
                )
            ]
        if head["covers"] in ("out", "both"):
            missing += [
                {
                    "kind": "payout",
                    "record": r["po"],
                    "reference": r["provider_ref"],
                    "amount_cents": int(r["trader_amount_cents"]),
                    "at": r["paid_at"],
                }
                for r in await self._pool.fetch(
                    f"SELECT p.po, p.provider_ref, p.trader_amount_cents, p.paid_at "
                    f"FROM {s}.payouts p WHERE p.status = 'paid' "
                    f"AND p.paid_at >= $1 AND p.paid_at < $2 "
                    f"AND NOT EXISTS (SELECT 1 FROM {s}.recon_lines l WHERE l.run = $3 "
                    f"AND l.direction = 'out' AND l.reference = p.provider_ref) "
                    f"ORDER BY p.paid_at, p.po",
                    head["period_from"],
                    head["period_to"],
                    run,
                )
            ]
        return {
            **head,
            "items": [
                {
                    "line": r["line"],
                    "occurred_at": r["occurred_at"],
                    "reference": r["reference"],
                    "direction": r["direction"],
                    "amount_cents": r["amount_cents"],
                    "fee_cents": r["fee_cents"],
                    "currency": r["currency"],
                    "status": r["status"],
                    "record": r["record"],
                    "expected_cents": r["expected_cents"],
                }
                for r in lines
            ],
            "missing": missing,
        }
