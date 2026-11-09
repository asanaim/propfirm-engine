"""Security emails to customers, through an outbox.

A notice is written in the same transaction as the change it reports (a
payout wallet added or changed), so a change is never made without its
email; `Notifications.advance` sends what is pending through a `Mailer`
and records each attempt (at least once: a crash between sending and
recording sends it again). A failed send is tried again after `RETRY_MS`.
Sent to the email address the customer registered, as exchanges do for a
new withdrawal address.

`notifications` holds personal data (the address, the name, the wallet in
the text): insert-only by the code, erasable. `notification_events` (sent,
failed) is append-only.

Mailers: SMTP (any transactional provider: Amazon SES, Postmark, SendGrid
and others offer it), and the sandbox mailer, which sends nothing.
"""

import asyncio
import json
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, Protocol

import asyncpg

from propfirm.pgschema import Conn, check_schema
from propfirm.settings.notices import Notices, utc_text

log = logging.getLogger(__name__)
NOTICE_KINDS = ("wallet_changed",)
RETRY_MS = 60_000  # a failed send is tried again after this
TLS_MODES = ("starttls", "implicit")


@dataclass(frozen=True, slots=True)
class Notice:
    kind: str
    recipient: str
    subject: str
    body: str


def wallet_notice(
    email: str,
    full_name: str,
    address: str,
    previous: str | None,
    ts: int,
    notices: Notices | None = None,
) -> Notice:
    """The email for a payout wallet added (`previous` None) or changed, in
    the wording in force (rule AC12; the built-in wording by default)."""
    values = {
        "full_name": full_name,
        "action": "changed" if previous else "added",
        "time": utc_text(ts),
        "new_wallet": address,
        "previous_wallet_line": f"\n  Previous wallet: {previous}" if previous else "",
    }
    subject, body = (notices or Notices()).render("wallet_changed", values)
    return Notice("wallet_changed", email, subject, body)


async def insert_notice(
    conn: Conn, schema: str, cif: str, notice: Notice, ts: int, detail: dict[str, Any]
) -> str:
    """Add a notice to the outbox, in the caller's transaction; returns NTF."""
    s = check_schema(schema)
    if notice.kind not in NOTICE_KINDS:
        raise ValueError(f"unknown notice kind {notice.kind!r}")
    number = await conn.fetchval(f"SELECT nextval('{s}.ntf_seq')")
    ntf = f"NTF{number:08d}"
    await conn.execute(
        f"INSERT INTO {s}.notifications (ntf, cif, kind, recipient, subject, body, "
        f"created_at, detail) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)",
        ntf,
        cif,
        notice.kind,
        notice.recipient,
        notice.subject,
        notice.body,
        ts,
        json.dumps(detail, sort_keys=True),
    )
    return ntf


@dataclass(frozen=True, slots=True)
class Pending:
    ntf: str
    cif: str
    kind: str
    recipient: str
    subject: str
    body: str
    created_at: int
    last_failed_at: int | None


class NotificationStore:
    def __init__(self, pool: asyncpg.Pool, schema: str) -> None:
        """The customers schema must be migrated (CustomerStore.open)."""
        self._pool = pool
        self._s = check_schema(schema)

    async def pending(self, limit: int = 100) -> list[Pending]:
        """Notices not sent yet, oldest first."""
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT n.ntf, n.cif, n.kind, n.recipient, n.subject, n.body, n.created_at, "
            f"(SELECT max(e.ts) FROM {s}.notification_events e "
            f" WHERE e.ntf = n.ntf AND e.kind = 'failed') AS last_failed_at "
            f"FROM {s}.notifications n WHERE NOT EXISTS (SELECT 1 FROM "
            f"{s}.notification_events e WHERE e.ntf = n.ntf AND e.kind = 'sent') "
            f"ORDER BY n.ntf LIMIT $1",
            limit,
        )
        return [Pending(**dict(r)) for r in rows]

    async def for_customer(self, cif: str) -> list[dict[str, Any]]:
        """The customer's notices with their send attempts."""
        s = self._s
        rows = await self._pool.fetch(
            f"SELECT n.ntf, n.kind, n.recipient, n.subject, n.body, n.created_at, "
            f"coalesce(json_agg(json_build_object('kind', e.kind, 'ts', e.ts, "
            f"'detail', e.detail) ORDER BY e.id) FILTER (WHERE e.id IS NOT NULL), "
            f"'[]')::text AS events FROM {s}.notifications n "
            f"LEFT JOIN {s}.notification_events e USING (ntf) WHERE n.cif = $1 "
            f"GROUP BY n.ntf ORDER BY n.ntf",
            cif,
        )
        return [{**dict(r), "events": json.loads(r["events"])} for r in rows]

    async def record(self, ntf: str, kind: str, ts: int, detail: dict[str, Any]) -> None:
        await self._pool.execute(
            f"INSERT INTO {self._s}.notification_events (ntf, kind, ts, detail) "
            f"VALUES ($1, $2, $3, $4::jsonb)",
            ntf,
            kind,
            ts,
            json.dumps(detail, sort_keys=True),
        )


class Mailer(Protocol):
    @property
    def name(self) -> str: ...

    async def send(self, recipient: str, subject: str, body: str) -> str:
        """Send one email; returns its message id."""
        ...


@dataclass
class SandboxMailer:
    """Sends nothing; keeps what it would have sent (development and tests)."""

    name: str = "sandbox"
    sent: list[tuple[str, str, str]] = field(default_factory=list)

    async def send(self, recipient: str, subject: str, body: str) -> str:
        self.sent.append((recipient, subject, body))
        return f"<sandbox-{len(self.sent)}@propfirm.invalid>"


@dataclass(frozen=True, slots=True)
class SmtpMailer:
    """SMTP with TLS: STARTTLS (port 587) or implicit TLS (port 465); the
    server's certificate is verified."""

    host: str
    port: int
    sender: str  # the From address
    username: str | None = None
    password: str | None = field(default=None, repr=False)  # secret
    tls: str = "starttls"
    timeout_s: float = 30.0
    name: str = "smtp"

    def __post_init__(self) -> None:
        if self.tls not in TLS_MODES:
            raise ValueError(f"tls must be one of {TLS_MODES}")

    async def send(self, recipient: str, subject: str, body: str) -> str:
        return await asyncio.to_thread(self._send, recipient, subject, body)

    def _send(self, recipient: str, subject: str, body: str) -> str:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.sender, recipient, subject
        message_id = make_msgid(domain=self.sender.rsplit("@", 1)[-1])
        message["Message-ID"] = message_id
        message.set_content(body)
        context = ssl.create_default_context()
        if self.tls == "implicit":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout_s, context=context
            )
        else:
            client = smtplib.SMTP(self.host, self.port, timeout=self.timeout_s)
        with client:
            if self.tls == "starttls":
                client.starttls(context=context)
            if self.username:
                client.login(self.username, self.password or "")
            client.send_message(message)
        return message_id


class Notifications:
    def __init__(self, store: NotificationStore, mailer: Mailer) -> None:
        self._store = store
        self._mailer = mailer

    @property
    def store(self) -> NotificationStore:
        return self._store

    async def advance(self, ts: int) -> None:
        """Send what is pending; a failure waits `RETRY_MS` before the next try."""
        for notice in await self._store.pending():
            if notice.last_failed_at is not None and ts < notice.last_failed_at + RETRY_MS:
                continue
            try:
                message_id = await self._mailer.send(notice.recipient, notice.subject, notice.body)
            except Exception as exc:
                log.warning("notice %s: sending failed: %s", notice.ntf, type(exc).__name__)
                detail = {"mailer": self._mailer.name, "error": type(exc).__name__}
                await self._store.record(notice.ntf, "failed", ts, detail)
                continue
            detail = {"mailer": self._mailer.name, "message_id": message_id}
            await self._store.record(notice.ntf, "sent", ts, detail)
