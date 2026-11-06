"""Demo data for showing the platform: never in production.

    docker compose --profile platform run --rm demo-seed

Refuses unless PROPFIRM_SANDBOX=1 (development only). Safe to run again:
what exists is kept, what is missing is added.

What it adds:

1. Keycloak logins (realm `propfirm`, through the admin API's partial import,
   so the rest of the realm is untouched), all with the password `12345`:
   - one employee per level per section: `<section>-<level>@example.com`,
     levels apprentice, associate, specialist, lead (8 sections: 32 logins);
   - two admins (`admin-a@`, `admin-b@`) and two owners (`owner-a@`,
     `owner-b@`);
   - five traders (`trader-a@` to `trader-e@example.com`).
   Every employee login has the same authenticator secret (one entry in an
   authenticator app serves all of them; `DEMO_TOTP_BASE32`).
2. The staff directory, through the directory's own rules (four eyes, the
   training gate): the owners as T5; the admins joined by owner A and
   approved by owner B; each section's lead, specialist and associate joined
   by admin A, trained and approved by admin B; each section's apprentice
   (mentor: the section's lead, a four-month term).
3. Through the API as the traders and staff would act: each trader
   registered, with one paid evaluation account (sandbox payment); an
   integrity case on trader A taken by the integrity lead; an apprentice's
   proposed note on it waiting for the mentor; a request to give the KYC
   associate a level in Payouts (another section) waiting for an owner.

Not included: payouts (they need a funded account with profit, i.e. a
trader passing both evaluation stages).
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx

from propfirm.policy import load_policy
from propfirm.service.config import ServiceConfig
from propfirm.settings.service import Settings
from propfirm.settings.store import SettingsStore
from propfirm.staff.directory import Actor, Directory, Owner
from propfirm.staff.model import ROLES, add_months
from propfirm.staff.records import StaffRefused
from propfirm.staff.store import StaffStore

log = logging.getLogger("propfirm.demo")

# the demo password, asked for; demo logins only, refused outside the sandbox
PASSWORD = "12345"  # nosec B105
DEMO_TOTP_SECRET = b"pipridge-demo-totp"  # raw bytes, as Keycloak stores it
DEMO_TOTP_BASE32 = base64.b32encode(DEMO_TOTP_SECRET).decode().rstrip("=")
LEVELS = ("apprentice", "associate", "specialist", "lead")  # T0 to T3
TRADERS = ("a", "b", "c", "d", "e")
REALM = "propfirm"
_NS = uuid.UUID("0b8b7a4e-6f0e-4c39-9b55-6e0d7c3f9a10")


def demo_id(email: str) -> str:
    """A fixed Keycloak user id per demo login (the same on every machine)."""
    return str(uuid.uuid5(_NS, email))


@dataclass(frozen=True, slots=True)
class Login:
    email: str
    first: str
    last: str
    otp: bool

    @property
    def id(self) -> str:
        return demo_id(self.email)


def logins() -> list[Login]:
    out = [Login(f"owner-{x}@example.com", "Owner", x.upper(), True) for x in "ab"]
    out += [Login(f"admin-{x}@example.com", "Admin", x.upper(), True) for x in "ab"]
    for section in ROLES:
        for level in LEVELS:
            out.append(
                Login(f"{section}-{level}@example.com", section.title(), level.title(), True)
            )
    out += [Login(f"trader-{x}@example.com", "Trader", x.upper(), False) for x in TRADERS]
    return out


def password_credential(password: str, salt: bytes) -> dict[str, Any]:
    """Keycloak's stored form (PBKDF2-SHA512, 210,000 rounds, 512-bit key).
    Imported already hashed: the realm's password policy (12 characters)
    is not applied to these demo logins, by request; real users keep it."""
    digest = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 210_000, dklen=64)
    return {
        "type": "password",
        "temporary": False,
        "secretData": json.dumps(
            {
                "value": base64.b64encode(digest).decode(),
                "salt": base64.b64encode(salt).decode(),
                "additionalParameters": {},
            }
        ),
        "credentialData": json.dumps(
            {"hashIterations": 210_000, "algorithm": "pbkdf2-sha512", "additionalParameters": {}}
        ),
    }


def user_representation(login: Login) -> dict[str, Any]:
    salt = hashlib.sha256(f"pipridge-demo|{login.email}".encode()).digest()[:16]
    credentials: list[dict[str, Any]] = [password_credential(PASSWORD, salt)]
    if login.otp:
        credentials.append(
            {
                "type": "otp",
                "userLabel": "demo authenticator",
                "secretData": json.dumps({"value": DEMO_TOTP_SECRET.decode()}),
                "credentialData": json.dumps(
                    {
                        "subType": "totp",
                        "digits": 6,
                        "counter": 0,
                        "period": 30,
                        "algorithm": "HmacSHA1",
                    }
                ),
            }
        )
    return {
        "id": login.id,
        "username": login.email,
        "email": login.email,
        "emailVerified": True,
        "firstName": login.first,
        "lastName": login.last,
        "enabled": True,
        "credentials": credentials,
        "realmRoles": ["default-roles-propfirm"],  # as every self-registered user
    }


def totp(secret: bytes, now: float) -> str:
    """RFC 6238, as an authenticator app computes it."""
    digest = hmac.new(secret, struct.pack(">Q", int(now // 30)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


# --- 1. Keycloak ------------------------------------------------------------------------


async def keycloak_users(http: httpx.AsyncClient, base: str, admin_password: str) -> int:
    token = await http.post(
        f"{base}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "admin",
            "password": admin_password,
        },
    )
    token.raise_for_status()
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    # OVERWRITE: only these demo logins are replaced (same ids), so a rerun
    # also restores a changed demo password; every other user is untouched
    body = {"ifResourceExists": "OVERWRITE", "users": [user_representation(u) for u in logins()]}
    r = await http.post(f"{base}/admin/realms/{REALM}/partialImport", json=body, headers=headers)
    r.raise_for_status()
    result = r.json()
    done: int = result.get("added", 0) + result.get("overwritten", 0)
    return done


# --- 2. the staff directory ---------------------------------------------------------------


class _NoGateway:
    async def submit(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError("the demo seed changes no settings")


async def directory(config: ServiceConfig, pool: asyncpg.Pool, now: int) -> dict[str, str]:
    """Return email -> STF/APP id."""
    policy = load_policy(config.policy_path)
    settings = Settings(
        policy, await SettingsStore.open(pool, config.settings_schema), _NoGateway()
    )
    owners = [
        Owner(demo_id(f"owner-{x}@example.com"), f"Owner {x.upper()}", f"owner-{x}@example.com")
        for x in "ab"
    ]
    staff = Directory(
        await StaffStore.open(pool, config.staff_schema), settings, owners, legacy_roles=False
    )
    await staff.bootstrap(now)
    owner_a, owner_b = (Actor(o.subject) for o in owners)
    admin_a, admin_b = Actor(demo_id("admin-a@example.com")), Actor(demo_id("admin-b@example.com"))
    sids: dict[str, str] = {}

    async def existing(email: str) -> str | None:
        async with staff._store.read() as ops:  # the seed reads its own rows
            m = await ops.member_by_subject(demo_id(email))
        return None if m is None else m.sid

    async def join(by: Actor, approver: Actor, email: str, name: str, **fields: Any) -> str:
        found = await existing(email)
        if found is not None:
            return found
        trained: list[str] = fields.pop("trained", [])
        member, requested = await staff.join(
            by,
            subject=demo_id(email),
            name=name,
            email=email,
            reason="demo data",
            now=now,
            **{
                "kind": "staff",
                "team": None,
                "lead": None,
                "mentor": None,
                "ends_at": None,
                "tier": None,
                "roles": [],
                **fields,
            },
        )
        for section in trained:
            await staff.record_training(by, member.sid, section, "demo: course passed", now)
        for g in requested:
            await staff.approve(approver, g.grt, "demo data", now)
        return member.sid

    for x in "ab":
        email = f"admin-{x}@example.com"
        sids[email] = await join(owner_a, owner_b, email, f"Admin {x.upper()}", tier=4)
    for section in ROLES:
        lead_email = f"{section}-lead@example.com"
        lead = await join(
            admin_a,
            admin_b,
            lead_email,
            f"{section.title()} Lead",
            team=section,
            sections={section: 3},
            trained=[section],
        )
        sids[lead_email] = lead
        for level, name in ((2, "specialist"), (1, "associate")):
            email = f"{section}-{name}@example.com"
            sids[email] = await join(
                admin_a,
                admin_b,
                email,
                f"{section.title()} {name.title()}",
                team=section,
                lead=lead,
                sections={section: level},
                trained=[section],
            )
        email = f"{section}-apprentice@example.com"
        sids[email] = await join(
            admin_a,
            admin_b,
            email,
            f"{section.title()} Apprentice",
            kind="apprentice",
            team=section,
            mentor=lead,
            sections={section: 0},
            ends_at=add_months(now, 4),
        )

    # waiting for an owner: a level in another section for the KYC associate
    kyc_associate = sids["kyc-associate@example.com"]
    async with staff._store.read() as ops:
        pending = [g for g in await ops.grants_of(kyc_associate) if g.status == "requested"]
    if not pending:
        try:
            await staff.request(
                admin_a,
                kyc_associate,
                "level",
                "payouts:1",
                None,
                None,
                "demo: covers payouts at month end",
                now,
            )
        except StaffRefused as exc:
            log.warning("cross-section request not added: %s", exc)
    return sids


# --- 3. traders, a case, an apprentice's proposal ----------------------------------------------


class Api:
    def __init__(self, http: httpx.AsyncClient, api: str, keycloak: str) -> None:
        self._http, self._api, self._kc = http, api.rstrip("/"), keycloak.rstrip("/")

    async def token(self, email: str, otp: bool) -> str:
        data = {
            "grant_type": "password",
            "client_id": "dev-cli",
            "username": email,
            "password": PASSWORD,
            "scope": "openid",
        }
        if otp:
            data["totp"] = totp(DEMO_TOTP_SECRET, time.time())
        r = await self._http.post(
            f"{self._kc}/realms/{REALM}/protocol/openid-connect/token", data=data
        )
        r.raise_for_status()
        token: str = r.json()["access_token"]
        return token

    async def call(self, token: str, method: str, path: str, body: Any = None) -> httpx.Response:
        return await self._http.request(
            method, f"{self._api}{path}", json=body, headers={"Authorization": f"Bearer {token}"}
        )


TRADER_NAMES = {
    "a": "Ayesha Rahman",
    "b": "Ben Okafor",
    "c": "Chen Wei",
    "d": "Diego Silva",
    "e": "Elif Kaya",
}
COUNTRIES = {"a": "GB", "b": "DE", "c": "SG", "d": "ES", "e": "NL"}


async def traders(api: Api) -> dict[str, str]:
    """Return email -> CIF."""
    out = {}
    for x in TRADERS:
        email = f"trader-{x}@example.com"
        token = await api.token(email, otp=False)
        r = await api.call(
            token,
            "POST",
            "/me",
            {
                "full_name": TRADER_NAMES[x],
                "date_of_birth": "1990-05-17",
                "residence_country": COUNTRIES[x],
            },
        )
        if r.status_code not in (201, 409):
            raise RuntimeError(f"{email}: register {r.status_code} {r.text}")
        me = (await api.call(token, "GET", "/me")).json()
        out[email] = me["cif"]
        if me.get("accounts"):
            continue
        products = (await api.call(token, "GET", "/catalogue")).json()
        product = products[TRADERS.index(x) % len(products)]
        size = product["sizes"][0]["size_cents"]
        order = await api.call(
            token, "POST", "/orders", {"product_id": product["id"], "size_cents": size}
        )
        if order.status_code != 201:
            raise RuntimeError(f"{email}: order {order.status_code} {order.text}")
        paid = await api.call(token, "POST", f"/dev/payments/{order.json()['ord']}/complete")
        if paid.status_code not in (200, 201):
            raise RuntimeError(f"{email}: payment {paid.status_code} {paid.text}")
    return out


async def casework(api: Api, cif: str) -> None:
    lead = await api.token("integrity-lead@example.com", otp=True)
    cases = (await api.call(lead, "GET", f"/staff/cases?cif={cif}")).json()
    if cases:
        return
    alert = {
        "cif": cif,
        "kind": "fraud",
        "severity": "high",
        "reason": "demo: same device as another trader",
    }
    case = await api.call(lead, "POST", "/staff/cases", alert)
    if case.status_code != 201:
        raise RuntimeError(f"case: {case.status_code} {case.text}")
    cas = case.json()["cas"]
    await api.call(lead, "POST", f"/staff/cases/{cas}/assign")
    apprentice = await api.token("integrity-apprentice@example.com", otp=True)
    proposal = {
        "action": "case.note",
        "target": cas,
        "body": {"text": "Same device fingerprint as another account last week."},
    }
    r = await api.call(apprentice, "POST", "/staff/apprentice/proposals", proposal)
    if r.status_code != 201:
        log.warning("apprentice proposal not added: %s %s", r.status_code, r.text)


async def seed(env: dict[str, str]) -> dict[str, Any]:
    if env.get("PROPFIRM_SANDBOX") != "1":
        raise SystemExit("demo data only in the sandbox (PROPFIRM_SANDBOX=1)")
    config = ServiceConfig.from_env(env)
    keycloak = env.get("DEMO_KEYCLOAK_URL", "http://keycloak:8080")
    api_url = env.get("DEMO_API_URL", "http://api:8000")
    now = time.time_ns() // 1_000_000
    async with httpx.AsyncClient(timeout=30) as http:
        added = await keycloak_users(
            http, keycloak, env.get("KEYCLOAK_ADMIN_PASSWORD", "admin-dev")
        )
        pool = await asyncpg.create_pool(config.database_url, min_size=1, max_size=2)
        try:
            sids = await directory(config, pool, now)
        finally:
            await pool.close()
        api = Api(http, api_url, keycloak)
        cifs = await traders(api)
        await casework(api, cifs["trader-a@example.com"])
    return {"keycloak_users": added, "staff": len(sids) + 2, "traders": len(cifs)}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    result = asyncio.run(seed(dict(os.environ)))
    print(json.dumps(result))
    print(f"Password for every demo login: {PASSWORD}")
    print(f"Authenticator (employees): add this key in your app: {DEMO_TOTP_BASE32}")


if __name__ == "__main__":
    main()
