"""Oversight's pure parts: office hours, the policy's settings."""

from datetime import UTC, datetime

from propfirm.policy import load_policy
from propfirm.staff.oversight import OversightPolicy, in_office, oversight_policy


def at(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()) * 1000


def test_office_hours_are_dhaka_sunday_to_thursday_nine_to_six() -> None:
    p = OversightPolicy()
    # 2026-11-08 is a Sunday; Dhaka is UTC+6
    assert in_office(at(2026, 11, 8, 3, 0), p)  # 09:00 Dhaka
    assert not in_office(at(2026, 11, 8, 2, 59), p)  # 08:59
    assert not in_office(at(2026, 11, 8, 12, 0), p)  # 18:00
    assert not in_office(at(2026, 11, 6, 6, 0), p)  # Friday 12:00: the weekend
    assert in_office(at(2026, 11, 12, 6, 0), p)  # Thursday


def test_the_shipped_policy_gives_the_oversight_settings() -> None:
    p = oversight_policy(load_policy().staff)
    assert p == OversightPolicy()
