"""Tiers, functional roles and permissions (staff spec, 31 Oct 2026).

Access is the sum of a tier (seniority and approval limits), functional
roles (what work a person does) and time-limited grants of admin powers.
No permission is attached to a person directly: each comes from a grant
someone approved (`propfirm.staff.records`).

A permission needs a minimum tier and, below T4, a functional role. Admins
(T4) and owners (T5) do any role's work. An admin power granted to someone
below T4 gives that one permission only, never payout or void approval: no
amount is approved below admin [decided 31 Oct 2026].

Before every employee has a directory record, the Keycloak roles map as
today's access (`legacy_access`): `admin` is T4, `staff` is T2 with every
role. `STAFF_LEGACY_ROLES=0` turns that off once the directory is complete.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum


class Tier(IntEnum):
    APPRENTICE = 0
    ASSOCIATE = 1
    SPECIALIST = 2
    LEAD = 3
    ADMIN = 4
    OWNER = 5


TITLES = {
    Tier.APPRENTICE: "Apprentice",
    Tier.ASSOCIATE: "Associate",
    Tier.SPECIALIST: "Specialist",
    Tier.LEAD: "Team lead",
    Tier.ADMIN: "Admin",
    Tier.OWNER: "Owner",
}

ROLES = ("support", "kyc", "risk", "payouts", "integrity", "product", "marketing", "it")
KINDS = ("staff", "apprentice", "contractor")


@dataclass(frozen=True, slots=True)
class Permission:
    name: str
    tier: Tier  # the lowest tier that has it
    role: str | None  # the functional role it needs below T4 (None: any)
    approval: bool  # an approval: step-up sign-in (second factor, recent)
    describes: str


_P = Permission
PERMISSIONS: dict[str, Permission] = {
    p.name: p
    for p in (
        _P("staff.view", Tier.ASSOCIATE, None, False, "read back-office records"),
        _P(
            "progression.decide",  # T2, risk desk [decided 6 Nov 2026: fewer approvers of a
            Tier.SPECIALIST,  # funded account, the main insider-fraud risk (M17)]
            "risk",
            True,
            "approve or deny a funded review",
        ),
        _P("onboarding.decide", Tier.ASSOCIATE, "kyc", True, "accept or reject an identity"),
        _P("case.work", Tier.ASSOCIATE, "integrity", False, "alerts, notes, decisions on cases"),
        _P("case.pass", Tier.SPECIALIST, "integrity", False, "propose a void or profit removal"),
        _P("case.confirm", Tier.SPECIALIST, "integrity", True, "confirm a customer block"),
        _P("case.authorise", Tier.ADMIN, None, True, "authorise a void or profit removal"),
        _P("payout.pass", Tier.SPECIALIST, "payouts", True, "check and pass a payout"),
        _P("payout.deny", Tier.SPECIALIST, "payouts", False, "deny a payout"),
        _P("payout.authorise", Tier.ADMIN, None, True, "authorise a passed payout"),
        _P("news.desk", Tier.ASSOCIATE, "risk", False, "add restricted news windows"),
        _P("news.cancel", Tier.ADMIN, None, True, "cancel a news window"),
        _P("settings.view", Tier.ADMIN, None, False, "read admin settings and their history"),
        _P("settings.change", Tier.ADMIN, None, True, "change business settings"),
        _P("staff.directory", Tier.LEAD, None, False, "read the staff directory and grants"),
        _P("staff.manage", Tier.LEAD, None, False, "add staff and request grants"),
    )
}

# admin powers a T3 or below may be granted for a while (spec: two admins or
# one owner, at most `power_max_days`); never an amount approval
POWERS = frozenset({"settings.view", "settings.change", "news.cancel"})


@dataclass(frozen=True, slots=True)
class Access:
    """What one login may do now."""

    subject: str
    source: str  # "directory", "legacy" (Keycloak roles) or "none"
    tier: Tier | None  # None: no staff access
    roles: frozenset[str] = frozenset()
    powers: frozenset[str] = frozenset()
    sid: str | None = None
    kind: str | None = None
    grants: tuple[str, ...] = field(default=())  # GRT ids in force

    def has(self, name: str) -> bool:
        p = PERMISSIONS[name]
        if self.tier is None:
            return False
        if name in self.powers:
            return True
        if self.tier < p.tier:
            return False
        return p.role is None or self.tier >= Tier.ADMIN or p.role in self.roles

    def permissions(self) -> list[str]:
        return [name for name in PERMISSIONS if self.has(name)]


NO_ACCESS_SOURCE = "none"


def no_access(subject: str, source: str = NO_ACCESS_SOURCE, sid: str | None = None) -> Access:
    return Access(subject, source, None, sid=sid)


def legacy_access(subject: str, keycloak_roles: frozenset[str]) -> Access:
    """Today's Keycloak roles, unchanged: admin T4, staff T2 with every role."""
    if "admin" in keycloak_roles:
        return Access(subject, "legacy", Tier.ADMIN, frozenset(ROLES))
    if "staff" in keycloak_roles:
        return Access(subject, "legacy", Tier.SPECIALIST, frozenset(ROLES))
    return no_access(subject)


DAY_MS = 86_400_000


def add_months(ts: int, months: int) -> int:
    """`ts` moved by whole calendar months in UTC; a day past the new month's
    end becomes its last day (31 Jan + 1 month = 28 or 29 Feb). Explicit,
    tested rounding (CLAUDE.md)."""
    seconds, ms = divmod(ts, 1000)
    t = datetime.fromtimestamp(seconds, UTC)
    year, month0 = divmod(t.year * 12 + t.month - 1 + months, 12)
    month = month0 + 1
    first_next = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
    last_day = (first_next - datetime(year, month, 1, tzinfo=UTC)).days
    moved = t.replace(year=year, month=month, day=min(t.day, last_day))
    return int(moved.timestamp()) * 1000 + ms
