"""Withdrawal methods (D-14.25): the payout order carries no fixed network;
the charge is booked as a lower payout cost; the manual rail is idempotent."""

import asyncio

import pytest

from propfirm.customers.books import (
    CASH,
    PAYOUT_EXPENSE,
    PAYOUTS_PAYABLE,
    RESERVE,
    draw_entry,
    payout_paid_entry,
)
from propfirm.customers.payout_store import PayoutRecord
from propfirm.customers.providers import PayoutOrder, SandboxPayouts
from propfirm.customers.transfers import ManualPayouts, NoPayoutRail, WithdrawalUnavailable


def test_the_charge_stays_with_the_firm() -> None:
    row = {"po": "PO1", "trader_amount_cents": 80_000, "charge_cents": 900, "paid_at": 5}
    paid = payout_paid_entry(row)
    assert [(p.account, p.amount) for p in paid.postings] == [
        (PAYOUTS_PAYABLE, 80_000),
        (CASH, -79_100),
        (PAYOUT_EXPENSE, -900),
    ]
    assert [(p.account, p.amount) for p in draw_entry(row).postings] == [
        (CASH, 79_100),
        (RESERVE, -79_100),
    ]
    # without a charge the entries are what they were before (D-14.16)
    plain = payout_paid_entry({**row, "charge_cents": 0})
    assert [(p.account, p.amount) for p in plain.postings] == [
        (PAYOUTS_PAYABLE, 80_000),
        (CASH, -80_000),
    ]


def test_what_reaches_the_trader() -> None:
    record = PayoutRecord(
        "PO1", "ACC1", "CIF1", "ORD1", "WAL1", 0, 80_000, 100_000, "sent", 2, 1, None, None, None,
        method_id="usdt-hand", charge_cents=900,
    )  # fmt: skip
    assert record.net_cents == 79_100


def test_rails_send_an_order_with_no_network_fixed() -> None:
    order = PayoutOrder(
        "PO1", "usdt-hand", {"network": "TRC20", "address": "T" + "1" * 33}, 79_100, "USDT"
    )
    manual = ManualPayouts(None, "s", "usdt-hand")  # type: ignore[arg-type]
    first, again = asyncio.run(manual.send(order)), asyncio.run(manual.send(order))
    assert first == again == "usdt-hand-PO1"  # idempotent on the PO
    assert asyncio.run(SandboxPayouts().send(order)) == "sandbox-PO1"
    with pytest.raises(WithdrawalUnavailable):
        asyncio.run(NoPayoutRail().send(order))
