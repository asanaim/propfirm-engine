"""Subscribers with a consent record (rule AC25; D-15.1).

Anyone may ask for the firm's news by email. The record of consent is the
point (AC25: time, source, wording shown), so every step is an
append-only event:
- subscribed: the email, the time, where on the site (`source`), the
  exact wording the API showed beside the button and its version, the
  visitor's country from the proxy; a confirmation email goes out;
- confirmed: the subscriber followed the emailed link (double opt-in);
  only confirmed subscribers are on the mailing list;
- unsubscribed: by the link in every email, at any time.

Tokens are random, sent once, and stored only as hashes. Asking again
while a confirmation is waiting sends nothing new for 24 hours. The answer
never says whether an address is already on the list. At most
`max_per_hour` confirmation emails leave each hour (the form is public).
"""

import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

HOUR_MS = 3_600_000
CONFIRM_HOURS = 7 * 24  # a confirmation link works this long
RESEND_HOURS = 24
EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,63}")
SOURCES = frozenset({"footer", "home", "blog", "checkout"})


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def wording_version(wording: str) -> str:
    return sha(wording)[:12]


class SubscribeRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Mailer(Protocol):
    async def send(self, recipient: str, subject: str, body: str) -> str: ...


@dataclass(frozen=True, slots=True)
class Consent:
    email: str
    status: str  # "pending", "confirmed" or "unsubscribed"
    subscribed_at: int
    source: str
    wording: str
    wording_version: str
    country: str | None
    confirmed_at: int | None
    unsubscribed_at: int | None


class Subscribers:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        policy: Any,
        mailer: Mailer | None,
        public_url: str | None,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._policy = policy
        self._mailer = mailer
        self._url = (public_url or "").rstrip("/")
        self._sent: list[int] = []  # confirmation emails in the last hour

    async def wording(self) -> dict[str, Any]:
        p = (await self._policy.current()).subscribers
        return {"on": p.on, "wording": p.wording, "version": wording_version(p.wording)}

    async def subscribe(
        self, email: str, source: str, version: str, country: str | None, now: int
    ) -> None:
        """Record the request with its consent and send the confirmation.
        `version` is the wording version the visitor saw: an older one is
        refused, so the record never names wording they did not see."""
        p = (await self._policy.current()).subscribers
        if not p.on:
            raise SubscribeRefused("closed", "the newsletter is closed")
        address = email.strip().lower()
        if not EMAIL.fullmatch(address):
            raise SubscribeRefused("bad_email", "enter a valid email address")
        if source not in SOURCES:
            raise SubscribeRefused("bad_source", f"source is one of {sorted(SOURCES)}")
        if version != wording_version(p.wording):
            raise SubscribeRefused("old_wording", "the page is out of date: reload it")
        if self._mailer is None:
            raise SubscribeRefused("no_mail", "email is not configured")
        last = await self._last(address)
        if last is not None:
            kind, at = last["kind"], int(last["at"])
            if kind == "confirmed":
                return  # already on the list: nothing to send, nothing said
            if kind == "subscribed" and now - at < RESEND_HOURS * HOUR_MS:
                return
        self._sent = [t for t in self._sent if now - t < HOUR_MS]
        if len(self._sent) >= p.max_per_hour:
            raise SubscribeRefused("busy", "too many requests: try again later")
        self._sent.append(now)
        confirm = secrets.token_urlsafe(32)
        leave = secrets.token_urlsafe(32)
        await self._event(
            address,
            "subscribed",
            now,
            {
                "source": source,
                "wording": p.wording,
                "wording_version": wording_version(p.wording),
                "country": country,
                "confirm_sha": sha(confirm),
                "unsubscribe_sha": sha(leave),
            },
        )
        await self._mailer.send(
            address,
            "Confirm your subscription",
            f"You asked for Pipridge news at this address.\n\n"
            f"Confirm: {self._url}/subscribe/confirm?token={confirm}\n\n"
            f"If it was not you, ignore this email: nothing more will be sent.\n"
            f"Unsubscribe at any time: {self._url}/unsubscribe?token={leave}\n",
        )

    async def confirm(self, token: str, now: int) -> bool:
        found = await self._by_token("confirm_sha", token)
        if found is None:
            return False
        address, at = str(found["email"]), int(found["at"])
        last = await self._last(address)
        if last is None or last["kind"] != "subscribed" or int(last["at"]) != at:
            return False  # superseded, confirmed or left since
        if now - at > CONFIRM_HOURS * HOUR_MS:
            return False
        await self._event(address, "confirmed", now, {})
        return True

    async def unsubscribe(self, token: str, now: int) -> bool:
        found = await self._by_token("unsubscribe_sha", token)
        if found is None:
            return False
        address = str(found["email"])
        last = await self._last(address)
        if last is None or last["kind"] == "unsubscribed":
            return True  # already off the list
        await self._event(address, "unsubscribed", now, {})
        return True

    async def _event(self, email: str, kind: str, at: int, detail: Mapping[str, Any]) -> None:
        await self._pool.execute(
            f"INSERT INTO {self._s}.subscriber_events (email, kind, at, detail) "
            f"VALUES ($1, $2, $3, $4::jsonb)",
            email,
            kind,
            at,
            json.dumps(dict(detail), sort_keys=True),
        )

    async def _last(self, email: str) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            f"SELECT kind, at FROM {self._s}.subscriber_events WHERE email = $1 "
            f"ORDER BY seq DESC LIMIT 1",
            email,
        )

    async def _by_token(self, field: str, token: str) -> asyncpg.Record | None:
        if not token or len(token) > 100:
            return None
        return await self._pool.fetchrow(
            f"SELECT email, at FROM {self._s}.subscriber_events WHERE kind = 'subscribed' "
            f"AND detail->>$1 = $2 ORDER BY seq DESC LIMIT 1",
            field,
            sha(token),
        )

    async def consents(self, status: str | None = None) -> list[Consent]:
        """Each address's consent record: the subscription it stands on and
        what happened after, newest first."""
        rows = await self._pool.fetch(
            f"WITH last_sub AS ("
            f"  SELECT DISTINCT ON (email) seq, email, at, detail FROM {self._s}.subscriber_events "
            f"  WHERE kind = 'subscribed' ORDER BY email, seq DESC) "
            f"SELECT s.email, s.at, s.detail::text AS detail, "
            f"(SELECT max(at) FROM {self._s}.subscriber_events c WHERE c.email = s.email "
            f" AND c.kind = 'confirmed' AND c.seq > s.seq) AS confirmed_at, "
            f"(SELECT max(at) FROM {self._s}.subscriber_events u WHERE u.email = s.email "
            f" AND u.kind = 'unsubscribed' AND u.seq > s.seq) AS unsubscribed_at "
            f"FROM last_sub s ORDER BY s.at DESC"
        )
        out = []
        for r in rows:
            d = json.loads(r["detail"])
            state = (
                "unsubscribed"
                if r["unsubscribed_at"] is not None
                else "confirmed"
                if r["confirmed_at"] is not None
                else "pending"
            )
            if status is not None and state != status:
                continue
            out.append(
                Consent(
                    str(r["email"]),
                    state,
                    int(r["at"]),
                    str(d["source"]),
                    str(d["wording"]),
                    str(d["wording_version"]),
                    d.get("country"),
                    None if r["confirmed_at"] is None else int(r["confirmed_at"]),
                    None if r["unsubscribed_at"] is None else int(r["unsubscribed_at"]),
                )
            )
        return out
