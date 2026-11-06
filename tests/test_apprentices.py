"""Apprentices' masked views and the training gate's courses."""

from pathlib import Path

import pytest

from propfirm.staff.masking import MASK, mask_pii
from propfirm.staff.model import ROLES, add_months
from propfirm.staff.training import COURSES_FILE, Passed, TrainingError, current, load_courses


def test_personal_data_is_masked_at_any_depth_and_figures_stay() -> None:
    record = {
        "po": "PO00000001",
        "trader_amount_cents": 84_400,
        "wallet": {"wal": "WAL00000001", "address": "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8"},
        "verified": {"full_name": "Ada Trader", "date_of_birth": "1990-05-17", "result": "ok"},
        "history": [
            {"kind": "signed", "detail": {"typed_name": "Ada", "ip": "10.0.0.1", "by": "s1"}},
            {"kind": "note", "detail": {"email": None}},
        ],
        "email": "ada@example.com",
    }
    masked = mask_pii(record)
    assert masked["wallet"] == {"wal": "WAL00000001", "address": MASK}
    assert masked["verified"] == {"full_name": MASK, "date_of_birth": MASK, "result": "ok"}
    assert masked["history"][0]["detail"] == {"typed_name": MASK, "ip": MASK, "by": "s1"}
    assert masked["history"][1]["detail"] == {"email": None}  # nothing to hide
    assert (masked["po"], masked["trader_amount_cents"], masked["email"]) == (
        "PO00000001",
        84_400,
        MASK,
    )


def test_one_course_per_section() -> None:
    found = load_courses()
    assert set(found) == set(ROLES)
    assert found["kyc"].course == "kyc-aml" and found["kyc"].version == 1


def test_bad_course_files_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "courses.toml"
    path.write_text(COURSES_FILE.read_text().replace("[it]", "[sales]"))
    with pytest.raises(TrainingError):
        load_courses(path)
    path.write_text(COURSES_FILE.read_text().replace("version = 1\n\n[it]", "version = 0\n\n[it]"))
    with pytest.raises(TrainingError):
        load_courses(path)


def test_a_pass_is_valid_for_the_set_months_the_latest_counting() -> None:
    t0 = 1_790_000_000_000
    old = Passed("STF00000002", "kyc", "kyc-aml", 1, t0, "a", "n")
    new = Passed("STF00000002", "kyc", "kyc-aml", 1, add_months(t0, 6), "a", "n")
    other = Passed("STF00000002", "risk", "risk-desk", 1, t0, "a", "n")
    assert current([old], "kyc", add_months(t0, 12) - 1, 12) == old
    assert current([old], "kyc", add_months(t0, 12), 12) is None  # lapsed
    assert current([old, new, other], "kyc", add_months(t0, 13), 12) == new
    assert current([other], "kyc", t0, 12) is None
