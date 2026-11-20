"""Reconciliation (D-14.21): statement parsing and line matching. The
database path is in tests/integration/test_reconciliation.py."""

import pytest

from propfirm.customers.reconciliation import (
    StatementLine,
    StatementRefused,
    match,
    money_cents,
    parse_statement,
    utc_ms,
)

NOV = utc_ms("2026-11-01T00:00:00Z")
DEC = utc_ms("2026-12-01T00:00:00Z")
GOOD = """date,reference,direction,amount,currency,fee
2026-11-20T09:30:00Z,inv-1,in,55.00,USD,1.10
2026-11-20T10:00:00+06:00,PAY-7,OUT,120.5,usdt,
"""


def test_times_and_amounts_are_exact() -> None:
    assert utc_ms("1970-01-01T00:00:00Z") == 0
    assert utc_ms("2026-11-20T10:00:00+06:00") == utc_ms("2026-11-20T04:00:00Z")
    assert utc_ms("1970-01-01T00:00:00.0019Z") == 1  # below a millisecond: floor
    assert money_cents("55") == 5500
    assert money_cents("55.5") == 5550
    assert money_cents("0.07") == 7
    for bad in ("55.555", "-1", "1,000.00", "", "5e3", " ."):
        with pytest.raises(ValueError):
            money_cents(bad)
    with pytest.raises(ValueError, match="no time zone"):
        utc_ms("2026-11-20T09:30:00")


def test_a_good_statement_is_read_line_by_line() -> None:
    lines = parse_statement(GOOD, "both", NOV, DEC)
    assert lines == (
        StatementLine(1, utc_ms("2026-11-20T09:30:00Z"), "inv-1", "in", 5500, 110, "USD"),
        StatementLine(2, utc_ms("2026-11-20T04:00:00Z"), "PAY-7", "out", 12050, 0, "USDT"),
    )


def test_the_fee_column_is_optional() -> None:
    text = "date,reference,direction,amount,currency\n2026-11-02T00:00:00Z,a,in,1,USD\n"
    assert parse_statement(text, "in", NOV, DEC)[0].fee_cents == 0


@pytest.mark.parametrize(
    ("text", "covers", "code", "problem"),
    [
        ("", "in", "empty", None),
        ("date,reference,direction,amount,currency\n", "in", "empty", None),
        ("when,ref,dir,amount,currency\nx,y,in,1,USD\n", "in", "bad_header", None),
        (GOOD, "in", "bad_lines", "line 2: direction 'out' is not covered (in)"),
        (GOOD.replace("55.00", "55.001"), "both", "bad_lines", "line 1: not an amount"),
        (
            GOOD.replace("2026-11-20T09:30:00Z", "2026-12-01T00:00:00Z"),
            "both",
            "bad_lines",
            "line 1: outside the period",
        ),
        (GOOD.replace("55.00", "0.00"), "both", "bad_lines", "line 1: the amount is more than 0"),
        (GOOD.replace(",inv-1,", ",,"), "both", "bad_lines", "line 1: a reference"),
        (GOOD.replace(",USD,", ",US$,"), "both", "bad_lines", "line 1: not a currency"),
        (GOOD.replace("USD,1.10", "USD"), "both", "bad_lines", "line 1: 5 columns, not 6"),
        (GOOD, "sideways", "bad_request", None),
    ],
)
def test_a_bad_statement_is_refused_whole(
    text: str, covers: str, code: str, problem: str | None
) -> None:
    with pytest.raises(StatementRefused) as caught:
        parse_statement(text, covers, NOV, DEC)
    assert caught.value.code == code
    if problem is not None:
        assert any(p.startswith(problem) for p in caught.value.problems), caught.value.problems


def test_too_many_lines_are_refused() -> None:
    text = "date,reference,direction,amount,currency\n" + "2026-11-02T00:00:00Z,a,in,1,USD\n" * 5001
    with pytest.raises(StatementRefused, match="at most 5000 lines"):
        parse_statement(text, "in", NOV, DEC)


def line(direction: str = "in", amount: int = 5500, reference: str = "r") -> StatementLine:
    return StatementLine(1, NOV, reference, direction, amount, 0, "USD")


def test_each_line_gets_one_status() -> None:
    seen: set[tuple[str, str]] = set()
    assert match(line(), ("ORD1", "provisioned", 5500), seen).status == "matched"
    assert match(line(reference="x"), None, seen).status == "unmatched"
    differs = match(line(reference="d"), ("ORD2", "paid", 5000), seen)
    assert (differs.status, differs.record, differs.expected_cents) == (
        "amount_differs",
        "ORD2",
        5000,
    )
    assert match(line(reference="p"), ("ORD3", "pending", 5500), seen).status == "not_settled"
    again = match(line(), ("ORD1", "provisioned", 5500), seen)
    assert (again.status, again.record) == ("repeated", "ORD1")
    # money out: a payout is settled once sent or paid
    assert match(line("out", 100, "po"), ("PO1", "sent", 100), seen).status == "matched"
    assert match(line("out", 100, "pa"), ("PO2", "approved", 100), seen).status == "not_settled"
    # the same reference in the other direction is not a repeat
    assert match(line("out", 5500), ("PO3", "paid", 5500), seen).status == "matched"
