"""Identity verification (Tier 2 KYC, outline M05): Didit, and its sandbox.

A verification is a provider session the customer completes on the
provider's page. Its outcome is read from the provider, never from what a
webhook body claims: a webhook only says "look again" (and is checked for
its signature first).

Didit (API v3, docs.didit.me):
- create: POST {base}/v3/session/ with header x-api-key, body
  {workflow_id, vendor_data, callback}; answers session_id and url;
- decision: GET {base}/v3/session/{session_id}/decision/; `status` is one of
  "Not Started", "In Progress", "In Review", "Approved", "Declined",
  "Resubmitted", "Awaiting User", "Abandoned", "Expired", "Kyc Expired";
  `id_verifications[]` carries full_name (or first_name and last_name),
  date_of_birth and issuing_state (ISO 3166-1 alpha-3);
- webhooks: header X-Signature-V2 is the hex HMAC-SHA256, keyed with the
  webhook secret, of the body re-encoded canonically (whole-valued floats as
  integers, keys sorted, compact separators, Unicode kept); X-Timestamp
  (Unix seconds) must be within 300 s of now.
Only "Approved" and "Declined" are final here; every other status waits.
"""

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

import httpx
import pycountry

from propfirm.customers.providers import Screening

DIDIT_BASE = "https://verification.didit.me"
SIGNATURE_WINDOW_S = 300


class KycError(Exception):
    """The provider could not be reached or answered something unusable."""


@dataclass(frozen=True, slots=True)
class KycSession:
    provider: str
    session_id: str
    url: str  # where the customer completes the verification


@dataclass(frozen=True, slots=True)
class KycDecision:
    """A final outcome ("approved" or "declined"), or None while pending,
    with the verified identity when approved."""

    result: str | None
    full_name: str | None = None
    date_of_birth: date | None = None
    document_country: str | None = None  # ISO 3166-1 alpha-2
    detail: dict[str, Any] = field(default_factory=dict)


class KycProvider(Protocol):
    name: str

    async def start(self, reference: str, callback_url: str | None) -> KycSession: ...

    async def decision(self, session_id: str) -> KycDecision: ...


def alpha2(country: str | None) -> str | None:
    """An ISO 3166-1 alpha-3 (or alpha-2) code as alpha-2; None if unknown."""
    if not country:
        return None
    code = country.strip().upper()
    found = (
        pycountry.countries.get(alpha_3=code)
        if len(code) == 3
        else pycountry.countries.get(alpha_2=code)
    )
    return None if found is None else str(found.alpha_2)


# --- Didit ----------------------------------------------------------------------------


def _shorten_floats(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _shorten_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shorten_floats(v) for v in value]
    return value


def didit_signature(body: bytes, secret: str) -> str:
    """The X-Signature-V2 value Didit sends for `body`."""
    canonical = json.dumps(
        _shorten_floats(json.loads(body)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hmac.new(secret.encode(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def didit_webhook_valid(
    body: bytes, signature: str | None, timestamp: str | None, secret: str, now_s: int
) -> bool:
    """Whether a webhook came from Didit: a matching signature and a
    timestamp within SIGNATURE_WINDOW_S of now. Malformed input is invalid."""
    if not signature or not timestamp or not secret:
        return False
    try:
        sent = int(timestamp)
        expected = didit_signature(body, secret)
    except ValueError:  # not an integer, or not JSON
        return False
    if abs(now_s - sent) > SIGNATURE_WINDOW_S:
        return False
    return hmac.compare_digest(expected, signature.strip().lower())


def didit_decision(data: dict[str, Any]) -> KycDecision:
    """A Didit decision body as a KycDecision."""
    status = data.get("status")
    if status == "Declined":
        return KycDecision("declined", detail={"status": status})
    if status != "Approved":
        return KycDecision(None, detail={"status": status})
    checks = data.get("id_verifications") or []
    if not isinstance(checks, list) or not checks or not isinstance(checks[0], dict):
        raise KycError("an approved Didit decision without an id verification")
    doc = checks[0]
    name = doc.get("full_name") or " ".join(
        str(p) for p in (doc.get("first_name"), doc.get("last_name")) if p
    )
    try:
        born = date.fromisoformat(str(doc["date_of_birth"])) if doc.get("date_of_birth") else None
    except ValueError as exc:
        raise KycError(f"date_of_birth {doc.get('date_of_birth')!r}") from exc
    return KycDecision(
        "approved",
        full_name=str(name) or None,
        date_of_birth=born,
        document_country=alpha2(doc.get("issuing_state")),
        detail={
            "status": status,
            "document_type": doc.get("document_type"),
            "issuing_state": doc.get("issuing_state"),
        },
    )


class DiditKyc:
    name = "didit"

    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        workflow_id: str,
        base_url: str = DIDIT_BASE,
    ) -> None:
        self._http = http
        self._headers = {"x-api-key": api_key}
        self._workflow = workflow_id
        self._base = base_url.rstrip("/")

    async def start(self, reference: str, callback_url: str | None) -> KycSession:
        body: dict[str, Any] = {"workflow_id": self._workflow, "vendor_data": reference}
        if callback_url:
            body["callback"] = callback_url
        data = await self._call("POST", "/v3/session/", body)
        try:
            return KycSession(self.name, str(data["session_id"]), str(data["url"]))
        except KeyError as exc:
            raise KycError(f"Didit session answer without {exc}") from exc

    async def decision(self, session_id: str) -> KycDecision:
        return didit_decision(await self._call("GET", f"/v3/session/{session_id}/decision/"))

    async def _call(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        try:
            answer = await self._http.request(
                method, self._base + path, headers=self._headers, json=body, timeout=15.0
            )
        except httpx.HTTPError as exc:
            raise KycError(f"Didit unreachable: {exc}") from exc
        if answer.status_code >= 400:
            raise KycError(f"Didit {method} {path}: HTTP {answer.status_code}")
        data = answer.json()
        if not isinstance(data, dict):
            raise KycError(f"Didit {method} {path}: not a JSON object")
        return data


class WorkflowScreening:
    """AML screening done inside the Didit verification workflow: an
    approved decision has passed it (the workflow must include Didit's AML
    step, C7), so the check at this point records that and finds nothing new."""

    async def screen(self, full_name: str, date_of_birth: str, country: str) -> Screening:
        return Screening("didit-workflow", "clear", {"screened_in": "didit_workflow"})


# --- sandbox --------------------------------------------------------------------------


class SandboxKyc:
    """Development stand-in: a session stays pending until the sandbox
    endpoint completes it (`complete`). Never in production."""

    name = "sandbox"

    def __init__(self) -> None:
        self._decisions: dict[str, KycDecision] = {}

    async def start(self, reference: str, callback_url: str | None) -> KycSession:
        session_id = f"sandbox-{reference}-{uuid.uuid4().hex[:12]}"
        return KycSession(self.name, session_id, f"/dev/kyc/{session_id}/complete")

    async def decision(self, session_id: str) -> KycDecision:
        return self._decisions.get(session_id, KycDecision(None))

    def complete(self, session_id: str, decision: KycDecision) -> None:
        self._decisions[session_id] = decision
