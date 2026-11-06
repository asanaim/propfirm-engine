"""Leavers' logins: the cut-off history and its status."""

import pytest

from propfirm.staff.cutoff import _ended, cutoff_view
from propfirm.staff.records import Member, StaffError, fold_cutoffs

H = 3_600_000


def member(status: str = "active", ends_at: int | None = None) -> Member:
    return Member("STF00000002", "kc-2", "Bo", None, "staff", None, None, None, 0, ends_at,
                  status, None, None, None)  # fmt: skip


def test_whose_access_has_ended() -> None:
    assert _ended(member("left"), 10) == "left"
    assert _ended(member("graduated"), 10) == "graduated"
    assert _ended(member(ends_at=10), 10) == "ended"
    assert _ended(member(ends_at=11), 10) is None
    assert _ended(member(), 10) is None


def test_a_cut_off_is_requested_once_retried_alerted_and_done_once() -> None:
    rows = [
        ("STF00000002", "kc-2", "requested", "left", 0),
        ("STF00000002", "kc-2", "requested", "ended", 5),  # a repeat changes nothing
        ("STF00000002", "kc-2", "failed", "IdentityError: 503", 10),
        ("STF00000002", "kc-2", "alerted", None, H),
        ("STF00000002", "kc-2", "done", "keycloak", H + 60_000),
        ("STF00000002", "kc-2", "confirmed", "by hand: a-1", H + 120_000),
    ]
    (c,) = fold_cutoffs(rows)
    assert (c.reason, c.requested_at, c.done_at, c.how) == ("left", 0, H + 60_000, "keycloak")
    assert (c.last_error, c.alerted) == ("IdentityError: 503", ("alerted",))


@pytest.mark.parametrize(
    "rows",
    [
        [("STF00000002", "kc-2", "done", "keycloak", 0)],
        [("STF00000002", "kc-2", "requested", "left", 0), ("STF00000002", "kc-2", "lost", None, 1)],
    ],
)
def test_histories_no_valid_record_produces(
    rows: list[tuple[str, str, str, str | None, int]],
) -> None:
    with pytest.raises(StaffError):
        fold_cutoffs(rows)


def test_the_status_shows_late_and_overdue() -> None:
    (c,) = fold_cutoffs([("STF00000002", "kc-2", "requested", "left", 0)])
    alert, hard = H, 24 * H
    assert cutoff_view(c, H - 1, alert, hard)["status"] == "pending"
    assert cutoff_view(c, H, alert, hard)["status"] == "late"
    assert cutoff_view(c, 24 * H, alert, hard)["status"] == "overdue"
