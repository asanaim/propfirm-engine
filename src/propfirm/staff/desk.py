"""The staff page (back office 5b): duty sheets and their acknowledgement,
notifications to whoever holds a permission now, and internal messages
tied to a record. All append-only (`staff` schema, migration 2).

Duty sheets [staff spec]: the written responsibilities of each functional
role, of admins and owners, and of apprentices (`config/staff/duties.toml`),
versioned; each person acknowledges the version in force for every sheet
that applies to them.

Notifications [staff spec]: every alert has an owner (a permission, so its
recipients are whoever holds it at the moment, never a fixed list), or one
member. A notification stays on each person's page until they acknowledge
it or the work it points to closes (`close`). Urgent ones are also emailed
by their producer (news sources: `propfirm.newsfeed.alerts`). Escalation to
the lead when not acknowledged in time comes with the oversight step (5d).

Messages: internal threads on one record (a case, payout, onboarding,
progression, account, customer, grant or member), so the discussion is
part of the record's file. Never sent outside the platform.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

from propfirm.pgschema import check_schema
from propfirm.staff.model import PERMISSIONS, ROLES, Access, Tier

DUTIES_FILE = Path(__file__).resolve().parents[3] / "config" / "staff" / "duties.toml"
SHEETS = (*ROLES, "apprentice", "admin", "owner")
REF = re.compile(r"^(CAS|PO|ONB|PRG|ACC|CIF|GRT|STF|APP)[0-9]{8}$")
MEMBER_AUDIENCE = re.compile(r"^member:(STF|APP)[0-9]{8}$")


class DeskError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DutySheet:
    sheet: str  # a role, or apprentice / admin / owner
    version: int
    title: str
    duties: tuple[str, ...]


def load_duties(path: Path = DUTIES_FILE) -> dict[str, DutySheet]:
    """Every sheet, strictly: each of `SHEETS` once, a version, duties."""
    with path.open("rb") as f:
        raw = tomllib.load(f)
    if set(raw) != set(SHEETS):
        raise DeskError(f"duty sheets must be exactly {sorted(SHEETS)}; got {sorted(raw)}")
    out = {}
    for name, table in raw.items():
        if not isinstance(table, dict) or set(table) != {"version", "title", "duties"}:
            raise DeskError(f"[{name}]: version, title and duties")
        version, title, duties = table["version"], table["title"], table["duties"]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise DeskError(f"[{name}] version: a whole number from 1")
        if not isinstance(title, str) or not title.strip():
            raise DeskError(f"[{name}] title")
        if (
            not isinstance(duties, list)
            or not duties
            or not all(isinstance(d, str) and d.strip() for d in duties)
        ):
            raise DeskError(f"[{name}] duties: a list of text")
        out[name] = DutySheet(name, version, title, tuple(duties))
    return out


def sheets_for(access: Access) -> list[str]:
    """The sheets that apply: the tier's (apprentice, admin, owner) and
    each role's (admins and owners do any role's work: their sheet covers
    it)."""
    if access.tier is None:
        return []
    if access.tier == Tier.APPRENTICE:
        return ["apprentice", *sorted(access.roles)]
    if access.tier == Tier.OWNER:
        return ["owner", "admin"]
    if access.tier == Tier.ADMIN:
        return ["admin"]
    return sorted(access.roles)


def audience_valid(audience: str) -> bool:
    return audience in PERMISSIONS or MEMBER_AUDIENCE.fullmatch(audience) is not None


def reaches(audience: str, access: Access) -> bool:
    if audience.startswith("member:"):
        return access.sid == audience.removeprefix("member:")
    return access.has(audience)


@dataclass(frozen=True, slots=True)
class Notification:
    id: int
    audience: str
    kind: str
    ref: str
    title: str
    body: str
    urgent: bool
    ts: int
    closed_at: int | None
    acked_at: int | None  # by the person reading


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    ref: str
    subject: str
    sid: str | None
    body: str
    ts: int


class StaffDesk:
    def __init__(self, pool: asyncpg.Pool, schema: str, duties: dict[str, DutySheet]) -> None:
        self._pool = pool
        self._s = check_schema(schema)
        self.duties = duties

    # --- duties -----------------------------------------------------------------------

    async def acknowledged(self, subject: str) -> dict[str, int]:
        """Per sheet, the latest version this login acknowledged."""
        rows = await self._pool.fetch(
            f"SELECT sheet, max(version) AS version FROM {self._s}.duty_acks "
            f"WHERE subject = $1 GROUP BY sheet",
            subject,
        )
        return {r["sheet"]: r["version"] for r in rows}

    async def acknowledge(self, access: Access, sheet: str, ts: int) -> int:
        """Acknowledge the version in force of a sheet that applies."""
        if sheet not in sheets_for(access):
            raise DeskError(f"the {sheet} duties do not apply to this login")
        version = self.duties[sheet].version
        await self._pool.execute(
            f"INSERT INTO {self._s}.duty_acks (subject, sheet, version, ts) "
            f"VALUES ($1, $2, $3, $4) ON CONFLICT (subject, sheet, version) DO NOTHING",
            access.subject,
            sheet,
            version,
            ts,
        )
        return version

    async def duties_view(self, access: Access) -> list[dict[str, Any]]:
        acked = await self.acknowledged(access.subject)
        out = []
        for name in sheets_for(access):
            sheet = self.duties[name]
            out.append(
                {
                    "sheet": name,
                    "title": sheet.title,
                    "version": sheet.version,
                    "duties": list(sheet.duties),
                    "acknowledged_version": acked.get(name),
                    "acknowledged": acked.get(name) == sheet.version,
                }
            )
        return out

    # --- notifications ----------------------------------------------------------------

    async def notify(
        self,
        audience: str,
        kind: str,
        ref: str,
        title: str,
        body: str,
        ts: int,
        urgent: bool = False,
    ) -> int:
        if not audience_valid(audience):
            raise DeskError(f"audience {audience!r}: a permission or member:<id>")
        notification: int = await self._pool.fetchval(
            f"INSERT INTO {self._s}.notifications "
            f"(audience, kind, ref, title, body, urgent, ts) "
            f"VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
            audience,
            kind,
            ref,
            title,
            body,
            urgent,
            ts,
        )
        return notification

    async def close(self, ref: str, reason: str, ts: int, kind: str | None = None) -> int:
        """Close every open notification about `ref` (the work is done)."""
        result: str = await self._pool.execute(
            f"INSERT INTO {self._s}.notification_closes (notification, reason, ts) "
            f"SELECT n.id, $2, $3 FROM {self._s}.notifications n WHERE n.ref = $1 "
            f"AND ($4::text IS NULL OR n.kind = $4) "
            f"AND NOT EXISTS (SELECT 1 FROM {self._s}.notification_closes c "
            f"WHERE c.notification = n.id)",
            ref,
            reason,
            ts,
            kind,
        )
        return int(result.split()[-1])

    async def open_for(self, ref: str, kind: str | None = None) -> list[int]:
        rows = await self._pool.fetch(
            f"SELECT n.id FROM {self._s}.notifications n WHERE n.ref = $1 "
            f"AND ($2::text IS NULL OR n.kind = $2) AND NOT EXISTS "
            f"(SELECT 1 FROM {self._s}.notification_closes c WHERE c.notification = n.id) "
            f"ORDER BY n.id",
            ref,
            kind,
        )
        return [r["id"] for r in rows]

    async def notifications(
        self, access: Access, include_done: bool = False, limit: int = 200
    ) -> list[Notification]:
        """What reaches this login now: open and not acknowledged by them
        (or all, with `include_done`), newest first."""
        rows = await self._pool.fetch(
            f"SELECT n.*, c.ts AS closed_at, a.ts AS acked_at "
            f"FROM {self._s}.notifications n "
            f"LEFT JOIN {self._s}.notification_closes c ON c.notification = n.id "
            f"LEFT JOIN {self._s}.notification_acks a "
            f"ON a.notification = n.id AND a.subject = $1 "
            f"WHERE $2 OR (c.ts IS NULL AND a.ts IS NULL) ORDER BY n.id DESC LIMIT $3",
            access.subject,
            include_done,
            2_000,  # newest first; filtered by audience below
        )
        out = []
        for r in rows:
            if not reaches(r["audience"], access):
                continue
            out.append(
                Notification(
                    r["id"],
                    r["audience"],
                    r["kind"],
                    r["ref"],
                    r["title"],
                    r["body"],
                    r["urgent"],
                    r["ts"],
                    r["closed_at"],
                    r["acked_at"],
                )
            )
        return out[:limit]

    async def acknowledge_notification(self, access: Access, notification: int, ts: int) -> None:
        row = await self._pool.fetchrow(
            f"SELECT audience FROM {self._s}.notifications WHERE id = $1", notification
        )
        if row is None or not reaches(row["audience"], access):
            raise DeskError("unknown notification")
        await self._pool.execute(
            f"INSERT INTO {self._s}.notification_acks (notification, subject, ts) "
            f"VALUES ($1, $2, $3) ON CONFLICT (notification, subject) DO NOTHING",
            notification,
            access.subject,
            ts,
        )

    # --- messages ---------------------------------------------------------------------

    async def post(self, access: Access, ref: str, body: str, ts: int) -> Message:
        if REF.fullmatch(ref) is None:
            raise DeskError("a thread is on one record: CAS, PO, ONB, PRG, ACC, CIF, GRT, STF, APP")
        text = body.strip()
        if not 1 <= len(text) <= 4000:
            raise DeskError("a message is 1 to 4,000 characters")
        row = await self._pool.fetchrow(
            f"INSERT INTO {self._s}.messages (ref, subject, sid, body, ts) "
            f"VALUES ($1, $2, $3, $4, $5) RETURNING id",
            ref,
            access.subject,
            access.sid,
            text,
            ts,
        )
        assert row is not None
        return Message(row["id"], ref, access.subject, access.sid, text, ts)

    async def thread(self, ref: str, limit: int = 500) -> list[Message]:
        rows = await self._pool.fetch(
            f"SELECT * FROM {self._s}.messages WHERE ref = $1 ORDER BY id LIMIT $2", ref, limit
        )
        return [
            Message(r["id"], r["ref"], r["subject"], r["sid"], r["body"], r["ts"]) for r in rows
        ]

    async def threads(self, limit: int = 50) -> list[dict[str, Any]]:
        """The records discussed most recently."""
        rows = await self._pool.fetch(
            f"SELECT ref, count(*) AS messages, max(ts) AS last_at FROM {self._s}.messages "
            f"GROUP BY ref ORDER BY max(id) DESC LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]


def notification_view(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "audience": n.audience,
        "kind": n.kind,
        "ref": n.ref,
        "title": n.title,
        "body": n.body,
        "urgent": n.urgent,
        "ts": n.ts,
        "closed_at": n.closed_at,
        "acknowledged_at": n.acked_at,
    }


def message_view(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "ref": m.ref,
        "subject": m.subject,
        "sid": m.sid,
        "body": m.body,
        "ts": m.ts,
    }
