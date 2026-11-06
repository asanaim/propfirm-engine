"""The staff directory, the service: access for each login, and every
change to members and grants under the rules of `propfirm.staff.records`.

Each change runs in one transaction under the directory's lock: the
actor's access is computed inside it, so a grant revoked a moment earlier
is never used to approve.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from propfirm.policy import PolicySource
from propfirm.staff.model import ROLES, Access, Tier, legacy_access, no_access
from propfirm.staff.records import (
    SYSTEM,
    Grant,
    Limits,
    Member,
    StaffRefused,
    access_of,
    check_approval,
    check_graduate,
    check_join,
    check_leave,
    check_recommend,
    check_request,
    check_revoke,
)
from propfirm.staff.store import StaffOps, StaffStore


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is acting: the login and its Keycloak realm roles."""

    subject: str
    roles: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Owner:
    subject: str
    name: str
    email: str | None = None


class Directory:
    def __init__(
        self,
        store: StaffStore,
        policy: PolicySource,
        owners: Sequence[Owner] = (),
        *,
        legacy_roles: bool = True,
    ) -> None:
        self._store = store
        self._policy = policy
        self._owners = tuple(owners)
        self._legacy = legacy_roles

    @property
    def store(self) -> StaffStore:
        return self._store

    async def _limits(self) -> Limits:
        return (await self._policy.current()).staff.limits

    # --- access -----------------------------------------------------------------------

    async def _access(self, ops: StaffOps, actor: Actor, now: int) -> Access:
        member = await ops.member_by_subject(actor.subject)
        if member is None:
            return (
                legacy_access(actor.subject, actor.roles)
                if self._legacy
                else no_access(actor.subject)
            )
        return access_of(member, await ops.grants_of(member.sid), now)

    async def access(self, actor: Actor, now: int) -> Access:
        async with self._store.read() as ops:
            return await self._access(ops, actor, now)

    async def _member(self, ops: StaffOps, sid: str) -> Member:
        member = await ops.member(sid)
        if member is None:
            raise StaffRefused("unknown", f"no member {sid}")
        return member

    async def _grant(self, ops: StaffOps, grt: str) -> tuple[Grant, Member]:
        grant = await ops.grant(grt)
        if grant is None:
            raise StaffRefused("unknown", f"no grant {grt}")
        return grant, await self._member(ops, grant.sid)

    async def _member_access(self, ops: StaffOps, member: Member, now: int) -> Access:
        return access_of(member, await ops.grants_of(member.sid), now)

    # --- the owners -------------------------------------------------------------------

    async def bootstrap(self, now: int) -> list[str]:
        """Record each configured owner not yet in the directory, as T5 (a
        system grant: the one change without four eyes). Return their STFs."""
        added: list[str] = []
        async with self._store.change() as ops:
            for owner in self._owners:
                if await ops.member_by_subject(owner.subject) is not None:
                    continue
                sid = await ops.new_sid("staff")
                await ops.member_event(
                    sid,
                    "joined",
                    now,
                    {
                        "subject": owner.subject,
                        "name": owner.name,
                        "email": owner.email,
                        "kind": "staff",
                        "by": SYSTEM,
                        "reason": "owner named in configuration (STAFF_OWNERS)",
                    },
                )
                grt = await ops.new_grt()
                await ops.grant_event(
                    grt,
                    "requested",
                    now,
                    {
                        "sid": sid,
                        "grant": "tier",
                        "value": int(Tier.OWNER),
                        "starts_at": now,
                        "by": SYSTEM,
                        "reason": "owner named in configuration (STAFF_OWNERS)",
                    },
                )
                await ops.grant_event(grt, "approved", now, {"by": SYSTEM, "reason": "owner"})
                added.append(sid)
        return added

    # --- members ----------------------------------------------------------------------

    async def join(
        self,
        actor: Actor,
        *,
        subject: str,
        name: str,
        email: str | None,
        kind: str,
        team: str | None,
        lead: str | None,
        mentor: str | None,
        ends_at: int | None,
        tier: int | None,
        roles: Sequence[str],
        reason: str,
        now: int,
    ) -> tuple[Member, list[Grant]]:
        """A joiner, and the tier and roles requested for them (each waits
        for its approver)."""
        limits = await self._limits()
        async with self._store.change() as ops:
            me = await self._access(ops, actor, now)
            if await ops.member_by_subject(subject) is not None:
                raise StaffRefused("exists", "that login already has a staff record")
            lead_m = await self._member(ops, lead) if lead else None
            mentor_m = await self._member(ops, mentor) if mentor else None
            mentor_a = await self._member_access(ops, mentor_m, now) if mentor_m else None
            check_join(me, kind, subject, ends_at, lead_m, mentor_m, mentor_a, now, limits)
            if kind == "apprentice":
                tier = 0 if tier is None else tier
            for role in roles:
                if role not in ROLES:
                    raise StaffRefused("invalid", f"role must be one of {ROLES}")
            sid = await ops.new_sid(kind)
            detail: dict[str, Any] = {
                "subject": subject,
                "name": name,
                "email": email,
                "kind": kind,
                "team": team,
                "lead": lead,
                "mentor": mentor,
                "ends_at": ends_at,
                "by": actor.subject,
                "reason": reason,
            }
            member = await ops.member_event(sid, "joined", now, detail)
            requested: list[Grant] = []
            wanted: list[tuple[str, int | str]] = [] if tier is None else [("tier", tier)]
            wanted += [("role", r) for r in dict.fromkeys(roles)]
            for grant, value in wanted:
                requested.append(
                    await self._request(ops, me, member, grant, value, now, ends_at, reason, now)
                )
            return member, requested

    async def move(
        self, actor: Actor, sid: str, team: str | None, lead: str | None, reason: str, now: int
    ) -> Member:
        """A mover: a new team or lead. Roles that no longer fit are revoked
        as their own changes."""
        async with self._store.change() as ops:
            me = await self._access(ops, actor, now)
            member = await self._member(ops, sid)
            if not me.has("staff.manage") or me.subject == member.subject:
                raise StaffRefused("forbidden", "moving staff needs T3 or above, not oneself")
            theirs = (await self._member_access(ops, member, now)).tier
            if theirs is not None and (me.tier is None or theirs > me.tier):
                raise StaffRefused("forbidden", "nobody moves someone of a higher tier")
            if not member.active(now):
                raise StaffRefused("not_allowed", f"{sid} is not active")
            if lead is not None:
                new_lead = await self._member(ops, lead)
                if not new_lead.active(now) or lead == sid:
                    raise StaffRefused("invalid", "the lead is not another active member")
            detail = {"team": team, "lead": lead, "by": actor.subject, "reason": reason}
            return await ops.member_event(sid, "moved", now, detail)

    async def leave(self, actor: Actor, sid: str, reason: str, now: int) -> Member:
        """A leaver: no access from now; every grant ends with the record."""
        async with self._store.change() as ops:
            me = await self._access(ops, actor, now)
            member = await self._member(ops, sid)
            check_leave(me, member, await self._member_access(ops, member, now), now)
            left = await ops.member_event(sid, "left", now, {"by": actor.subject, "reason": reason})
            await self._end_grants(ops, sid, actor.subject, f"left: {reason}", now)
            return left

    async def _end_grants(self, ops: StaffOps, sid: str, by: str, reason: str, now: int) -> None:
        for g in await ops.grants_of(sid):
            if g.status == "approved":
                await ops.grant_event(g.grt, "revoked", now, {"by": by, "reason": reason})
            elif g.status == "requested":
                await ops.grant_event(g.grt, "denied", now, {"by": by, "reason": reason})

    async def recommend(self, actor: Actor, sid: str, reason: str, now: int) -> Member:
        async with self._store.change() as ops:
            me = await self._access(ops, actor, now)
            member = await self._member(ops, sid)
            check_recommend(me, member, now)
            detail = {"by": actor.subject, "reason": reason}
            return await ops.member_event(sid, "recommended", now, detail)

    async def graduate(
        self,
        actor: Actor,
        sid: str,
        subject: str,
        email: str | None,
        reason: str,
        now: int,
    ) -> Member:
        """An admin approves the mentor's recommendation: a new T1 staff
        member with its own login, linked to the apprentice record, which
        ends."""
        async with self._store.change() as ops:
            me = await self._access(ops, actor, now)
            apprentice = await self._member(ops, sid)
            check_graduate(me, apprentice, subject, now)
            if await ops.member_by_subject(subject) is not None:
                raise StaffRefused("exists", "that login already has a staff record")
            assert apprentice.recommended_by is not None
            new_sid = await ops.new_sid("staff")
            joined = {
                "subject": subject,
                "name": apprentice.name,
                "email": email,
                "kind": "staff",
                "team": apprentice.team,
                "lead": apprentice.mentor,
                "from_sid": sid,
                "by": actor.subject,
                "reason": reason,
            }
            staff = await ops.member_event(new_sid, "joined", now, joined)
            grt = await ops.new_grt()
            await ops.grant_event(
                grt,
                "requested",
                now,
                {
                    "sid": new_sid,
                    "grant": "tier",
                    "value": int(Tier.ASSOCIATE),
                    "starts_at": now,
                    "by": apprentice.recommended_by,
                    "reason": f"graduation of {sid}",
                },
            )
            await ops.grant_event(grt, "approved", now, {"by": actor.subject, "reason": reason})
            graduated = {"by": actor.subject, "reason": reason, "to_sid": new_sid}
            await ops.member_event(sid, "graduated", now, graduated)
            await self._end_grants(ops, sid, actor.subject, f"graduated to {new_sid}", now)
            return staff

    # --- grants -----------------------------------------------------------------------

    async def _request(
        self,
        ops: StaffOps,
        me: Access,
        member: Member,
        grant: str,
        value: int | str,
        starts_at: int,
        ends_at: int | None,
        reason: str,
        now: int,
    ) -> Grant:
        limits = await self._limits()
        if grant == "tier":
            ends_at = None
        member_access = await self._member_access(ops, member, now)
        check_request(me, member, member_access, grant, value, starts_at, ends_at, now, limits)
        for g in await ops.grants_of(member.sid):
            if g.grant == grant and g.value == value and g.status == "requested":
                raise StaffRefused("pending", f"{g.grt} already asks for this")
        grt = await ops.new_grt()
        detail: dict[str, Any] = {
            "sid": member.sid,
            "grant": grant,
            "value": value,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "by": me.subject,
            "tier": None if me.tier is None else int(me.tier),
            "reason": reason,
        }
        return await ops.grant_event(grt, "requested", now, detail)

    async def request(
        self,
        actor: Actor,
        sid: str,
        grant: str,
        value: int | str,
        starts_at: int | None,
        ends_at: int | None,
        reason: str,
        now: int,
    ) -> Grant:
        async with self._store.change() as ops:
            me = await self._access(ops, actor, now)
            member = await self._member(ops, sid)
            start = now if starts_at is None else starts_at
            return await self._request(ops, me, member, grant, value, start, ends_at, reason, now)

    async def approve(self, actor: Actor, grt: str, reason: str, now: int) -> Grant:
        async with self._store.change() as ops:
            me = await self._access(ops, actor, now)
            grant, member = await self._grant(ops, grt)
            check_approval(me, member, await self._member_access(ops, member, now), grant, now)
            detail = {"by": actor.subject, "tier": int(me.tier or 0), "reason": reason}
            approved = await ops.grant_event(grt, "approved", now, detail)
            if grant.grant == "tier":  # one tier at a time: the new one replaces the old
                for g in await ops.grants_of(member.sid):
                    if g.grant == "tier" and g.status == "approved" and g.grt != grt:
                        await ops.grant_event(
                            g.grt, "superseded", now, {"by": actor.subject, "by_grt": grt}
                        )
            return approved

    async def deny(self, actor: Actor, grt: str, reason: str, now: int) -> Grant:
        async with self._store.change() as ops:
            me = await self._access(ops, actor, now)
            grant, member = await self._grant(ops, grt)
            check_approval(me, member, await self._member_access(ops, member, now), grant, now)
            return await ops.grant_event(
                grt, "denied", now, {"by": actor.subject, "reason": reason}
            )

    async def cancel(self, actor: Actor, grt: str, reason: str, now: int) -> Grant:
        """The requester withdraws a request not yet decided."""
        async with self._store.change() as ops:
            grant, _ = await self._grant(ops, grt)
            if grant.requested_by != actor.subject:
                raise StaffRefused("forbidden", "only the requester cancels a request")
            if grant.status != "requested":
                raise StaffRefused("not_allowed", f"{grt} is {grant.status}")
            detail = {"by": actor.subject, "reason": reason}
            return await ops.grant_event(grt, "cancelled", now, detail)

    async def revoke(self, actor: Actor, grt: str, reason: str, now: int) -> Grant:
        async with self._store.change() as ops:
            me = await self._access(ops, actor, now)
            grant, member = await self._grant(ops, grt)
            check_revoke(me, member, grant, now)
            detail = {"by": actor.subject, "reason": reason}
            return await ops.grant_event(grt, "revoked", now, detail)

    # --- what one person sees ---------------------------------------------------------

    async def awaiting(self, actor: Actor, now: int) -> list[Grant]:
        """Requests this login may approve now."""
        async with self._store.read() as ops:
            me = await self._access(ops, actor, now)
            out = []
            for g in await ops.grants("requested"):
                member = await ops.member(g.sid)
                assert member is not None
                try:
                    check_approval(
                        me, member, access_of(member, await ops.grants_of(g.sid), now), g, now
                    )
                except StaffRefused:
                    continue
                out.append(g)
            return out

    async def graduations(self, actor: Actor, now: int) -> list[Member]:
        """Recommended apprentices this login may graduate."""
        async with self._store.read() as ops:
            me = await self._access(ops, actor, now)
            out = []
            for m in await ops.members("active"):
                if m.kind != "apprentice" or m.recommended_by is None:
                    continue
                if (
                    me.tier is not None
                    and me.tier >= Tier.ADMIN
                    and actor.subject
                    not in (
                        m.subject,
                        m.recommended_by,
                    )
                ):
                    out.append(m)
            return out

    async def emails_for(self, permission: str, now: int) -> list[str]:
        """Email addresses of the active members who hold `permission` now:
        alert recipients by role, never a fixed list [staff spec 5b]."""
        out = []
        async with self._store.read() as ops:
            for m in await ops.members("active"):
                if m.email is None:
                    continue
                if access_of(m, await ops.grants_of(m.sid), now).has(permission):
                    out.append(m.email)
        return sorted(set(out))
