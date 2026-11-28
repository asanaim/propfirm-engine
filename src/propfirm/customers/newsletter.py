"""Newsletter sending (D-15.2; rules AC00, AC25).

A campaign is a subject and a plain text body. Sending is publishing, so
it is a decision under the two person rule (AC00):
- a staff member with settings rights drafts and proposes it;
- a different one approves it, and only then is it sent; or rejects it;
- a proposal can be cancelled by its maker before approval.

On approval the audience is fixed: every address confirmed at that
moment (rule AC25, double opt-in). The API loop then sends at most
`per_minute` emails a minute, so a mail provider's rate limits hold. A
failed send is tried again up to `attempts` times. Each email ends with:
- an unsubscribe link of its own (a random token, kept as a hash, which
  the unsubscribe endpoint honours like the sign-up one);
- the firm's postal address and name from the website's contact section
  (anti-spam law in the US and EU wants both).
An address that left after approval and before its turn is skipped.

Every step is an append-only record: campaigns, their events, the
audience and each attempt's result.
"""

import hashlib
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

log = logging.getLogger(__name__)

STATES = ("proposed", "approved", "rejected", "cancelled")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class CampaignRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Mailer(Protocol):
    async def send(self, recipient: str, subject: str, body: str) -> str: ...


class Contact(Protocol):
    async def contact(self) -> Mapping[str, str]: ...


class SiteContact:
    """The website's contact section (rule AC07) as the email footer."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def contact(self) -> Mapping[str, str]:
        section = (await self._settings.site()).get("contact")
        return {} if section is None else dict(section.fields)


@dataclass(frozen=True, slots=True)
class Campaign:
    id: int
    subject: str
    body: str
    created_by: str
    created_at: int
    state: str  # proposed, approved, rejected, cancelled
    decided_by: str | None
    decided_at: int | None
    reason: str | None
    audience: int
    sent: int
    failed: int  # gave up after every attempt
    skipped: int  # left the list before their turn


def footer(unsubscribe_url: str, contact: Mapping[str, str]) -> str:
    name = contact.get("company_name", "").strip()
    address = contact.get("address", "").strip()
    sender = ", ".join(p for p in (name, address) if p)
    return (
        "\n\n--\n"
        "You get this because you subscribed to Pipridge news.\n"
        f"Unsubscribe: {unsubscribe_url}\n" + (f"{sender}\n" if sender else "")
    )


class Newsletter:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        policy: Any,
        mailer: Mailer | None,
        public_url: str | None,
        contact: Contact | None = None,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._policy = policy
        self._mailer = mailer
        self._url = (public_url or "").rstrip("/")
        self._contact = contact
        self._last = -1

    # --- drafting and deciding --------------------------------------------------------

    async def propose(self, subject: str, body: str, by: str, now: int) -> int:
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            cid = await conn.fetchval(
                f"INSERT INTO {s}.campaigns (subject, body, created_by, created_at) "
                f"VALUES ($1, $2, $3, $4) RETURNING id",
                subject,
                body,
                by,
                now,
            )
            await conn.execute(
                f"INSERT INTO {s}.campaign_events (campaign, kind, by_whom, at) "
                f"VALUES ($1, 'proposed', $2, $3)",
                cid,
                by,
                now,
            )
        return int(cid)

    async def decide(self, cid: int, kind: str, by: str, reason: str | None, now: int) -> int:
        """approve, reject or cancel a proposed campaign; approving fixes
        the audience and returns its size."""
        if kind not in ("approved", "rejected", "cancelled"):
            raise ValueError("unknown decision")
        s = self._s
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT created_by FROM {s}.campaigns WHERE id = $1 FOR UPDATE", cid
            )
            if row is None:
                raise CampaignRefused("unknown", "no campaign by that number")
            state = await conn.fetchval(
                f"SELECT kind FROM {s}.campaign_events WHERE campaign = $1 "
                f"ORDER BY seq DESC LIMIT 1",
                cid,
            )
            if state != "proposed":
                raise CampaignRefused("decided", f"the campaign is already {state}")
            maker = str(row["created_by"])
            if kind == "cancelled" and by != maker:
                raise CampaignRefused("not_maker", "only its maker cancels a proposal")
            if kind != "cancelled" and by == maker:
                raise CampaignRefused(
                    "same_person", "a second person approves or rejects a campaign"
                )
            await conn.execute(
                f"INSERT INTO {s}.campaign_events (campaign, kind, by_whom, at, reason) "
                f"VALUES ($1, $2, $3, $4, $5)",
                cid,
                kind,
                by,
                now,
                reason,
            )
            if kind != "approved":
                return 0
            done = await conn.execute(
                f"INSERT INTO {s}.campaign_audience (campaign, email) "
                f"SELECT $1, email FROM ("
                f"  SELECT DISTINCT ON (email) email, kind FROM {s}.subscriber_events "
                f"  ORDER BY email, seq DESC) last WHERE kind = 'confirmed'",
                cid,
            )
        return int(done.split()[-1])

    # --- sending ----------------------------------------------------------------------

    async def send_due(self, now: int, every_ms: int = 60_000) -> int:
        """Send the next batch of approved campaigns; return how many went."""
        policy = (await self._policy.current()).subscribers
        if self._mailer is None:
            return 0
        if self._last >= 0 and now - self._last < every_ms:
            return 0
        self._last = now
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT a.campaign, a.email, c.subject, c.body, "
            f"(SELECT count(*) FROM {s}.campaign_results r WHERE r.campaign = a.campaign "
            f" AND r.email = a.email) AS tries "
            f"FROM {s}.campaign_audience a JOIN {s}.campaigns c ON c.id = a.campaign "
            f"WHERE NOT EXISTS (SELECT 1 FROM {s}.campaign_results r WHERE r.campaign = "
            f" a.campaign AND r.email = a.email AND r.outcome IN ('sent', 'skipped')) "
            f"AND (SELECT count(*) FROM {s}.campaign_results r WHERE r.campaign = a.campaign "
            f" AND r.email = a.email) < $1 "
            f"ORDER BY a.campaign, a.email LIMIT $2",
            policy.attempts,
            policy.per_minute,
        )
        contact = await self._contact.contact() if self._contact is not None else {}
        sent = 0
        for r in rows:
            cid, email = int(r["campaign"]), str(r["email"])
            if await self._left(email):
                await self._result(cid, email, "skipped", None, None, now)
                continue
            token = secrets.token_urlsafe(32)
            link = f"{self._url}/unsubscribe?token={token}"
            try:
                message_id = await self._mailer.send(
                    email, str(r["subject"]), str(r["body"]) + footer(link, contact)
                )
            except Exception as exc:  # one bad address must not stop the rest
                log.warning("newsletter %s: send failed (%s)", cid, type(exc).__name__)
                await self._result(cid, email, "failed", None, type(exc).__name__, now)
                continue
            await self._result(cid, email, "sent", message_id, sha(token), now)
            sent += 1
        return sent

    async def _left(self, email: str) -> bool:
        kind = await self._pool.fetchval(
            f"SELECT kind FROM {self._s}.subscriber_events WHERE email = $1 "
            f"ORDER BY seq DESC LIMIT 1",
            email,
        )
        return bool(kind != "confirmed")

    async def _result(
        self,
        cid: int,
        email: str,
        outcome: str,
        message_id: str | None,
        detail: str | None,
        now: int,
    ) -> None:
        """detail: the unsubscribe token's hash for a sent email, else the
        error's type."""
        await self._pool.execute(
            f"INSERT INTO {self._s}.campaign_results (campaign, email, at, outcome, "
            f"message_id, unsubscribe_sha, error) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            cid,
            email,
            now,
            outcome,
            message_id,
            detail if outcome == "sent" else None,
            detail if outcome == "failed" else None,
        )

    # --- reading ----------------------------------------------------------------------

    async def campaigns(self) -> list[Campaign]:
        s = self._s
        policy = (await self._policy.current()).subscribers
        rows = await self._pool.fetch(
            f"SELECT c.id, c.subject, c.body, c.created_by, c.created_at, "
            f"e.kind AS state, e.by_whom, e.at AS decided_at, e.reason, "
            f"(SELECT count(*) FROM {s}.campaign_audience a WHERE a.campaign = c.id) AS audience, "
            f"(SELECT count(*) FROM {s}.campaign_results r WHERE r.campaign = c.id "
            f" AND r.outcome = 'sent') AS sent, "
            f"(SELECT count(*) FROM {s}.campaign_results r WHERE r.campaign = c.id "
            f" AND r.outcome = 'skipped') AS skipped, "
            f"(SELECT count(*) FROM (SELECT email FROM {s}.campaign_results r "
            f" WHERE r.campaign = c.id GROUP BY email HAVING count(*) FILTER "
            f" (WHERE outcome = 'failed') >= $1 AND bool_and(outcome = 'failed')) f) AS failed "
            f"FROM {s}.campaigns c JOIN LATERAL (SELECT kind, by_whom, at, reason FROM "
            f"{s}.campaign_events WHERE campaign = c.id ORDER BY seq DESC LIMIT 1) e ON true "
            f"ORDER BY c.id DESC",
            policy.attempts,
        )
        return [
            Campaign(
                int(r["id"]),
                str(r["subject"]),
                str(r["body"]),
                str(r["created_by"]),
                int(r["created_at"]),
                str(r["state"]),
                None if r["state"] == "proposed" else str(r["by_whom"]),
                None if r["state"] == "proposed" else int(r["decided_at"]),
                r["reason"],
                int(r["audience"]),
                int(r["sent"]),
                int(r["failed"]),
                int(r["skipped"]),
            )
            for r in rows
        ]
