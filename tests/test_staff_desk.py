"""The staff page's pure parts: duty sheets, who a notification reaches."""

from pathlib import Path

import pytest

from propfirm.staff.desk import (
    DUTIES_FILE,
    SHEETS,
    DeskError,
    audience_valid,
    load_duties,
    reaches,
    sheets_for,
)
from propfirm.staff.model import ROLES, Access, Tier


def access(tier: int | None, *roles: str, sid: str | None = "STF00000002") -> Access:
    return Access("u", "directory", None if tier is None else Tier(tier), frozenset(roles), sid=sid)


def test_the_shipped_duty_sheets_cover_every_role_and_tier_sheet() -> None:
    duties = load_duties()
    assert set(duties) == set(SHEETS) == {*ROLES, "apprentice", "admin", "owner"}
    assert all(sheet.version >= 1 and sheet.duties for sheet in duties.values())
    # the decided rules are written into the duties people acknowledge
    assert any("admin authorises" in d for d in duties["payouts"].duties)
    assert any("second factor" in d for d in duties["it"].duties)


@pytest.mark.parametrize(
    "text",
    [
        "[support]\nversion = 1\ntitle = 'S'\nduties = ['x']\n",  # sheets missing
        DUTIES_FILE.read_text().replace("[owner]\nversion = 1", "[owner]\nversion = 0"),
        DUTIES_FILE.read_text().replace('title = "Owner"', 'title = ""'),
        DUTIES_FILE.read_text().replace("[it]\nversion = 1", "[it]\nversion = 1\nextra = 2"),
    ],
)
def test_bad_duty_sheets_are_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "duties.toml"
    path.write_text(text)
    with pytest.raises(DeskError):
        load_duties(path)


def test_the_sheets_that_apply_follow_tier_and_roles() -> None:
    assert sheets_for(access(2, "payouts", "kyc")) == ["kyc", "payouts"]
    assert sheets_for(access(0, "kyc")) == ["apprentice", "kyc"]
    assert sheets_for(access(4, "kyc")) == ["admin"]
    assert sheets_for(access(5)) == ["owner", "admin"]
    assert sheets_for(access(None, "kyc")) == []


def test_a_notification_reaches_the_holders_of_its_permission_or_one_member() -> None:
    assert reaches("news.desk", access(1, "risk"))
    assert reaches("news.desk", access(4))  # admins do any role's work
    assert not reaches("news.desk", access(2, "kyc"))
    assert reaches("member:STF00000002", access(1))
    assert not reaches("member:STF00000003", access(1))
    assert audience_valid("payout.authorise") and audience_valid("member:APP00000001")
    assert not audience_valid("everyone") and not audience_valid("member:CIF00000001")
