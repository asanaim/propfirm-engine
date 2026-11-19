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
    discounts,
    expense_entry,
    fee_income,
    fee_refunds,
    operating,
    order_entry,
    payout_debited_entry,
    payout_paid_entry,
    referral_entry,
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
