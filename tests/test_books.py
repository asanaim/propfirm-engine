"""Operator ledger entries (D-14.16): each source record makes one
balanced entry with a fixed id."""

import pytest

from propfirm.customers.books import (
    CASH,
    COMMISSION_EXPENSE,
    COMMISSIONS_PAYABLE,
    PAYABLES,
    PAYOUT_EXPENSE,
    PAYOUTS_PAYABLE,
    PROCESSOR_FEES,
    account_name,
    account_type,
    cents_text,
    discounts,
    expense_entry,
    fee_income,
    fee_refunds,
    month_of,
    month_start,
    next_month,
    operating,
    order_entry,
    payout_debited_entry,
    payout_paid_entry,
    referral_entry,
    statement_csv,
    statement_rows,
)


def postings(entry: object) -> dict[str, int]:
    return {p.account: p.amount for p in entry.postings}  # type: ignore[attr-defined]


def test_a_paid_order_books_cash_income_and_the_discount() -> None:
    # Lite 25K: $109 list, 10 % code ($10.90), Swing add-on 15 % ($16.35)
    row = {
        "ord": "ORD1",
        "product_id": "lite",
        "list_price_cents": 10_900,
        "discount": "1/10",
        "price_cents": 10_900 - 1_090 + 1_635,
        "paid_at": 5,
    }
    e = order_entry(row)
    assert e is not None and (e.entry_id, e.occurred_at, e.kind, e.reference) == (
        "order:ORD1",
        5,
        "order_paid",
        "ORD1",
    )
    assert postings(e) == {
        CASH: 11_445,
        fee_income("lite"): -(10_900 + 1_635),
        discounts("lite"): 1_090,
    }


def test_an_order_without_a_discount_has_no_discount_line() -> None:
    row = {
        "ord": "ORD2",
        "product_id": "one-step",
        "list_price_cents": 9_500,
        "discount": "0",
        "price_cents": 9_500,
        "paid_at": 1,
    }
    assert postings(order_entry(row)) == {CASH: 9_500, fee_income("one-step"): -9_500}


def test_a_free_trial_books_nothing() -> None:
    row = {
        "ord": "ORD3",
        "product_id": "lite",
        "list_price_cents": 5_500,
        "discount": "1",
        "price_cents": 0,
        "paid_at": 1,
    }
    assert order_entry(row) is None


def test_a_payout_is_an_expense_when_debited_and_cash_when_paid() -> None:
    row = {
        "po": "PO1",
        "product_id": "lite",
        "trader_amount_cents": 85_500,
        "fee_refund_cents": 5_500,
        "debited_at": 10,
        "paid_at": 20,
    }
    debited = payout_debited_entry(row)
    assert (debited.entry_id, debited.occurred_at) == ("payout-debited:PO1", 10)
    assert postings(debited) == {
        PAYOUTS_PAYABLE: -85_500,
        PAYOUT_EXPENSE: 80_000,
        fee_refunds("lite"): 5_500,
    }
    paid = payout_paid_entry(row)
    assert (paid.entry_id, paid.occurred_at) == ("payout-paid:PO1", 20)
    assert postings(paid) == {PAYOUTS_PAYABLE: 85_500, CASH: -85_500}
    no_refund = payout_debited_entry({**row, "fee_refund_cents": 0})
    assert postings(no_refund) == {PAYOUTS_PAYABLE: -85_500, PAYOUT_EXPENSE: 85_500}


def test_commission_and_its_clawback_cancel() -> None:
    accrued = referral_entry({"id": 1, "ord": "ORD1", "amount_cents": 550, "ts": 3})
    back = referral_entry({"id": 2, "ord": "ORD1", "amount_cents": -550, "ts": 4})
    assert (accrued.kind, back.kind) == ("commission_accrued", "commission_clawback")
    assert postings(accrued) == {COMMISSION_EXPENSE: 550, COMMISSIONS_PAYABLE: -550}
    assert postings(back) == {COMMISSION_EXPENSE: -550, COMMISSIONS_PAYABLE: 550}


def test_an_expense_is_against_cash_or_payables() -> None:
    row = {
        "exp": "EXP1",
        "category": "hosting",
        "amount_cents": 2_999,
        "paid": True,
        "incurred_at": 7,
    }
    assert postings(expense_entry(row)) == {"expense:operating:hosting": 2_999, CASH: -2_999}
    owed = {**row, "paid": False, "category": "processor_fees"}
    assert postings(expense_entry(owed)) == {PROCESSOR_FEES: 2_999, PAYABLES: -2_999}
    with pytest.raises(ValueError, match="category"):
        operating("yachts")


def test_account_types_and_names() -> None:
    assert account_type(fee_income("lite")) == "income"
    assert account_type(CASH) == "asset" and account_type(PAYOUTS_PAYABLE) == "liability"
    assert account_name(fee_income("lite")) == "Fees: lite"
    assert account_name(discounts("instant")) == "Discounts: instant"
    assert account_name("expense:operating:market_data") == "Operating: market data"
    with pytest.raises(ValueError):
        account_type("cash")


# --- income statement (D-14.17) ---------------------------------------------------------

JAN = 1_767_225_600_000  # 2026-01-01T00:00:00Z
FEB = 1_769_904_000_000  # 2026-02-01T00:00:00Z


def row(ms: int, kind: str, account: str, amount: int, product: str | None) -> dict[str, object]:
    return {
        "occurred_at": ms,
        "kind": kind,
        "account": account,
        "amount_cents": amount,
        "product_id": product,
    }


POSTINGS = [
    # January: a Lite order $109 less $10.90, Swing $16.35
    row(JAN + 1, "order_paid", CASH, 11_445, "lite"),
    row(JAN + 1, "order_paid", fee_income("lite"), -12_535, "lite"),
    row(JAN + 1, "order_paid", discounts("lite"), 1_090, "lite"),
    row(JAN + 2, "commission_accrued", COMMISSION_EXPENSE, 1_144, "lite"),
    row(JAN + 2, "commission_accrued", COMMISSIONS_PAYABLE, -1_144, "lite"),
    # a 1-Step order $95
    row(JAN + 3, "order_paid", CASH, 9_500, "one-step"),
    row(JAN + 3, "order_paid", fee_income("one-step"), -9_500, "one-step"),
    # hosting, paid
    row(JAN + 4, "operating_expense", "expense:operating:hosting", 2_999, None),
    row(JAN + 4, "operating_expense", CASH, -2_999, None),
    # a Lite payout debited in January, paid in February
    row(JAN + 5, "payout_debited", PAYOUTS_PAYABLE, -85_500, "lite"),
    row(JAN + 5, "payout_debited", PAYOUT_EXPENSE, 80_000, "lite"),
    row(JAN + 5, "payout_debited", fee_refunds("lite"), 5_500, "lite"),
    row(FEB + 1, "payout_paid", PAYOUTS_PAYABLE, 85_500, "lite"),
    row(FEB + 1, "payout_paid", CASH, -85_500, "lite"),
]


def test_the_accrual_view_by_month_and_product() -> None:
    rows = {
        (r["month"], r["product"]): r
        for r in statement_rows(POSTINGS, "accrual", ["2026-01", "2026-02"])
    }
    lite = rows[("2026-01", "lite")]
    assert (lite["fees"], lite["discounts"], lite["fee_refunds"], lite["net_revenue"]) == (
        12_535,
        1_090,
        5_500,
        5_945,
    )
    assert (lite["payouts"], lite["commissions"], lite["contribution"]) == (
        80_000,
        1_144,
        5_945 - 81_144,
    )
    total = rows[("2026-01", "all")]
    assert (total["net_revenue"], total["operating"]) == (5_945 + 9_500, 2_999)
    assert total["profit"] == 5_945 + 9_500 - 80_000 - 1_144 - 2_999
    assert rows[("2026-01", "none")]["operating"] == 2_999
    feb = rows[("2026-02", "all")]  # the payment of a payable is no cost
    assert all(feb[n] == 0 for n in ("fees", "payouts", "profit"))


def test_the_cash_view_follows_the_money() -> None:
    rows = {
        (r["month"], r["product"]): r
        for r in statement_rows(POSTINGS, "cash", ["2026-01", "2026-02"])
    }
    jan, feb = rows[("2026-01", "all")], rows[("2026-02", "all")]
    assert (jan["received"], jan["expenses_paid"], jan["payouts_paid"], jan["net_cash"]) == (
        11_445 + 9_500,
        2_999,
        0,
        11_445 + 9_500 - 2_999,
    )
    assert (feb["payouts_paid"], feb["net_cash"]) == (85_500, -85_500)
    assert rows[("2026-02", "lite")]["payouts_paid"] == 85_500


def test_months_outside_the_range_and_empty_months() -> None:
    rows = statement_rows(POSTINGS, "accrual", ["2026-03"])
    assert [(r["month"], r["product"], r["profit"]) for r in rows] == [("2026-03", "all", 0)]
    with pytest.raises(ValueError, match="view"):
        statement_rows([], "tax", ["2026-01"])


def test_month_arithmetic_and_csv() -> None:
    assert (month_of(JAN), month_of(FEB - 1), next_month("2026-12")) == (
        "2026-01",
        "2026-01",
        "2027-01",
    )
    assert month_start("2026-02") == FEB
    rows = statement_rows(POSTINGS, "cash", ["2026-02"])
    text = statement_csv(rows, "cash")
    assert text.splitlines() == [
        "month,product,received,refunds_paid,chargebacks,payouts_paid,commissions_paid,"
        "expenses_paid,net_cash",
        "2026-02,all,0.00,0.00,0.00,855.00,0.00,0.00,-855.00",
        "2026-02,lite,0.00,0.00,0.00,855.00,0.00,0.00,-855.00",
    ]
    assert (cents_text(5), cents_text(-12_345), cents_text(0)) == ("0.05", "-123.45", "0.00")
