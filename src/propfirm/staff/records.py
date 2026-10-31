"""Staff records and grants, rebuilt from append-only histories, and the
rules every change follows (staff spec, 31 Oct 2026).

A member (STF00000001; an apprentice APP00000001, a separate identity that
is never reused) is a person and their place in the firm: name, kind
(staff, apprentice, contractor), team, lead, mentor and end date. What they
may do comes only from grants (GRT00000001):

- tier (a promotion): requested by T3+, approved by T4+; T4 and T5 by two
  owners (owner requests, another owner approves); lasts until another
  tier is approved;
- role (a functional role): requested by T3+, approved by T4+; optional end;
- power (one admin power, for T3 or below): requested by T3+; approved by
  a second admin, or by an owner when the requester is below T4; ends
  within `power_max_days` (90);
- acting (a higher tier, up to the requester's own, for cover): requested
  by T3+, approved by T4+; ends within `acting_max_days` (30).

Separation of duties, always: nobody requests, approves or revokes their
own access; the approver is never the requester. Grants end on their own
(no wall clock: the end is compared with the `now` each caller passes). A
member who leaves, or whose end date passes, has no access at all.

Owners: the configured owners are recorded once at start-up (system
grants, the one exception to four eyes). An owner's tier is never revoked
or marked as left through the API: who owns the firm changes outside it.

Apprentices [decided 31 Oct 2026]: a term of 3 to 6 months (policy), a
named T2 or T3 mentor, tier T0; the mentor recommends, then an admin
(not the recommender) approves graduation to a new T1 staff member with
its own login, linked to the apprentice record.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from propfirm.staff.model import (
    DAY_MS,
    KINDS,
    POWERS,
    ROLES,
    Access,
    Tier,
    add_months,
    no_access,
)

MEMBER_KINDS = ("joined", "moved", "recommended", "graduated", "left")
GRANT_KINDS = ("requested", "approved", "denied", "cancelled", "revoked", "superseded")
GRANTS = ("tier", "role", "power", "acting")
GRANT_STATUSES = ("requested", "approved", "denied", "cancelled", "revoked", "superseded")
SYSTEM = "system:config"  # the owners named in configuration


class StaffError(ValueError):
    """Events that no valid record produces."""


class StaffRefused(Exception):
    """A change the rules refuse; `code` says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Limits:
    """The policy's numbers (`[staff]`)."""

    apprentice_min_months: int = 3
    apprentice_max_months: int = 6
    power_max_days: int = 90
    acting_max_days: int = 30


# --- members --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemberEvent:
    sid: str
    kind: str
    ts: int
    detail: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Member:
    sid: str
    subject: str  # the Keycloak login
    name: str
    email: str | None
    kind: str
    team: str | None
    lead: str | None  # STF of their lead (a contractor's sponsor)
    mentor: str | None  # STF of an apprentice's mentor
    starts_at: int
    ends_at: int | None  # apprentices and contractors: required
    status: str  # active, left, graduated
    from_sid: str | None  # the apprentice record a staff member graduated from
    to_sid: str | None  # the staff record an apprentice graduated to
    recommended_by: str | None  # subject of the mentor who recommended graduation

    def active(self, now: int) -> bool:
        return self.status == "active" and (self.ends_at is None or now < self.ends_at)


def member_state(sid: str, events: Iterable[MemberEvent]) -> Member:
    m: Member | None = None
    for e in events:
        if e.sid != sid or e.kind not in MEMBER_KINDS:
            raise StaffError(f"{sid}: unexpected event {e.sid} {e.kind}")
        d = e.detail
        if e.kind == "joined":
            if m is not None:
                raise StaffError(f"{sid}: joined twice")
            m = Member(
                sid,
                str(d["subject"]),
                str(d["name"]),
                d.get("email"),
                str(d["kind"]),
                d.get("team"),
                d.get("lead"),
                d.get("mentor"),
                e.ts,
                d.get("ends_at"),
                "active",
                d.get("from_sid"),
                None,
                None,
            )
            continue
        if m is None or m.status != "active":
            raise StaffError(f"{sid}: {e.kind} on a record not active")
        match e.kind:
            case "moved":
                m = replace(m, team=d.get("team"), lead=d.get("lead"))
            case "recommended":
                if m.kind != "apprentice":
                    raise StaffError(f"{sid}: only an apprentice is recommended")
                m = replace(m, recommended_by=str(d["by"]))
            case "graduated":
                if m.recommended_by is None:
                    raise StaffError(f"{sid}: graduated without a recommendation")
                m = replace(m, status="graduated", to_sid=str(d["to_sid"]))
            case "left":
                m = replace(m, status="left")
    if m is None:
        raise StaffError(f"{sid}: no joined event")
    return m


# --- grants ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GrantEvent:
    grt: str
    kind: str
    ts: int
    detail: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Grant:
    grt: str
    sid: str
    grant: str  # tier, role, power, acting
    value: int | str  # a tier (int) or a role or power name
    starts_at: int
    ends_at: int | None
    status: str
    requested_by: str
    requester_tier: int | None  # None: the system
    requested_at: int
    reason: str
    approved_by: str | None
    approved_at: int | None
    ended_by: str | None  # denied, cancelled, revoked or superseded by
    ended_at: int | None

    def in_force(self, now: int) -> bool:
        return (
            self.status == "approved"
            and self.starts_at <= now
            and (self.ends_at is None or now < self.ends_at)
        )

    def state(self, now: int) -> str:
        """The status, with `expired` for an approved grant past its end."""
        if self.status == "approved" and self.ends_at is not None and now >= self.ends_at:
            return "expired"
        return self.status


def grant_state(grt: str, events: Iterable[GrantEvent]) -> Grant:
    g: Grant | None = None
    for e in events:
        if e.grt != grt or e.kind not in GRANT_KINDS:
            raise StaffError(f"{grt}: unexpected event {e.grt} {e.kind}")
        d = e.detail
        if e.kind == "requested":
            if g is not None:
                raise StaffError(f"{grt}: requested twice")
            kind = str(d["grant"])
            if kind not in GRANTS:
                raise StaffError(f"{grt}: grant {kind!r}")
            tier = d.get("tier")
            g = Grant(
                grt,
                str(d["sid"]),
                kind,
                d["value"],
                int(d["starts_at"]),
                d.get("ends_at"),
                "requested",
                str(d["by"]),
                int(tier) if tier is not None else None,
                e.ts,
                str(d["reason"]),
                None,
                None,
                None,
                None,
            )
            continue
        if g is None:
            raise StaffError(f"{grt}: {e.kind} before requested")
        by = str(d["by"])
        match e.kind:
            case "approved":
                if g.status != "requested" or (by == g.requested_by and by != SYSTEM):
                    raise StaffError(f"{grt}: approved from {g.status} by {by}")
                g = replace(g, status="approved", approved_by=by, approved_at=e.ts)
            case "denied" | "cancelled":
                if g.status != "requested":
                    raise StaffError(f"{grt}: {e.kind} from {g.status}")
                g = replace(g, status=e.kind, ended_by=by, ended_at=e.ts)
            case "revoked" | "superseded":
                if g.status != "approved":
                    raise StaffError(f"{grt}: {e.kind} from {g.status}")
                g = replace(g, status=e.kind, ended_by=by, ended_at=e.ts)
    if g is None:
        raise StaffError(f"{grt}: no requested event")
    return g


def access_of(member: Member, grants: Sequence[Grant], now: int) -> Access:
    """A directory member's access now: the latest approved tier (raised by
    an acting grant in force), the roles and powers in force."""
    if not member.active(now):
        return no_access(member.subject, "directory", member.sid)
    mine = [g for g in grants if g.sid == member.sid and g.in_force(now)]
    tiers = sorted(
        (g for g in mine if g.grant == "tier"),
        key=lambda g: (g.approved_at or 0, g.grt),
    )
    if not tiers:
        return no_access(member.subject, "directory", member.sid)
    tier = int(tiers[-1].value)
    for g in mine:
        if g.grant == "acting":
            tier = max(tier, int(g.value))
    return Access(
        member.subject,
        "directory",
        Tier(tier),
        frozenset(str(g.value) for g in mine if g.grant == "role"),
        frozenset(str(g.value) for g in mine if g.grant == "power"),
        member.sid,
        member.kind,
        tuple(sorted(g.grt for g in mine)),
    )


# --- the rules ------------------------------------------------------------------------


def _need(ok: bool, code: str, message: str) -> None:
    if not ok:
        raise StaffRefused(code, message)


def _tier(access: Access) -> int:
    return -1 if access.tier is None else int(access.tier)


def check_join(
    actor: Access,
    kind: str,
    subject: str,
    ends_at: int | None,
    lead: Member | None,
    mentor: Member | None,
    mentor_access: Access | None,
    now: int,
    limits: Limits,
) -> None:
    _need(actor.has("staff.manage"), "forbidden", "adding staff needs T3 or above")
    _need(actor.subject != subject, "own_access", "nobody adds themselves")
    _need(kind in KINDS, "invalid", f"kind must be one of {KINDS}")
    _need(lead is None or lead.active(now), "invalid", "the lead is not an active member")
    if kind == "apprentice":
        _need(
            mentor is not None
            and mentor.active(now)
            and mentor_access is not None
            and mentor_access.tier in (Tier.SPECIALIST, Tier.LEAD),
            "invalid",
            "an apprentice needs an active T2 or T3 mentor",
        )
        earliest = add_months(now, limits.apprentice_min_months)
        latest = add_months(now, limits.apprentice_max_months)
        _need(
            ends_at is not None and earliest <= ends_at <= latest,
            "invalid",
            f"an apprentice's term is {limits.apprentice_min_months} to "
            f"{limits.apprentice_max_months} months: ends_at between {earliest} and {latest}",
        )
    else:
        _need(mentor is None, "invalid", "only an apprentice has a mentor")
    if kind == "contractor":
        _need(ends_at is not None and ends_at > now, "invalid", "a contractor needs an end date")
        _need(lead is not None, "invalid", "a contractor needs a sponsor (lead)")
    if kind == "staff":
        _need(ends_at is None, "invalid", "staff have no end date (a leaver is marked left)")


def check_request(
    actor: Access,
    member: Member,
    member_access: Access,
    grant: str,
    value: int | str,
    starts_at: int,
    ends_at: int | None,
    now: int,
    limits: Limits,
) -> None:
    _need(actor.has("staff.manage"), "forbidden", "requesting a grant needs T3 or above")
    _need(actor.subject != member.subject, "own_access", "nobody requests their own access")
    _need(member.active(now), "invalid", f"{member.sid} is not active")
    _need(grant in GRANTS, "invalid", f"grant must be one of {GRANTS}")
    _need(
        starts_at >= 0 and (ends_at is None or ends_at > max(starts_at, now)),
        "invalid",
        "the end must be after the start and in the future",
    )
    actor_tier = _tier(actor)
    if grant in ("tier", "acting"):
        _need(
            isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 5,
            "invalid",
            "a tier is 0 to 5",
        )
    match grant:
        case "tier":
            assert isinstance(value, int)
            _need(ends_at is None, "invalid", "a tier lasts until another is approved")
            allowed = {"apprentice": (0, 0), "contractor": (1, 2), "staff": (1, 5)}[member.kind]
            _need(
                allowed[0] <= value <= allowed[1],
                "invalid",
                f"a {member.kind} has tier T{allowed[0]} to T{allowed[1]}",
            )
            admin = member_access.tier is not None and member_access.tier >= Tier.ADMIN
            if value >= Tier.ADMIN or admin:
                _need(
                    actor_tier == Tier.OWNER,
                    "forbidden",
                    "only an owner asks to make or change an admin's or owner's tier",
                )
            _need(
                member_access.tier != Tier.OWNER,
                "forbidden",
                "an owner's tier is not changed through the API",
            )
        case "role":
            _need(value in ROLES, "invalid", f"role must be one of {ROLES}")
        case "power":
            _need(value in POWERS, "invalid", f"power must be one of {sorted(POWERS)}")
            _need(member.kind == "staff", "invalid", "admin powers go to staff only")
            _need(
                member_access.tier is not None and member_access.tier < Tier.ADMIN,
                "invalid",
                "admins hold every admin power already",
            )
            _need(
                ends_at is not None and ends_at - starts_at <= limits.power_max_days * DAY_MS,
                "invalid",
                f"an admin power ends within {limits.power_max_days} days",
            )
        case "acting":
            assert isinstance(value, int)
            _need(member.kind == "staff", "invalid", "acting cover goes to staff only")
            _need(1 <= value <= Tier.LEAD, "invalid", "acting cover is T1 to T3")
            _need(value <= actor_tier, "forbidden", "nobody delegates above their own tier")
            _need(
                ends_at is not None and ends_at - starts_at <= limits.acting_max_days * DAY_MS,
                "invalid",
                f"acting cover ends within {limits.acting_max_days} days",
            )


def check_approval(
    actor: Access, member: Member, member_access: Access, grant: Grant, now: int
) -> None:
    """Approve or deny: the same authority."""
    _need(grant.status == "requested", "not_allowed", f"{grant.grt} is {grant.status}")
    _need(actor.subject != member.subject, "own_access", "nobody approves their own access")
    _need(actor.subject != grant.requested_by, "four_eyes", "the requester does not approve")
    _need(grant.ends_at is None or now < grant.ends_at, "not_allowed", f"{grant.grt} has ended")
    tier = _tier(actor)
    match grant.grant:
        case "tier" if int(grant.value) >= Tier.ADMIN or (
            member_access.tier is not None and member_access.tier >= Tier.ADMIN
        ):
            _need(tier == Tier.OWNER, "forbidden", "an admin's or owner's tier needs two owners")
        case "power" if (grant.requester_tier or 0) < Tier.ADMIN:
            _need(tier == Tier.OWNER, "forbidden", "an admin power needs two admins or an owner")
        case _:
            _need(tier >= Tier.ADMIN, "forbidden", "approving a grant needs T4 or above")


def check_revoke(actor: Access, member: Member, grant: Grant, now: int) -> None:
    _need(grant.status == "approved", "not_allowed", f"{grant.grt} is {grant.status}")
    _need(actor.subject != member.subject, "own_access", "nobody revokes their own access")
    tier = _tier(actor)
    if grant.grant == "tier" and int(grant.value) == Tier.OWNER:
        raise StaffRefused("forbidden", "an owner's tier is not changed through the API")
    if grant.grant == "tier" and int(grant.value) == Tier.ADMIN:
        _need(tier == Tier.OWNER, "forbidden", "an admin's tier is revoked by an owner")
    lead = actor.sid is not None and actor.sid == member.lead and tier >= Tier.LEAD
    _need(
        tier >= Tier.ADMIN or (lead and grant.grant in ("role", "acting")),
        "forbidden",
        "revoking needs T4, or the member's lead for a role or acting cover",
    )


def check_leave(actor: Access, member: Member, member_access: Access, now: int) -> None:
    _need(actor.has("staff.manage"), "forbidden", "marking a leaver needs T3 or above")
    _need(actor.subject != member.subject, "own_access", "nobody marks themselves as left")
    _need(member.status == "active", "not_allowed", f"{member.sid} is {member.status}")
    _need(member_access.tier != Tier.OWNER, "forbidden", "an owner is not removed through the API")
    _need(
        _tier(actor) > _tier(member_access),
        "forbidden",
        "only someone of a higher tier marks a leaver",
    )


def check_recommend(actor: Access, member: Member, now: int) -> None:
    _need(
        member.kind == "apprentice" and member.active(now),
        "not_allowed",
        f"{member.sid} is not an active apprentice",
    )
    _need(
        actor.sid is not None and actor.sid == member.mentor,
        "forbidden",
        "the apprentice's mentor recommends graduation",
    )
    _need(_tier(actor) >= Tier.SPECIALIST, "forbidden", "the mentor must be T2 or above")
    _need(member.recommended_by is None, "not_allowed", "already recommended")


def check_graduate(actor: Access, member: Member, new_subject: str, now: int) -> None:
    _need(
        member.kind == "apprentice" and member.active(now),
        "not_allowed",
        f"{member.sid} is not an active apprentice",
    )
    _need(member.recommended_by is not None, "not_allowed", "no staff recommendation yet")
    _need(_tier(actor) >= Tier.ADMIN, "forbidden", "an admin approves graduation")
    _need(
        actor.subject not in (member.subject, member.recommended_by, new_subject),
        "four_eyes",
        "the approving admin is neither the apprentice nor the recommender",
    )
    _need(new_subject != member.subject, "invalid", "the staff login is not the apprentice login")
