"""Oversight [staff spec 5d]: who watches the people with access.

- **Escalation**: an urgent notification nobody has acknowledged within
  `escalate_hours` is raised again to leads and admins (`staff.directory`),
  or, for one member's notification, to that member's lead. Once each.
- **Dormant accounts**: a member with no sign-in for `dormant_days` (30)
  loses access (status `dormant`) until an admin reinstates them; owners
  are exempt (they hold break-glass duties, reviewed by each other).
  A sign-in is the first staff request of a day (`sign_ins`).
- **Access reviews**: each lead confirms their team's access every
  `review_days_lead` (90), and owners each admin's every `review_days_admin`
  (30); due reviews are on the reviewer's to-do list.
- **Anomaly report**: approvals out of office hours, and the same maker and
  checker together again and again (collusion the four-eyes rule alone
  misses), over the last days.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from propfirm.pgschema import check_schema
from propfirm.staff.desk import StaffDesk
from propfirm.staff.model import DAY_MS, Tier
from propfirm.staff.records import Member, access_of
from propfirm.staff.store import StaffStore

SYSTEM = "system:oversight"


@dataclass(frozen=True, slots=True)
class OversightPolicy:
    escalate_hours: int = 4
    dormant_days: int = 30
    review_days_lead: int = 90
    review_days_admin: int = 30
    office_tz: str = "Asia/Dhaka"
    office_days: tuple[int, ...] = (6, 0, 1, 2, 3)  # Sunday to Thursday (Mon = 0)
    office_start_hour: int = 9
    office_end_hour: int = 18
    pair_threshold: int = 3  # the same maker and checker this often: flagged


def oversight_policy(staff: Any) -> OversightPolicy:
    """From the policy's `[staff]` (`propfirm.policy.StaffPolicy`)."""
    return OversightPolicy(
        staff.escalate_hours,
        staff.dormant_days,
        staff.review_days_lead,
        staff.review_days_admin,
        staff.office_timezone,
        tuple(staff.office_days),
        staff.office_start_hour,
        staff.office_end_hour,
        staff.pair_threshold,
    )


def in_office(ts: int, p: OversightPolicy) -> bool:
    t = datetime.fromtimestamp(ts / 1000, UTC).astimezone(ZoneInfo(p.office_tz))
    return t.weekday() in p.office_days and p.office_start_hour <= t.hour < p.office_end_hour


class Oversight:
    def __init__(
        self,
        pool: asyncpg.Pool,
        staff_schema: str,
        customer_schema: str,
        store: StaffStore,
        desk: StaffDesk | None,
    ) -> None:
        self._pool = pool
        self._s = check_schema(staff_schema)
        self._c = check_schema(customer_schema)
        self._store = store
        self._desk = desk
        self._seen: set[tuple[str, str]] = set()  # (subject, day) already recorded

    # --- sign-ins and dormant accounts ------------------------------------------------

    async def signed_in(self, subject: str, now: int) -> None:
        """Record the first staff request of the day (cheap after that)."""
        day = datetime.fromtimestamp(now / 1000, UTC).date()
        key = (subject, day.isoformat())
        if key in self._seen:
            return
        await self._pool.execute(
            f"INSERT INTO {self._s}.sign_ins (subject, day, first_ts) VALUES ($1, $2, $3) "
            f"ON CONFLICT (subject, day) DO NOTHING",
            subject,
            day,
            now,
        )
        self._seen.add(key)

    async def last_sign_in(self, subject: str) -> int | None:
        found: int | None = await self._pool.fetchval(
            f"SELECT max(first_ts) FROM {self._s}.sign_ins WHERE subject = $1", subject
        )
        return found

    async def dormant(self, now: int, p: OversightPolicy) -> list[str]:
        """Members with no sign-in for `dormant_days` (counted from joining
        when they never signed in) lose access until reinstated."""
        limit = p.dormant_days * DAY_MS
        out = []
        async with self._store.change() as ops:
            for m in await ops.members("active"):
                access = access_of(m, await ops.grants_of(m.sid), now)
                if access.tier is None or access.tier == Tier.OWNER:
                    continue
                last = await self.last_sign_in(m.subject)
                since = max(last or 0, m.starts_at, await self._reinstated_at(ops, m))
                if now - since < limit:
                    continue
                detail = {"by": SYSTEM, "reason": f"no sign-in for {p.dormant_days} days"}
                await ops.member_event(m.sid, "dormant", now, detail)
                out.append(m.sid)
        if self._desk is not None:
            for sid in out:
                await self._desk.notify(
                    "staff.directory",
                    "dormant",
                    sid,
                    f"Dormant account suspended: {sid}",
                    f"{sid} has not signed in for {p.dormant_days} days; access is suspended "
                    f"until an admin reinstates it (POST /staff/members/{sid}/reinstate).",
                    now,
                )
        return out

    @staticmethod
    async def _reinstated_at(ops: Any, m: Member) -> int:
        last = 0
        for e in await ops.member_history(m.sid):
            if e.kind == "reinstated":
                last = e.ts
        return last

    # --- escalation -------------------------------------------------------------------

    async def escalate(self, now: int, p: OversightPolicy) -> list[int]:
        """Urgent, open, acknowledged by nobody, older than `escalate_hours`,
        not escalated yet: raised to leads and admins (or the member's lead)."""
        if self._desk is None:
            return []
        rows = await self._pool.fetch(
            f"SELECT n.* FROM {self._s}.notifications n WHERE n.urgent "
            f"AND n.ts <= $1 AND n.kind <> 'escalation' "
            f"AND NOT EXISTS (SELECT 1 FROM {self._s}.notification_closes c "
            f"WHERE c.notification = n.id) "
            f"AND NOT EXISTS (SELECT 1 FROM {self._s}.notification_acks a "
            f"WHERE a.notification = n.id) "
            f"AND NOT EXISTS (SELECT 1 FROM {self._s}.escalations e "
            f"WHERE e.notification = n.id) ORDER BY n.id",
            now - p.escalate_hours * 3_600_000,
        )
        out = []
        for r in rows:
            audience = "staff.directory"
            if r["audience"].startswith("member:"):
                async with self._store.read() as ops:
                    m = await ops.member(r["audience"].removeprefix("member:"))
                if m is not None and m.lead is not None:
                    audience = f"member:{m.lead}"
            if audience == r["audience"]:
                audience = "settings.change"  # leads already had it: admins
            escalation = await self._desk.notify(
                audience,
                "escalation",
                r["ref"],
                f"Not acknowledged for {p.escalate_hours} hours: {r['title']}",
                r["body"],
                now,
                urgent=True,
            )
            await self._pool.execute(
                f"INSERT INTO {self._s}.escalations (notification, escalation, ts) "
                f"VALUES ($1, $2, $3) ON CONFLICT (notification) DO NOTHING",
                r["id"],
                escalation,
                now,
            )
            out.append(r["id"])
        return out

    # --- access reviews ---------------------------------------------------------------

    async def review(self, sid: str, reviewer: str, decision: str, note: str, now: int) -> None:
        await self._pool.execute(
            f"INSERT INTO {self._s}.access_reviews (sid, reviewer, decision, note, ts) "
            f"VALUES ($1, $2, $3, $4, $5)",
            sid,
            reviewer,
            decision,
            note,
            now,
        )

    async def last_review(self, sid: str) -> int | None:
        found: int | None = await self._pool.fetchval(
            f"SELECT max(ts) FROM {self._s}.access_reviews WHERE sid = $1", sid
        )
        return found

    async def reviews_due(
        self, reviewer_sid: str | None, reviewer_tier: Tier | None, now: int, p: OversightPolicy
    ) -> list[tuple[str, int]]:
        """(member, due since) whose review this reviewer owes: their team
        every `review_days_lead`; each admin, for an owner, every
        `review_days_admin`."""
        out = []
        async with self._store.read() as ops:
            for m in await ops.members("active"):
                access = access_of(m, await ops.grants_of(m.sid), now)
                if access.tier is None or m.sid == reviewer_sid:
                    continue
                if access.tier == Tier.ADMIN and reviewer_tier == Tier.OWNER:
                    every = p.review_days_admin
                elif m.lead is not None and m.lead == reviewer_sid:
                    every = p.review_days_lead
                else:
                    continue
                since = (await self.last_review(m.sid)) or m.starts_at
                if now - since >= every * DAY_MS:
                    out.append((m.sid, since + every * DAY_MS))
        return out

    # --- the anomaly report -----------------------------------------------------------

    async def anomalies(self, since: int, p: OversightPolicy) -> dict[str, Any]:
        """Approvals out of office hours, and maker-checker pairs seen at
        least `pair_threshold` times, from `since`."""
        approvals: list[dict[str, Any]] = []
        pairs: Counter[tuple[str, str, str]] = Counter()
        rows = await self._pool.fetch(
            f"SELECT po, ts, detail->>'by' AS by, detail->>'as' AS step FROM "
            f"{self._c}.payout_events WHERE kind = 'approved' AND ts >= $1 ORDER BY po, id",
            since,
        )
        passer: dict[str, str] = {}
        for r in rows:
            approvals.append({"what": "payout", "ref": r["po"], "by": r["by"], "ts": r["ts"]})
            if r["step"] == "authorise" and r["po"] in passer:
                pairs[("payout", passer[r["po"]], r["by"])] += 1
            passer[r["po"]] = r["by"]
        rows = await self._pool.fetch(
            f"SELECT cas, kind, ts, detail->>'by' AS by FROM {self._c}.case_events "
            f"WHERE kind IN ('proposed', 'confirmed') AND ts >= $1 ORDER BY cas, id",
            since,
        )
        proposer: dict[str, str] = {}
        for r in rows:
            if r["kind"] == "proposed":
                proposer[r["cas"]] = r["by"]
                continue
            approvals.append({"what": "case", "ref": r["cas"], "by": r["by"], "ts": r["ts"]})
            if r["cas"] in proposer:
                pairs[("case", proposer[r["cas"]], r["by"])] += 1
        rows = await self._pool.fetch(
            f"SELECT e.grt, e.ts, e.detail->>'by' AS by, g.requested_by FROM "
            f"{self._s}.grant_events e JOIN {self._s}.grants g ON g.grt = e.grt "
            f"WHERE e.kind = 'approved' AND e.ts >= $1 ORDER BY e.id",
            since,
        )
        for r in rows:
            if r["by"] == "system:config":
                continue
            approvals.append({"what": "grant", "ref": r["grt"], "by": r["by"], "ts": r["ts"]})
            pairs[("grant", r["requested_by"], r["by"])] += 1
        return {
            "since": since,
            "approvals": len(approvals),
            "out_of_hours": [a for a in approvals if not in_office(a["ts"], p)],
            "repeated_pairs": [
                {"what": what, "maker": maker, "checker": checker, "times": n}
                for (what, maker, checker), n in sorted(pairs.items())
                if n >= p.pair_threshold
            ],
            "office": {
                "timezone": p.office_tz,
                "days": list(p.office_days),
                "hours": [p.office_start_hour, p.office_end_hour],
            },
        }

    async def advance(self, now: int, p: OversightPolicy) -> None:
        await self.escalate(now, p)
        await self.dormant(now, p)
