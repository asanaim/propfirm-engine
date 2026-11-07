"""Pre-employment screening, pure part (propfirm.staff.screening)."""

from pathlib import Path

import pytest

from propfirm.staff.screening import (
    CheckRecord,
    Decision,
    ScreeningError,
    checks,
    load_checks,
    status,
)

REQ = ["identity", "references"]
DAY = 86_400_000


def rec(check: str, ts: int, result: str = "pass", by: str = "lead") -> CheckRecord:
    return CheckRecord("STF00000009", check, result, "ref", by, ts)


def dec(decision: str, ts: int, until: int | None = None, by: str = "admin") -> Decision:
    return Decision("STF00000009", decision, "note", by, ts, until)


def test_the_shipped_checks() -> None:
    ids = [c.id for c in checks()]
    assert ids == ["identity", "right_to_work", "address", "sanctions_pep", "background",
                   "references", "conflicts", "trading_accounts", "agreements"]  # fmt: skip


def test_a_bad_checks_file_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "s.toml"
    one = '[[check]]\nid = "a"\ntitle = "A"\ntext = "x"\n'
    f.write_text(one + one)
    with pytest.raises(ScreeningError, match="unique"):
        load_checks(f)


def test_a_screening_runs_incomplete_pending_approved_expired() -> None:
    assert status(REQ, [], [], 10).status == "incomplete"
    one = status(REQ, [rec("identity", 1)], [], 10)
    assert (one.status, one.missing) == ("incomplete", ("references",))
    failed = status(REQ, [rec("identity", 1), rec("references", 2, "fail")], [], 10)
    assert failed.missing == ("references",)
    both = [rec("identity", 1), rec("references", 3)]
    pending = status(REQ, both, [], 10)
    assert (pending.status, pending.recorders) == ("pending", frozenset({"lead"}))
    approved = [dec("approved", 5, 5 + 365 * DAY)]
    now = status(REQ, both, approved, 6)
    assert (now.status, now.valid_until, now.round) == ("approved", 5 + 365 * DAY, {})
    later = status(REQ, both, approved, 5 + 366 * DAY)
    assert (later.status, later.valid_until) == ("expired", None)


def test_the_yearly_round_starts_after_the_last_decision() -> None:
    first = [rec("identity", 1), rec("references", 2)]
    decisions = [dec("approved", 5, 1000)]
    renewal = [*first, rec("identity", 900, by="lead2"), rec("references", 901, by="lead2")]
    sc = status(REQ, renewal, decisions, 950)
    assert sc.status == "approved"  # still valid while the renewal waits
    assert set(sc.round) == {"identity", "references"} and sc.recorders == {"lead2"}
    assert status(REQ, renewal, decisions, 1001).status == "pending"


def test_a_rejection_closes_the_round() -> None:
    both = [rec("identity", 1), rec("references", 2)]
    sc = status(REQ, both, [dec("rejected", 5)], 6)
    assert (sc.status, sc.round, sc.valid_until) == ("rejected", {}, None)
    again = status(REQ, [*both, rec("identity", 7)], [dec("rejected", 5)], 8)
    assert again.status == "incomplete" and again.missing == ("references",)
