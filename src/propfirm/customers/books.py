"""Operator ledger (build plan step 3, D-14.16): the firm's own books.

Double entry in integer US cents, append-only, beside the trader ledger
(which only the engine writes; this book never touches it). Postings use
the engine ledger's sign rule: a debit is positive, a credit negative,
and each entry sums to zero (`propfirm.ledger.Entry` checks it).

Entries come from the source records, each with a fixed id, so a sync run
twice posts once:

- `order:<ORD>`: a paid order. Cash in the price; fee income at the list
  price plus add-ons; the discount as contra income. A free trial (price
  0) posts nothing.
- `payout-debited:<PO>`: the engine took the payout from the account.
  Payout expense (the trader's share) and fee refund (contra income)
  against payouts payable. Accrual view.
- `payout-paid:<PO>`: the payout was sent. Payouts payable against cash.
- `referral:<id>`: commission accrued (expense against commission
  payable) or clawed back (the reverse).
- `expense:<EXP>`: an operating expense, proposed by one member of staff
  and approved by another; against cash if paid, else against payables.

Corrections are new entries, never edits.
"""

import csv
import io
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

import asyncpg

from propfirm.ledger import Entry, Posting
from propfirm.pgschema import Conn

# --- chart of accounts ------------------------------------------------------------------

ASSET, LIABILITY, EQUITY, INCOME, EXPENSE = "asset", "liability", "equity", "income", "expense"

CASH = "asset:cash"
PAYOUTS_PAYABLE = "liability:payouts_payable"
COMMISSIONS_PAYABLE = "liability:commissions_payable"
PAYABLES = "liability:payables"
OWNER_EQUITY = "equity:owner"
PAYOUT_EXPENSE = "expense:payouts"
COMMISSION_EXPENSE = "expense:affiliate_commissions"
PROCESSOR_FEES = "expense:processor_fees"

# operating expense categories staff may post (D-14.16); processor fees are
# posted here by hand until the payment adapters report them (step 4)
EXPENSE_CATEGORIES = (
    "hosting",
    "market_data",
    "software",
    "identity_checks",
    "processor_fees",
    "legal",
    "marketing",
    "staff",
    "other",
)

FIXED: dict[str, tuple[str, str]] = {
    CASH: (ASSET, "Cash"),
    PAYOUTS_PAYABLE: (LIABILITY, "Payouts payable"),
    COMMISSIONS_PAYABLE: (LIABILITY, "Commissions payable"),
    PAYABLES: (LIABILITY, "Other payables"),
    OWNER_EQUITY: (EQUITY, "Owner's equity"),
    PAYOUT_EXPENSE: (EXPENSE, "Trader payouts"),
    COMMISSION_EXPENSE: (EXPENSE, "Affiliate commissions"),
}


def fee_income(product_id: str) -> str:
    return f"income:fees:{product_id}"


def discounts(product_id: str) -> str:
    return f"income:discounts:{product_id}"  # contra income: debit balance


def fee_refunds(product_id: str) -> str:
    return f"income:fee_refunds:{product_id}"  # contra income: debit balance


def operating(category: str) -> str:
    if category not in EXPENSE_CATEGORIES:
        raise ValueError(f"unknown expense category {category!r}")
    return PROCESSOR_FEES if category == "processor_fees" else f"expense:operating:{category}"


def account_type(account: str) -> str:
    kind = account.split(":", 1)[0]
    if kind not in (ASSET, LIABILITY, EQUITY, INCOME, EXPENSE):
        raise ValueError(f"unknown account {account!r}")
    return kind


def account_name(account: str) -> str:
    if account in FIXED:
        return FIXED[account][1]
    parts = account.split(":")
    if parts[:2] == ["income", "fees"]:
        return f"Fees: {parts[2]}"
    if parts[:2] == ["income", "discounts"]:
        return f"Discounts: {parts[2]}"
    if parts[:2] == ["income", "fee_refunds"]:
        return f"Fee refunds: {parts[2]}"
    if account == PROCESSOR_FEES:
        return "Processor fees"
    if parts[:2] == ["expense", "operating"]:
        return f"Operating: {parts[2].replace('_', ' ')}"
    return account


# --- entries made from source records --------------------------------------------------


def order_entry(row: dict[str, Any]) -> Entry | None:
    """A paid order; None for a free trial (nothing paid)."""
    price, listed = int(row["price_cents"]), int(row["list_price_cents"])
    if price <= 0:
        return None
    share = Fraction(str(row["discount"]))
    off = math.floor(listed * share)  # as checkout priced it
    addons = price - (listed - off)
    product = str(row["product_id"])
    postings = [Posting(CASH, price), Posting(fee_income(product), -(listed + addons))]
    if off:
        postings.append(Posting(discounts(product), off))
    return Entry(
        f"order:{row['ord']}", int(row["paid_at"]), "order_paid", tuple(postings), row["ord"]
    )


def payout_debited_entry(row: dict[str, Any]) -> Entry:
    """The trader's amount becomes payable: their profit share as payout
    expense, a fee refund as contra income."""
    amount, refund = int(row["trader_amount_cents"]), int(row["fee_refund_cents"])
    share = amount - refund
    product = str(row["product_id"])
    postings = [Posting(PAYOUTS_PAYABLE, -amount)]
    if share:
        postings.append(Posting(PAYOUT_EXPENSE, share))
    if refund:
        postings.append(Posting(fee_refunds(product), refund))
    return Entry(
        f"payout-debited:{row['po']}",
        int(row["debited_at"]),
        "payout_debited",
        tuple(postings),
        row["po"],
    )


def payout_paid_entry(row: dict[str, Any]) -> Entry:
    amount = int(row["trader_amount_cents"])
    return Entry(
        f"payout-paid:{row['po']}",
        int(row["paid_at"]),
        "payout_paid",
        (Posting(PAYOUTS_PAYABLE, amount), Posting(CASH, -amount)),
        row["po"],
    )


def referral_entry(row: dict[str, Any]) -> Entry:
    amount = int(row["amount_cents"])  # positive accrued, negative clawback
    return Entry(
        f"referral:{row['id']}",
        int(row["ts"]),
        "commission_accrued" if amount > 0 else "commission_clawback",
        (Posting(COMMISSION_EXPENSE, amount), Posting(COMMISSIONS_PAYABLE, -amount)),
        str(row["ord"]),
    )


def expense_entry(row: dict[str, Any]) -> Entry:
    amount = int(row["amount_cents"])
    against = CASH if row["paid"] else PAYABLES
    return Entry(
        f"expense:{row['exp']}",
        int(row["incurred_at"]),
        "operating_expense",
        (Posting(operating(str(row["category"])), amount), Posting(against, -amount)),
        str(row["exp"]),
    )


# --- the store ---------------------------------------------------------------------------


class ExpenseRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Line:
    account: str
    type: str
    name: str
    balance_cents: int  # debit positive


class Books:
    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        self._pool = pool
        self._s = schema
        self._last = -1

    async def post(self, conn: Conn, entry: Entry, by: str, now: int, memo: str = "") -> bool:
        """Post `entry` once; False if its id is already in the book (the
        same entry again is a no-op; a different one with that id is an
        error)."""
        s = self._s
        made = await conn.fetchval(
            f"INSERT INTO {s}.ops_entries (entry_id, occurred_at, kind, reference, memo, "
            f"created_by, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7) "
            f"ON CONFLICT (entry_id) DO NOTHING RETURNING entry_id",
            entry.entry_id,
            entry.occurred_at,
            entry.kind,
            entry.reference,
            memo,
            by,
            now,
        )
        if made is None:
            stored = await conn.fetch(
                f"SELECT account, amount_cents FROM {s}.ops_postings WHERE entry_id = $1 "
                f"ORDER BY line",
                entry.entry_id,
            )
            if [(r["account"], r["amount_cents"]) for r in stored] != [
                (p.account, p.amount) for p in entry.postings
            ]:
                raise ValueError(f"{entry.entry_id} is in the book with other postings")
            return False
        await conn.executemany(
            f"INSERT INTO {s}.ops_postings (entry_id, line, account, amount_cents) "
            f"VALUES ($1, $2, $3, $4)",
            [(entry.entry_id, n, p.account, p.amount) for n, p in enumerate(entry.postings)],
        )
        return True

    async def _post_all(self, entries: Iterable[Entry], now: int) -> int:
        made = 0
        for entry in entries:
            async with self._pool.acquire() as conn, conn.transaction():
                made += await self.post(conn, entry, "system", now)
        return made

    async def sync(self, now: int, every_ms: int = 60_000) -> int:
        """Post every source record not yet in the book; return how many
        entries were made. At most once per `every_ms`."""
        if self._last >= 0 and now - self._last < every_ms:
            return 0
        self._last = now
        s = self._s
        orders = await self._pool.fetch(
            f"SELECT o.ord, o.product_id, o.list_price_cents, o.discount, o.price_cents, "
            f"o.paid_at FROM {s}.orders o WHERE o.status IN ('paid', 'provisioned') "
            f"AND o.paid_at IS NOT NULL AND o.price_cents > 0 "
            f"AND NOT EXISTS (SELECT 1 FROM {s}.ops_entries e "
            f"WHERE e.entry_id = 'order:' || o.ord) ORDER BY o.paid_at, o.ord"
        )
        payouts = await self._pool.fetch(
            f"SELECT p.po, p.trader_amount_cents, p.debited_at, p.paid_at, p.status, o.product_id, "
            f"(SELECT e.detail FROM {s}.payout_events e WHERE e.po = p.po AND e.kind = 'requested' "
            f"ORDER BY e.id LIMIT 1) AS requested "
            f"FROM {s}.payouts p JOIN {s}.orders o ON o.ord = p.ord "
            f"WHERE p.debited_at IS NOT NULL AND (NOT EXISTS (SELECT 1 FROM {s}.ops_entries e "
            f"WHERE e.entry_id = 'payout-debited:' || p.po) OR (p.status = 'paid' AND "
            f"NOT EXISTS (SELECT 1 FROM {s}.ops_entries e "
            f"WHERE e.entry_id = 'payout-paid:' || p.po))) "
            f"ORDER BY p.debited_at, p.po"
        )
        referrals = await self._pool.fetch(
            f"SELECT r.id, r.ord, r.amount_cents, r.ts FROM {s}.referral_entries r "
            f"WHERE NOT EXISTS (SELECT 1 FROM {s}.ops_entries e "
            f"WHERE e.entry_id = 'referral:' || r.id) ORDER BY r.id"
        )
        expenses = await self._pool.fetch(
            f"SELECT * FROM {s}.ops_expenses x WHERE x.status = 'approved' "
            f"AND NOT EXISTS (SELECT 1 FROM {s}.ops_entries e "
            f"WHERE e.entry_id = 'expense:' || x.exp) ORDER BY x.exp"
        )
        entries: list[Entry] = []
        for r in orders:
            e = order_entry(dict(r))
            if e is not None:
                entries.append(e)
        for r in payouts:
            row = dict(r)
            detail = row.pop("requested")
            facts = json.loads(detail) if isinstance(detail, str) else (detail or {})
            row["fee_refund_cents"] = int(facts.get("eligibility", {}).get("fee_refund_cents", 0))
            entries.append(payout_debited_entry(row))
            if row["status"] == "paid" and row["paid_at"] is not None:
                entries.append(payout_paid_entry(row))
        entries += [referral_entry(dict(r)) for r in referrals]
        entries += [expense_entry(dict(r)) for r in expenses]
        return await self._post_all(entries, now)

    # --- expenses, two people ---------------------------------------------------------

    async def propose_expense(
        self,
        category: str,
        amount_cents: int,
        memo: str,
        paid: bool,
        incurred_at: int,
        by: str,
        now: int,
    ) -> dict[str, Any]:
        operating(category)  # ValueError for an unknown category
        if amount_cents <= 0:
            raise ValueError("an expense is more than 0")
        s = self._s
        number = await self._pool.fetchval(f"SELECT nextval('{s}.exp_seq')")
        row = await self._pool.fetchrow(
            f"INSERT INTO {s}.ops_expenses (exp, category, amount_cents, memo, paid, incurred_at, "
            f"proposed_by, proposed_at, status) "
            f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'proposed') RETURNING *",
            f"EXP{number:08d}",
            category,
            amount_cents,
            memo,
            paid,
            incurred_at,
            by,
            now,
        )
        assert row is not None
        return dict(row)

    async def decide_expense(
        self, exp: str, approve: bool, by: str, reason: str, now: int
    ) -> dict[str, Any]:
        """Approve (posts the entry at once) or reject; never by the proposer."""
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT * FROM {s}.ops_expenses WHERE exp = $1 FOR UPDATE", exp
            )
            if row is None:
                raise ExpenseRefused("unknown", "no such expense")
            if row["status"] != "proposed":
                raise ExpenseRefused("decided", f"the expense is {row['status']}")
            if row["proposed_by"] == by:
                raise ExpenseRefused("same_person", "a second member of staff decides")
            updated = await conn.fetchrow(
                f"UPDATE {s}.ops_expenses SET status = $2, decided_by = $3, decided_at = $4, "
                f"reason = $5 WHERE exp = $1 RETURNING *",
                exp,
                "approved" if approve else "rejected",
                by,
                now,
                reason,
            )
            assert updated is not None
            if approve:
                await self.post(conn, expense_entry(dict(updated)), by, now, str(updated["memo"]))
            return dict(updated)

    async def expenses(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.ops_expenses ORDER BY proposed_at DESC, exp DESC LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]

    # --- reading ------------------------------------------------------------------------

    async def trial_balance(self, until: int | None = None) -> list[Line]:
        """Every account's balance (debit positive) from entries up to
        `until` (ms, inclusive); the balances sum to zero."""
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT p.account, sum(p.amount_cents) AS balance FROM {s}.ops_postings p "
            f"JOIN {s}.ops_entries e ON e.entry_id = p.entry_id "
            f"WHERE $1::bigint IS NULL OR e.occurred_at <= $1 "
            f"GROUP BY p.account ORDER BY p.account",
            until,
        )
        return [
            Line(
                r["account"],
                account_type(r["account"]),
                account_name(r["account"]),
                int(r["balance"]),
            )
            for r in rows
        ]

    async def statement(self, view: str, first: str, last: str) -> list[dict[str, Any]]:
        """The income statement from month `first` to `last` (YYYY-MM,
        inclusive), accrual or cash view, by month and product."""
        months = [first]
        while months[-1] != last:
            if len(months) > 120:
                raise ValueError("at most 120 months")
            months.append(next_month(months[-1]))
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT e.occurred_at, e.kind, p.account, p.amount_cents, "
            f"coalesce(o.product_id, po.product_id) AS product_id "
            f"FROM {s}.ops_postings p JOIN {s}.ops_entries e ON e.entry_id = p.entry_id "
            f"LEFT JOIN {s}.orders o ON o.ord = e.reference "
            f"LEFT JOIN (SELECT x.po, y.product_id FROM {s}.payouts x "
            f"JOIN {s}.orders y ON y.ord = x.ord) po ON po.po = e.reference "
            f"WHERE e.occurred_at >= $1 AND e.occurred_at < $2",
            month_start(first),
            month_start(next_month(last)),
        )
        return statement_rows([dict(r) for r in rows], view, months)

    async def entries(self, limit: int = 200) -> list[dict[str, Any]]:
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT e.*, coalesce(json_agg(json_build_array(p.account, p.amount_cents) "
            f"ORDER BY p.line), '[]') AS postings FROM {s}.ops_entries e "
            f"JOIN {s}.ops_postings p ON p.entry_id = e.entry_id "
            f"GROUP BY e.entry_id ORDER BY e.occurred_at DESC, e.entry_id DESC LIMIT $1",
            limit,
        )
        out = []
        for r in rows:
            row = dict(r)
            raw = row["postings"]
            row["postings"] = [
                {"account": a, "amount_cents": int(c)}
                for a, c in (json.loads(raw) if isinstance(raw, str) else raw)
            ]
            out.append(row)
        return out


# --- income statement (step 3, line 3) -------------------------------------------------

ALL = "all"  # the column of every product together
NONE = "none"  # costs of no product (operating expenses)

ACCRUAL_LINES = (
    "fees",
    "discounts",
    "fee_refunds",
    "net_revenue",
    "payouts",
    "commissions",
    "contribution",
    "operating",
    "profit",
)
CASH_LINES = ("received", "payouts_paid", "commissions_paid", "expenses_paid", "net_cash")


def month_of(ms: int) -> str:
    """The UTC calendar month of `ms`, as YYYY-MM."""
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m")


def month_start(month: str) -> int:
    """The first millisecond of a YYYY-MM month, UTC."""
    year, number = (int(x) for x in month.split("-"))
    return int(datetime(year, number, 1, tzinfo=UTC).timestamp() * 1000)


def next_month(month: str) -> str:
    year, number = (int(x) for x in month.split("-"))
    return f"{year + number // 12:04d}-{number % 12 + 1:02d}"


def accrual_line(account: str) -> str | None:
    """The statement line an income or expense account adds to."""
    if account.startswith("income:fees:"):
        return "fees"
    if account.startswith("income:discounts:"):
        return "discounts"
    if account.startswith("income:fee_refunds:"):
        return "fee_refunds"
    if account == PAYOUT_EXPENSE:
        return "payouts"
    if account == COMMISSION_EXPENSE:
        return "commissions"
    if account == PROCESSOR_FEES or account.startswith("expense:operating:"):
        return "operating"
    return None  # a balance sheet account


CASH_LINE = {
    "order_paid": "received",
    "payout_paid": "payouts_paid",
    "commission_paid": "commissions_paid",
    "operating_expense": "expenses_paid",
}


def _totals(lines: dict[str, int]) -> dict[str, int]:
    out = dict(lines)
    out["net_revenue"] = out["fees"] - out["discounts"] - out["fee_refunds"]
    out["contribution"] = out["net_revenue"] - out["payouts"] - out["commissions"]
    out["profit"] = out["contribution"] - out["operating"]
    return out


def statement_rows(
    postings: Iterable[dict[str, Any]], view: str, months: list[str]
) -> list[dict[str, Any]]:
    """Rows of (month, product, line amounts in cents) from posting rows
    with occurred_at, kind, account, amount_cents and product_id (None for
    no product). Income and costs are positive. Every month in `months`
    gets an `all` row, even an empty one."""
    if view not in ("accrual", "cash"):
        raise ValueError("view is accrual or cash")
    names = ACCRUAL_LINES if view == "accrual" else CASH_LINES
    cells: dict[tuple[str, str], dict[str, int]] = {}

    def cell(month: str, product: str) -> dict[str, int]:
        return cells.setdefault((month, product), dict.fromkeys(names, 0))

    for m in months:
        cell(m, ALL)
    for p in postings:
        month = month_of(int(p["occurred_at"]))
        if month not in months:
            continue
        product = p["product_id"] or NONE
        amount = int(p["amount_cents"])
        if view == "accrual":
            line = accrual_line(str(p["account"]))
            if line is None:
                continue
            value = -amount if line == "fees" else amount  # income credits are negative
        else:
            if p["account"] != CASH:
                continue
            line = CASH_LINE.get(str(p["kind"]))
            if line is None:
                continue
            value = amount if line == "received" else -amount
        for key in ((month, product), (month, ALL)):
            cell(*key)[line] += value
    rows = []
    for (month, product), lines in sorted(cells.items()):
        if view == "accrual":
            lines = _totals(lines)
        else:
            lines["net_cash"] = (
                lines["received"]
                - lines["payouts_paid"]
                - lines["commissions_paid"]
                - lines["expenses_paid"]
            )
        rows.append({"month": month, "product": product, **lines})
    return rows


def statement_csv(rows: list[dict[str, Any]], view: str) -> str:
    """The statement as CSV, amounts in dollars with two decimals."""
    names = ACCRUAL_LINES if view == "accrual" else CASH_LINES
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["month", "product", *names])
    for r in rows:
        writer.writerow([r["month"], r["product"], *(cents_text(r[n]) for n in names)])
    return out.getvalue()


def cents_text(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}{whole}.{part:02d}"
