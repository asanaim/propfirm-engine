"""The transaction log's filters and CSV cells (rule AC20)."""

import pytest

from propfirm.customers.directory import TxFilter, cents_text, export_rows, safe_cell, utc_text


def test_money_is_written_from_integer_cents() -> None:
    assert [cents_text(c) for c in (0, 5, 123456, -1999)] == ["0.00", "0.05", "1234.56", "-19.99"]


def test_time_is_utc_with_milliseconds_cut() -> None:
    assert utc_text(1_794_492_792_999) == "2026-11-12T14:13:12Z"
    assert utc_text(None) == ""


def test_a_cell_is_never_run_as_a_formula() -> None:
    for text in ("=1+1", "+cmd", "-2", "@SUM(A1)", "\tx", "\rx"):
        assert safe_cell(text) == "'" + text
    assert safe_cell("LITE_10K code SPRING-10") == "LITE_10K code SPRING-10"
    assert safe_cell("") == ""


def test_export_rows_put_the_header_first() -> None:
    row = {
        "ref": "PO00000001", "type": "payout", "cif": "CIF00000001", "acc": None,
        "amount_cents": 25_050, "direction": "out", "status": "failed",
        "remark": "=HYPERLINK(1)", "at": 0, "settled_at": None,
    }  # fmt: skip
    header, cells = export_rows([row])
    assert header[0] == "ref" and len(header) == len(cells)
    assert cells[3:5] == ["", "250.50"]
    assert cells[7] == "'=HYPERLINK(1)"
    assert cells[8:] == ["1970-01-01T00:00:00Z", ""]


def test_filters_number_their_arguments_and_refuse_unknown_values() -> None:
    where, args = TxFilter("payout", "paid", "CIF00000001", " Code ", 10, 20).where(first=3)
    assert where == (
        "type = $3 AND status = $4 AND cif = $5 AND lower(remark) LIKE $6 AND at >= $7 AND at < $8"
    )
    assert args == ["payout", "paid", "CIF00000001", "%code%", 10, 20]
    assert TxFilter().where() == ("TRUE", [])
    with pytest.raises(ValueError):
        TxFilter(type="refund")
    with pytest.raises(ValueError):
        TxFilter(status="x")
