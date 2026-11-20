"""Refunds and chargebacks (D-14.22): ledger entries, statement lines and
the bound on what may be returned. Database paths are in
tests/integration/test_disputes.py."""

from propfirm.customers.books import (
    CASH,
    PROCESSOR_FEES,
    chargeback_entry,
    chargeback_won_entry,
    refund_entry,
    statement_rows,
)
from propfirm.customers.disputes import Room

JAN = 1_767_225_600_000  # 2026-01-01 UTC


def postings_of(entry: object) -> list[tuple[str, int]]:
    return [(p.account, p.amount) for p in entry.postings]  # type: ignore[attr-defined]


def test_a_refund_returns_cash_against_contra_income() -> None:
    e = refund_entry(
        {"rfd": "RFD1", "ord": "ORD1", "amount_cents": 5_500, "decided_at": 9, "product_id": "lite"}
    )
    assert (e.entry_id, e.kind, e.reference, e.occurred_at) == (
        "refund:RFD1",
        "order_refunded",
        "ORD1",
        9,
    )
    assert postings_of(e) == [("income:refunds:lite", 5_500), (CASH, -5_500)]


def test_a_chargeback_takes_the_amount_and_the_fee_and_a_win_returns_the_amount() -> None:
    row = {
        "cbk": "CBK1",
        "ord": "ORD1",
        "amount_cents": 5_500,
        "fee_cents": 1_500,
        "received_at": 7,
        "decided_at": 11,
        "product_id": "lite",
    }
    lost = chargeback_entry(row)
    assert lost.entry_id == "chargeback:CBK1"
    assert postings_of(lost) == [
        ("income:chargebacks:lite", 5_500),
        (PROCESSOR_FEES, 1_500),
        (CASH, -7_000),
    ]
    assert postings_of(chargeback_entry({**row, "fee_cents": 0})) == [
        ("income:chargebacks:lite", 5_500),
        (CASH, -5_500),
    ]
    won = chargeback_won_entry(row)
    assert (won.entry_id, won.occurred_at) == ("chargeback-won:CBK1", 11)
    assert postings_of(won) == [(CASH, 5_500), ("income:chargebacks:lite", -5_500)]


def test_refunds_and_chargebacks_lower_net_revenue_and_cash() -> None:
    def p(kind: str, account: str, amount: int) -> dict[str, object]:
        return {
            "occurred_at": JAN,
            "kind": kind,
            "account": account,
            "amount_cents": amount,
            "product_id": "lite",
        }

    postings = [
        p("order_paid", CASH, 10_000),
        p("order_paid", "income:fees:lite", -10_000),
        p("order_refunded", "income:refunds:lite", 2_000),
        p("order_refunded", CASH, -2_000),
        p("chargeback", "income:chargebacks:lite", 3_000),
        p("chargeback", PROCESSOR_FEES, 1_500),
        p("chargeback", CASH, -4_500),
        p("chargeback_won", CASH, 3_000),
        p("chargeback_won", "income:chargebacks:lite", -3_000),
    ]
    accrual = statement_rows(postings, "accrual", ["2026-01"])[0]
    assert (accrual["refunds"], accrual["chargebacks"], accrual["net_revenue"]) == (2_000, 0, 8_000)
    assert (accrual["operating"], accrual["profit"]) == (1_500, 6_500)
    cash = statement_rows(postings, "cash", ["2026-01"])[0]
    assert (cash["received"], cash["refunds_paid"], cash["chargebacks"]) == (10_000, 2_000, 1_500)
    assert cash["net_cash"] == 6_500


def test_what_may_still_be_returned() -> None:
    room = Room(price_cents=10_900, refunded_cents=1_000, proposed_cents=900, charged_back_cents=0)
    assert room.left_cents == 9_000
    assert Room(5_500, 0, 0, 5_500).left_cents == 0
