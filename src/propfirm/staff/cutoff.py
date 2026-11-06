"""Leavers lose every login at once [decided 6 Nov 2026; staff spec 5d].

The directory ends a leaver's access in the platform the moment they are
marked left (`Directory.leave`). This module ends it at the identity
provider too, so no session or token can be used again:

1. a cut-off is requested for every member who left, graduated (the
   apprentice login), or reached their end date (apprentices, contractors);
2. each round (every minute) asks Keycloak, through a service account with
   `manage-users` only, to disable the user and end all their sessions;
   retried until done;
3. not done within `leaver_cutoff_alert_minutes` (60): an urgent
   notification to the staff directory's holders (leads, admins) to do it
   by hand in Keycloak and confirm it (`POST /staff/cutoffs/{sid}/done`);
   past `leaver_cutoff_max_hours` (24, the hard limit): again, urgent.

Access tokens already issued stay valid until they expire (Keycloak's
default 5 minutes) but the API refuses them at once: it reads the directory
on every staff request. Records are append-only (`staff.access_cutoffs`).
"""

import logging
from typing import Protocol

import httpx

from propfirm.staff.desk import StaffDesk
from propfirm.staff.records import Cutoff, Member
from propfirm.staff.store import StaffStore

log = logging.getLogger(__name__)

AUDIENCE = "staff.directory"  # leads and admins


class IdentityError(Exception):
    pass


class IdentityProvider(Protocol):
    name: str

    async def disable(self, subject: str) -> None:
        """Disable the login and end every session it has."""


class KeycloakAdmin:
    """Keycloak's admin REST API with a client-credentials service account
    (`manage-users` in `realm-management`, nothing else)."""

    name = "keycloak"

    def __init__(
        self, http: httpx.AsyncClient, base_url: str, realm: str, client_id: str, secret: str
    ) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._realm = realm
        self._client = client_id
        self._secret = secret

    async def _token(self) -> str:
        response = await self._http.post(
            f"{self._base}/realms/{self._realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client,
                "client_secret": self._secret,
            },
            timeout=10.0,
        )
        if response.status_code != 200:
            raise IdentityError(f"service account token: HTTP {response.status_code}")
        token: str = response.json()["access_token"]
        return token

    async def disable(self, subject: str) -> None:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        user = f"{self._base}/admin/realms/{self._realm}/users/{subject}"
        updated = await self._http.put(user, json={"enabled": False}, headers=headers, timeout=10.0)
        if updated.status_code == 404:
            return  # no such login: nothing to end
        if updated.status_code not in (200, 204):
            raise IdentityError(f"disable: HTTP {updated.status_code}")
        logout = await self._http.post(f"{user}/logout", headers=headers, timeout=10.0)
        if logout.status_code not in (200, 204):
            raise IdentityError(f"logout: HTTP {logout.status_code}")


class SandboxIdentity:
    """Development: records the logins it would disable."""

    name = "sandbox"

    def __init__(self) -> None:
        self.disabled: list[str] = []

    async def disable(self, subject: str) -> None:
        self.disabled.append(subject)


class Cutoffs:
    def __init__(
        self,
        store: StaffStore,
        provider: IdentityProvider | None,
        desk: StaffDesk | None,
        alert_minutes: int = 60,
        max_hours: int = 24,
    ) -> None:
        self._store = store
        self._provider = provider
        self._desk = desk
        self._alert_ms = alert_minutes * 60_000
        self._max_ms = max_hours * 3_600_000

    async def all(self) -> list[Cutoff]:
        async with self._store.read() as ops:
            return await ops.cutoffs()

    async def request_due(self, now: int) -> list[str]:
        """Request a cut-off for every member whose access has ended and who
        has none yet (left, graduated, end date passed)."""
        added = []
        async with self._store.change() as ops:
            known = {c.sid for c in await ops.cutoffs()}
            for m in await ops.members():
                reason = _ended(m, now)
                if reason is None or m.sid in known:
                    continue
                await ops.cutoff_event(m.sid, m.subject, "requested", reason, now)
                added.append(m.sid)
        return added

    async def advance(self, now: int) -> None:
        """One round: request what is due, disable at the provider, alert
        on what is late."""
        await self.request_due(now)
        for c in await self.all():
            if c.done_at is not None:
                continue
            if self._provider is not None:
                try:
                    await self._provider.disable(c.subject)
                except Exception as exc:  # retried next round; alerted if late
                    log.warning("cut-off of %s at %s failed: %s", c.sid, self._provider.name, exc)
                    async with self._store.change() as ops:
                        detail = f"{type(exc).__name__}: {exc}"
                        await ops.cutoff_event(c.sid, c.subject, "failed", detail, now)
                else:
                    async with self._store.change() as ops:
                        await ops.cutoff_event(c.sid, c.subject, "done", self._provider.name, now)
                    if self._desk is not None:
                        await self._desk.close(f"cutoff:{c.sid}", "login disabled", now)
                    continue
            await self._alert(c, now)

    async def _alert(self, c: Cutoff, now: int) -> None:
        late = now - c.requested_at
        for kind, after in (("alerted", self._alert_ms), ("overdue", self._max_ms)):
            if late < after or kind in c.alerted:
                continue
            async with self._store.change() as ops:
                await ops.cutoff_event(c.sid, c.subject, kind, None, now)
            if self._desk is None:
                continue
            hours = "24 hours (the hard limit)" if kind == "overdue" else "1 hour"
            await self._desk.notify(
                AUDIENCE,
                f"cutoff_{kind}",
                f"cutoff:{c.sid}",
                f"Leaver's login still active after {hours}: {c.sid}",
                f"{c.sid} ({c.reason}) still has its login at the identity provider"
                f"{f'; last error: {c.last_error}' if c.last_error else ''}. Disable the user "
                f"and end its sessions in Keycloak by hand, then confirm: "
                f"POST /staff/cutoffs/{c.sid}/done.",
                now,
                urgent=True,
            )

    async def confirm(self, sid: str, by: str, now: int) -> Cutoff:
        """Confirm a cut-off done by hand at the identity provider."""
        async with self._store.change() as ops:
            found = {c.sid: c for c in await ops.cutoffs()}.get(sid)
            if found is None:
                raise IdentityError(f"no cut-off for {sid}")
            if found.done_at is None:
                await ops.cutoff_event(sid, found.subject, "confirmed", f"by hand: {by}", now)
        if self._desk is not None:
            await self._desk.close(f"cutoff:{sid}", "confirmed by hand", now)
        return {c.sid: c for c in await self.all()}[sid]


def _ended(m: Member, now: int) -> str | None:
    if m.status == "left":
        return "left"
    if m.status == "graduated":
        return "graduated"
    if m.ends_at is not None and now >= m.ends_at:
        return "ended"
    return None


def cutoff_view(c: Cutoff, now: int, alert_ms: int, max_ms: int) -> dict[str, object]:
    late = now - c.requested_at
    status = (
        "done"
        if c.done_at is not None
        else "overdue"
        if late >= max_ms
        else "late"
        if (late >= alert_ms)
        else "pending"
    )
    return {
        "sid": c.sid,
        "subject": c.subject,
        "reason": c.reason,
        "requested_at": c.requested_at,
        "done_at": c.done_at,
        "how": c.how,
        "last_error": c.last_error,
        "status": status,
    }
