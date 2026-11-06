"""The staff model: tiers, roles and permissions; member and grant
histories; and the rules every change follows (staff spec, 31 Oct 2026)."""

from datetime import UTC, datetime
from typing import Any

import pytest

from propfirm.staff.model import (
    DAY_MS,
    PERMISSIONS,
    POWERS,
    ROLES,
    Access,
    Tier,
    add_months,
    legacy_access,
)
from propfirm.staff.records import (
    SYSTEM,
    Grant,
    GrantEvent,
    Limits,
    Member,
    MemberEvent,
    StaffError,
    StaffRefused,
    access_of,
    check_approval,
    check_graduate,
    check_join,
    check_leave,
    check_recommend,
    check_request,
    check_revoke,
    grant_state,
    member_state,
)


def ms(year: int, month: int, day: int, hour: int = 0) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp()) * 1000


NOW = ms(2026, 11, 2, 10)
LIMITS = Limits()


def access(tier: int | None, *roles: str, subject: str = "actor", sid: str | None = None,
           powers: tuple[str, ...] = ()) -> Access:  # fmt: skip
    return Access(
        subject,
        "directory",
        None if tier is None else Tier(tier),
        frozenset(roles),
        frozenset(powers),
        sid,
        "staff",
    )


def member(
    sid: str = "STF00000009",
    subject: str = "member",
    kind: str = "staff",
    *,
    lead: str | None = None,
    mentor: str | None = None,
    ends_at: int | None = None,
    status: str = "active",
    recommended_by: str | None = None,
) -> Member:
    return Member(
        sid, subject, "A Person", None, kind, "ops", lead, mentor, NOW - DAY_MS, ends_at,
        status, None, None, recommended_by,
    )  # fmt: skip


def grant(
    kind: str = "tier",
    value: int | str = 2,
    *,
    status: str = "requested",
    by: str = "requester",
    tier: int | None = 3,
    starts_at: int = NOW,
    ends_at: int | None = None,
    approved_at: int | None = None,
    grt: str = "GRT00000001",
    sid: str = "STF00000009",
) -> Grant:
    return Grant(
        grt, sid, kind, value, starts_at, ends_at, status, by, tier, NOW, "why",
        "approver" if status != "requested" else None, approved_at, None, None,
    )  # fmt: skip


def refused(code: str, call: Any, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(StaffRefused) as caught:
        call(*args, **kwargs)
    assert caught.value.code == code, caught.value


# --- calendar months (explicit rounding) ------------------------------------------------


@pytest.mark.parametrize(
    ("start", "months", "end"),
    [
        (ms(2026, 1, 31, 12), 1, ms(2026, 2, 28, 12)),  # past the month's end: its last day
        (ms(2028, 1, 31), 1, ms(2028, 2, 29)),  # a leap year
        (ms(2026, 11, 2, 10), 3, ms(2027, 2, 2, 10)),  # over the year end
        (ms(2026, 8, 31), 6, ms(2027, 2, 28)),
        (ms(2026, 5, 15), 0, ms(2026, 5, 15)),
    ],
)
def test_add_months_moves_calendar_months_in_utc(start: int, months: int, end: int) -> None:
    assert add_months(start, months) == end


def test_add_months_keeps_the_milliseconds() -> None:
    assert add_months(ms(2026, 3, 10) + 999, 3) == ms(2026, 6, 10) + 999


# --- permissions ------------------------------------------------------------------------


def test_a_role_is_needed_below_admin_and_admins_do_any_role() -> None:
    assert not access(2).has("payout.pass")  # no payouts role
    assert access(2, "payouts").has("payout.pass")
    assert not access(1, "payouts").has("payout.pass")  # T1 never passes
    assert access(4).has("payout.pass") and access(5).has("onboarding.decide")


def test_no_amount_is_approved_below_admin() -> None:
    every_role = ROLES
    assert not access(3, *every_role).has("payout.authorise")
    assert not access(3, *every_role).has("case.authorise")
    assert access(4).has("payout.authorise") and access(4).has("case.authorise")
    assert "payout.authorise" not in POWERS and "case.authorise" not in POWERS
    # and a granted admin power gives only itself
    lead = access(3, powers=("settings.change",))
    assert lead.has("settings.change") and not lead.has("news.cancel")


def test_powers_are_admin_permissions() -> None:
    assert all(PERMISSIONS[p].tier == Tier.ADMIN for p in POWERS)


def test_an_apprentice_or_no_tier_has_no_permission() -> None:
    assert access(0, *ROLES).permissions() == []
    assert access(None, *ROLES).permissions() == []


def test_a_funded_review_takes_a_t2_on_the_risk_desk() -> None:
    """[decided 6 Nov 2026]: fewer people can approve a funded account."""
    assert not access(1, "risk").has("progression.decide")
    assert not access(2, "kyc").has("progression.decide")
    assert access(2, "risk").has("progression.decide")


def test_approvals_are_marked_for_step_up() -> None:
    approvals = {n for n, p in PERMISSIONS.items() if p.approval}
    assert {"payout.pass", "payout.authorise", "case.authorise", "settings.change"} <= approvals
    assert "staff.view" not in approvals and "payout.deny" not in approvals


def test_keycloak_roles_map_as_before() -> None:
    staff = legacy_access("s", frozenset({"staff"}))
    admin = legacy_access("a", frozenset({"admin", "staff"}))
    assert (staff.tier, staff.roles, staff.source) == (Tier.SPECIALIST, frozenset(ROLES), "legacy")
    assert staff.has("payout.pass") and not staff.has("payout.authorise")
    assert admin.tier == Tier.ADMIN and admin.has("news.cancel")
    assert legacy_access("t", frozenset({"trader"})).tier is None


# --- member history ---------------------------------------------------------------------


def joined(**detail: Any) -> MemberEvent:
    base = {"subject": "kc-1", "name": "Ada", "kind": "apprentice", "mentor": "STF00000002"}
    return MemberEvent("APP00000001", "joined", NOW, {**base, **detail})


def test_a_member_is_rebuilt_from_its_history() -> None:
    sid = "APP00000001"
    events = [
        joined(ends_at=NOW + 100 * DAY_MS),
        MemberEvent(sid, "moved", NOW + 1, {"team": "kyc", "lead": "STF00000003"}),
        MemberEvent(sid, "recommended", NOW + 2, {"by": "mentor-subject"}),
        MemberEvent(sid, "graduated", NOW + 3, {"by": "admin", "to_sid": "STF00000004"}),
    ]
    m = member_state(sid, events)
    assert (m.team, m.lead, m.recommended_by, m.status, m.to_sid) == (
        "kyc", "STF00000003", "mentor-subject", "graduated", "STF00000004",
    )  # fmt: skip
    assert not m.active(NOW + 4)


@pytest.mark.parametrize(
    "events",
    [
        [],
        [joined(), joined()],
        [joined(), MemberEvent("APP00000001", "graduated", NOW, {"by": "a", "to_sid": "S"})],
        [joined(kind="staff"), MemberEvent("APP00000001", "recommended", NOW, {"by": "m"})],
        [joined(), MemberEvent("APP00000001", "left", NOW, {"by": "a"}),
         MemberEvent("APP00000001", "moved", NOW, {"team": None, "lead": None})],
    ],
)  # fmt: skip
def test_member_histories_no_valid_record_produces(events: list[MemberEvent]) -> None:
    with pytest.raises(StaffError):
        member_state("APP00000001", events)


def test_an_end_date_ends_access_by_itself() -> None:
    m = member(kind="contractor", ends_at=NOW + DAY_MS)
    assert m.active(NOW) and not m.active(NOW + DAY_MS)


# --- grant history ----------------------------------------------------------------------


def requested(**detail: Any) -> GrantEvent:
    base = {"sid": "STF00000009", "grant": "tier", "value": 2, "starts_at": NOW, "by": "lead",
            "tier": 3, "reason": "promotion"}  # fmt: skip
    return GrantEvent("GRT00000001", "requested", NOW, {**base, **detail})


def ev(kind: str, by: str, ts: int = NOW) -> GrantEvent:
    return GrantEvent("GRT00000001", kind, ts, {"by": by, "reason": "r"})


def test_a_grant_is_rebuilt_from_its_history() -> None:
    g = grant_state("GRT00000001", [requested(), ev("approved", "admin", NOW + 5)])
    assert (g.status, g.approved_by, g.approved_at, g.requester_tier) == (
        "approved", "admin", NOW + 5, 3,
    )  # fmt: skip
    revoked = grant_state(
        "GRT00000001", [requested(), ev("approved", "admin"), ev("revoked", "owner")]
    )
    assert (revoked.status, revoked.ended_by) == ("revoked", "owner")


@pytest.mark.parametrize(
    "events",
    [
        [],
        [requested(), requested()],
        [requested(grant="everything")],
        [requested(), ev("approved", "lead")],  # the requester approves
        [requested(), ev("revoked", "admin")],  # not approved yet
        [requested(), ev("denied", "admin"), ev("approved", "admin")],
        [requested(), ev("approved", "admin"), ev("approved", "owner")],
    ],
)
def test_grant_histories_no_valid_record_produces(events: list[GrantEvent]) -> None:
    with pytest.raises(StaffError):
        grant_state("GRT00000001", events)


def test_the_system_grant_of_an_owner_is_its_own_approver() -> None:
    g = grant_state(
        "GRT00000001", [requested(by=SYSTEM, value=5, tier=None), ev("approved", SYSTEM)]
    )
    assert g.status == "approved" and g.requester_tier is None


def test_a_grant_is_in_force_between_its_start_and_end() -> None:
    g = grant("role", "kyc", status="approved", starts_at=NOW + 10, ends_at=NOW + 20)
    assert [g.in_force(t) for t in (NOW, NOW + 10, NOW + 19, NOW + 20)] == [
        False, True, True, False,
    ]  # fmt: skip
    assert g.state(NOW + 20) == "expired" and g.state(NOW) == "approved"


# --- access of a member -----------------------------------------------------------------


def test_access_is_the_latest_tier_raised_by_acting_cover_with_roles_and_powers() -> None:
    m = member()
    grants = [
        grant("tier", 3, status="approved", approved_at=NOW - 50, grt="GRT00000001"),
        grant("tier", 1, status="approved", approved_at=NOW - 10, grt="GRT00000002"),  # demoted
        grant("role", "kyc", status="approved", approved_at=NOW, grt="GRT00000003"),
        grant("role", "risk", status="requested", grt="GRT00000004"),  # not approved
        grant("power", "news.cancel", status="approved", ends_at=NOW + DAY_MS, grt="GRT00000005"),
        grant("acting", 2, status="approved", ends_at=NOW + DAY_MS, grt="GRT00000006"),
        grant("tier", 1, status="approved", sid="STF00000001", grt="GRT00000007"),  # another
    ]
    a = access_of(m, grants, NOW)
    assert (a.tier, a.roles, a.powers, a.sid) == (
        Tier.SPECIALIST, frozenset({"kyc"}), frozenset({"news.cancel"}), "STF00000009",
    )  # fmt: skip
    assert a.grants == ("GRT00000001", "GRT00000002", "GRT00000003", "GRT00000005", "GRT00000006")
    later = access_of(m, grants, NOW + DAY_MS)  # the power and the cover have ended
    assert (later.tier, later.powers) == (Tier.ASSOCIATE, frozenset())


def test_a_leaver_or_a_member_with_no_tier_has_no_access() -> None:
    approved = [grant("tier", 3, status="approved", approved_at=NOW)]
    assert access_of(member(status="left"), approved, NOW).tier is None
    assert access_of(member(), [grant("tier", 3)], NOW).tier is None  # only requested
    assert access_of(member(ends_at=NOW), approved, NOW).tier is None  # end date passed


# --- requests ---------------------------------------------------------------------------


def req(actor: Access, m: Member, kind: str, value: int | str, ends: int | None = None,
        m_tier: int | None = 1) -> None:  # fmt: skip
    check_request(actor, m, access(m_tier, subject=m.subject), kind, value, NOW, ends, NOW, LIMITS)


def test_a_lead_or_above_requests_and_nobody_for_themselves() -> None:
    req(access(3), member(), "tier", 2)
    refused("forbidden", req, access(2, *ROLES), member(), "role", "kyc")
    refused("own_access", req, access(4, subject="member"), member(), "role", "kyc")


def test_tier_requests_follow_the_kind_and_owners_alone_ask_for_t4_or_t5() -> None:
    refused("invalid", req, access(3), member(kind="apprentice"), "tier", 1)
    req(access(3), member(kind="apprentice"), "tier", 0)
    refused("invalid", req, access(3), member(kind="contractor"), "tier", 3)
    refused("forbidden", req, access(4), member(), "tier", 4)
    req(access(5), member(), "tier", 4)
    refused("invalid", req, access(3), member(), "tier", 6)
    refused("invalid", req, access(3), member(), "tier", 2, NOW + DAY_MS)  # a tier has no end


def test_an_owner_tier_is_not_changed_through_the_api() -> None:
    refused("forbidden", req, access(5), member(), "tier", 4, None, 5)


def test_an_admin_is_demoted_only_by_two_owners() -> None:
    refused("forbidden", req, access(4), member(), "tier", 3, None, 4)
    req(access(5), member(), "tier", 3, None, 4)
    demotion = grant("tier", 3, tier=5)
    refused("forbidden", approve, access(4), demotion, None, 4)
    approve(access(5), demotion, None, 4)


def test_an_admin_power_has_an_end_within_90_days_and_goes_below_admin_only() -> None:
    req(access(4), member(), "power", "settings.change", NOW + 90 * DAY_MS)
    refused("invalid", req, access(4), member(), "power", "settings.change", NOW + 91 * DAY_MS)
    refused("invalid", req, access(4), member(), "power", "settings.change")  # no end
    refused("invalid", req, access(4), member(), "power", "payout.authorise", NOW + DAY_MS)
    refused("invalid", req, access(5), member(), "power", "news.cancel", NOW + DAY_MS, 4)
    contractor = member(kind="contractor", ends_at=NOW + 99 * DAY_MS)
    refused("invalid", req, access(4), contractor, "power", "news.cancel", NOW + DAY_MS)


def test_acting_cover_ends_within_30_days_up_to_the_requesters_own_tier() -> None:
    req(access(3), member(), "acting", 3, NOW + 30 * DAY_MS)
    refused("invalid", req, access(3), member(), "acting", 3, NOW + 31 * DAY_MS)
    refused("invalid", req, access(5), member(), "acting", 4, NOW + DAY_MS)
    refused("invalid", req, access(3), member(), "acting", 2)  # no end


def test_a_request_ends_in_the_future() -> None:
    refused("invalid", req, access(3), member(), "role", "kyc", NOW)


# --- approvals --------------------------------------------------------------------------


def approve(actor: Access, g: Grant, m: Member | None = None, m_tier: int | None = 1) -> None:
    check_approval(actor, m or member(), access(m_tier, subject="member"), g, NOW)


def test_the_requester_and_the_grantee_never_approve() -> None:
    refused("four_eyes", approve, access(5, subject="requester"), grant())
    refused("own_access", approve, access(5, subject="member"), grant())


def test_tiers_up_to_t3_take_an_admin_and_t4_or_t5_two_owners() -> None:
    approve(access(4), grant("tier", 3))
    refused("forbidden", approve, access(3), grant("tier", 2))
    refused("forbidden", approve, access(4), grant("tier", 4, tier=5))
    approve(access(5), grant("tier", 4, tier=5))


def test_an_admin_power_takes_two_admins_or_an_owner() -> None:
    from_admin = grant("power", "news.cancel", tier=4, ends_at=NOW + DAY_MS)
    from_lead = grant("power", "news.cancel", tier=3, ends_at=NOW + DAY_MS)
    approve(access(4), from_admin)
    refused("forbidden", approve, access(4), from_lead)
    approve(access(5), from_lead)


def test_a_decided_or_ended_request_is_not_approved() -> None:
    refused("not_allowed", approve, access(5), grant(status="approved"))
    late = grant("acting", 2, starts_at=NOW - 10, ends_at=NOW)
    refused("not_allowed", approve, access(5), late)


# --- revoking, leavers ------------------------------------------------------------------


def test_revoking_needs_an_admin_or_the_lead_for_roles_and_cover() -> None:
    role = grant("role", "kyc", status="approved")
    check_revoke(access(4), member(), role, NOW)
    check_revoke(access(3, sid="STF00000002"), member(lead="STF00000002"), role, NOW)
    refused("forbidden", check_revoke, access(3, sid="STF00000005"), member(lead="STF00000002"),
            role, NOW)  # fmt: skip
    tier = grant("tier", 2, status="approved")
    refused("forbidden", check_revoke, access(3, sid="STF00000002"), member(lead="STF00000002"),
            tier, NOW)  # fmt: skip
    refused("own_access", check_revoke, access(4, subject="member"), member(), role, NOW)


def test_owner_and_admin_tiers() -> None:
    refused("forbidden", check_revoke, access(5), member(), grant("tier", 5, status="approved"),
            NOW)  # fmt: skip
    refused("forbidden", check_revoke, access(4), member(), grant("tier", 4, status="approved"),
            NOW)  # fmt: skip
    check_revoke(access(5), member(), grant("tier", 4, status="approved"), NOW)


def test_a_leaver_is_marked_by_someone_of_a_higher_tier_never_an_owner() -> None:
    check_leave(access(4), member(), access(3, subject="member"), NOW)
    refused("forbidden", check_leave, access(4), member(), access(4, subject="member"), NOW)
    refused("forbidden", check_leave, access(5), member(), access(5, subject="member"), NOW)
    refused("not_allowed", check_leave, access(5), member(status="left"), access(None), NOW)


# --- joiners and apprentices ------------------------------------------------------------


def join(kind: str, ends_at: int | None, *, lead: Member | None = None,
         mentor: Member | None = None, mentor_tier: int | None = None) -> None:  # fmt: skip
    mentor_access = None if mentor_tier is None else access(mentor_tier)
    check_join(access(3), kind, "new", ends_at, lead, mentor, mentor_access, NOW, LIMITS)


def test_an_apprentice_term_is_3_to_6_months_with_a_t2_or_t3_mentor() -> None:
    mentor = member("STF00000002", "mentor")
    join("apprentice", add_months(NOW, 3), mentor=mentor, mentor_tier=2)
    join("apprentice", add_months(NOW, 6), mentor=mentor, mentor_tier=3)
    refused("invalid", join, "apprentice", add_months(NOW, 3) - 1, mentor=mentor, mentor_tier=2)
    refused("invalid", join, "apprentice", add_months(NOW, 6) + 1, mentor=mentor, mentor_tier=2)
    refused("invalid", join, "apprentice", add_months(NOW, 4), mentor=mentor, mentor_tier=4)
    refused("invalid", join, "apprentice", add_months(NOW, 4))  # no mentor


def test_contractors_need_a_sponsor_and_an_end_staff_have_no_end() -> None:
    sponsor = member("STF00000002", "sponsor")
    join("contractor", NOW + DAY_MS, lead=sponsor)
    refused("invalid", join, "contractor", NOW + DAY_MS)
    refused("invalid", join, "contractor", None, lead=sponsor)
    join("staff", None)
    refused("invalid", join, "staff", NOW + DAY_MS)
    refused("invalid", join, "staff", None, mentor=sponsor, mentor_tier=2)


def test_only_a_lead_or_above_adds_staff_and_never_themselves() -> None:
    refused("forbidden", check_join, access(2, *ROLES), "staff", "new", None, None, None, None,
            NOW, LIMITS)  # fmt: skip
    refused("own_access", check_join, access(3, subject="new"), "staff", "new", None, None, None,
            None, NOW, LIMITS)  # fmt: skip


def test_the_mentor_recommends_then_another_admin_approves_graduation() -> None:
    apprentice = member("APP00000001", "app", "apprentice", mentor="STF00000002",
                        ends_at=add_months(NOW, 3))  # fmt: skip
    check_recommend(access(2, sid="STF00000002"), apprentice, NOW)
    refused("forbidden", check_recommend, access(3, sid="STF00000003"), apprentice, NOW)
    refused("not_allowed", check_graduate, access(4), apprentice, "app-staff", NOW)
    recommended = member("APP00000001", "app", "apprentice", mentor="STF00000002",
                         ends_at=add_months(NOW, 3), recommended_by="mentor")  # fmt: skip
    check_graduate(access(4), recommended, "app-staff", NOW)
    refused("four_eyes", check_graduate, access(4, subject="mentor"), recommended, "s", NOW)
    refused("forbidden", check_graduate, access(3), recommended, "app-staff", NOW)
    refused("invalid", check_graduate, access(4), recommended, "app", NOW)
    refused("not_allowed", check_recommend, access(2, sid="STF00000002"), recommended, NOW)
