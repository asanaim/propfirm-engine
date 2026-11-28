"""Account sharing detector (build plan step 5, line 3; D-14.35; strategic
review v3, D6).

A sign in is one authentication of a trader's login (the token's
`auth_time`) on one device. The API records it the first time it sees it:
- the device: the portal's own random device id (header `X-Device-Id`,
  kept in the browser), or else the user agent; stored only as a hash;
- the country and the rough place a trusted proxy gives (GeoIP headers),
  the place rounded to 0.1 degree (about 11 km); never the IP address.

Each new sign in is checked against the customer's last 7 days:
- more than 3 devices, or more than 3 countries: a medium fraud alert;
- two sign ins more than 500 km apart within an hour (great circle
  distance): a high fraud alert.
Each alert raised (one per rule per window) also asks for a step up: the
customer's next payout request or wallet change needs a second factor
entered after it. The alert opens a
case, which holds payouts until staff decide.
"""

import hashlib
import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from propfirm.customers.clusters import Alerts

log = logging.getLogger(__name__)

SOURCE = "detector"
DAY_MS = 86_400_000
EARTH_KM = 6_371.0088  # mean radius
PLACE_STEP = 10  # tenths of a degree


def device_hash(device_id: str | None, user_agent: str | None) -> str:
    """A device's hash: its id when the portal sent one, else its agent."""
    raw = f"id:{device_id}" if device_id else f"ua:{user_agent or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def tenths(value: str | None, limit: int) -> int | None:
    """A coordinate header in tenths of a degree (rounded half away from
    zero, explicitly); None when absent or not a coordinate."""
    if value is None:
        return None
    try:
        degrees = float(value)
    except ValueError:
        return None
    if not math.isfinite(degrees) or abs(degrees) > limit:
        return None
    scaled = abs(degrees) * PLACE_STEP
    rounded = math.floor(scaled + 0.5)
    return int(math.copysign(rounded, degrees)) if rounded else 0


def distance_km(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Great circle distance between two places in tenths of a degree."""
    lat1, lon1 = (math.radians(x / PLACE_STEP) for x in a)
    lat2, lon2 = (math.radians(x / PLACE_STEP) for x in b)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


@dataclass(frozen=True, slots=True)
class SignIn:
    at: int  # ms UTC: when it authenticated
    device: str
    country: str | None
    lat: int | None  # tenths of a degree
    lon: int | None


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str  # "devices", "countries" or "travel"
    severity: str
    detail: Mapping[str, Any]


def check(new: SignIn, recent: Iterable[SignIn], policy: Any) -> list[Finding]:
    """The rules for one new sign in against the customer's sign ins of the
    last `window_days` (the new one included)."""
    rows = list(recent)
    out = []
    devices = {r.device for r in rows}
    if len(devices) > policy.max_devices:
        out.append(Finding("devices", "medium", {"devices": len(devices)}))
    countries = {r.country for r in rows if r.country}
    if len(countries) > policy.max_countries:
        out.append(Finding("countries", "medium", {"countries": sorted(countries)}))
    if new.lat is not None and new.lon is not None:
        window = policy.travel_minutes * 60_000
        for r in rows:
            if r.lat is None or r.lon is None or r == new or abs(new.at - r.at) > window:
                continue
            km = distance_km((r.lat, r.lon), (new.lat, new.lon))
            if km > policy.travel_km:
                out.append(
                    Finding(
                        "travel",
                        "high",
                        {
                            "km": int(km),
                            "minutes": abs(new.at - r.at) // 60_000,
                            "countries": [r.country, new.country],
                        },
                    )
                )
                break
    return out


class Subjects(Protocol):
    async def cif_by_subject(self, subject: str) -> Any: ...


class SignIns:
    def __init__(
        self,
        pool: asyncpg.Pool,
        schema: str,
        policy: Any,
        customers: Subjects,
        alerts: Alerts,
    ) -> None:
        self._pool = pool
        self._s = schema
        self._policy = policy
        self._customers = customers
        self._alerts = alerts
        self._seen: set[tuple[str, int, str]] = set()  # (subject, auth time, device)

    async def record(
        self,
        subject: str,
        auth_time: int | None,
        device: str,
        country: str | None,
        lat: int | None,
        lon: int | None,
        now: int,
    ) -> list[Finding]:
        """Record a sign in the first time it is seen; check it. Returns the
        findings raised (none for a sign in already recorded)."""
        policy = (await self._policy.current()).sharing
        if not policy.on:
            return []
        at = auth_time if auth_time is not None else now
        key = (subject, at, device)
        if key in self._seen:
            return []
        cif = await self._customers.cif_by_subject(subject)
        if cif is None:
            return []  # not registered yet
        self._seen.add(key)
        if len(self._seen) > 100_000:  # bounded: the table remembers the rest
            self._seen.clear()
        s = self._s
        inserted = await self._pool.fetchval(
            f"INSERT INTO {s}.sign_ins (cif, at, device, country, lat, lon) "
            f"VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (cif, at, device) DO NOTHING "
            f"RETURNING 1",
            cif.cif,
            at,
            device,
            country,
            lat,
            lon,
        )
        if inserted is None:
            return []
        rows = await self._pool.fetch(
            f"SELECT at, device, country, lat, lon FROM {s}.sign_ins "
            f"WHERE cif = $1 AND at >= $2 ORDER BY at",
            cif.cif,
            at - policy.window_days * DAY_MS,
        )
        new = SignIn(at, device, country, lat, lon)
        recent = [SignIn(r["at"], r["device"], r["country"], r["lat"], r["lon"]) for r in rows]
        found = check(new, recent, policy)
        raised = []
        for f in found:
            if await self._raised(cif.cif, f.rule, now - policy.window_days * DAY_MS):
                continue
            await self._alerts.alert(
                cif.cif,
                None,
                SOURCE,
                "fraud",
                f.severity,
                {"key": f"sharing:{f.rule}:{at}", "detector": "account_sharing", "rule": f.rule}
                | dict(f.detail),
                now,
            )
            raised.append(f)
        if raised:  # asked once per alert, as the case is
            await self._pool.execute(
                f"INSERT INTO {s}.step_ups (cif, at, reason) VALUES ($1, $2, $3) "
                f"ON CONFLICT DO NOTHING",
                cif.cif,
                now,
                ",".join(sorted({f.rule for f in raised})),
            )
        return raised

    async def _raised(self, cif: str, rule: str, since: int) -> bool:
        """One alert per rule per window: the case already holds the rest."""
        found = await self._pool.fetchval(
            f"SELECT 1 FROM {self._s}.alerts WHERE cif = $1 AND source = $2 "
            f"AND detail->>'detector' = 'account_sharing' AND detail->>'rule' = $3 "
            f"AND ts >= $4 LIMIT 1",
            cif,
            SOURCE,
            rule,
            since,
        )
        return found is not None

    async def step_up_since(self, cif: str) -> int | None:
        """When the customer was last asked to step up, if ever."""
        found = await self._pool.fetchval(
            f"SELECT max(at) FROM {self._s}.step_ups WHERE cif = $1", cif
        )
        return None if found is None else int(found)

    async def of(self, cif: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT at, device, country, lat, lon FROM {self._s}.sign_ins WHERE cif = $1 "
            f"ORDER BY at DESC LIMIT $2",
            cif,
            limit,
        )
        return [dict(r) for r in rows]
