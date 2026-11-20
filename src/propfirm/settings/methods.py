"""Payment and withdrawal methods, and their secrets (rules AC02, AC13, AC14;
decided 8 Nov 2026).

A method is attached, changed, enabled or disabled through a settings
proposal: an admin or owner proposes, an owner who is not the proposer
approves (AC02). Each method names an adapter, the code that talks to its
provider; the buyer of the platform attaches methods for the adapters
installed, without code changes for the method itself.

Secrets (API keys, client secrets, merchant ids) are write-only (AC14):
encrypted (AES-256-GCM) before they are stored, with a key held outside
the database (`PROPFIRM_SECRETS_KEY`); never shown again, only that they
are set; replacing one is a proposal like any other change.
"""

import base64
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from propfirm.policy import PolicyError, parse_percent, percent_text

PAYMENT_METHOD = "payment_method"
METHOD_SECRET = "method_secret"  # nosec B105
METHOD_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
SECRET_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
KINDS = ("payment", "withdrawal")
# the adapters this build has: a method can only name one of them
ADAPTERS = ("sandbox", "manual", "nowpayments")  # D-14.24
FIELD_TYPES = ("text", "number", "email")


@dataclass(frozen=True, slots=True)
class UserField:
    """A field the trader fills for this method (a wallet address, an IBAN)."""

    name: str
    label: str
    type: str = "text"
    required: bool = True


@dataclass(frozen=True, slots=True)
class PaymentMethod:
    method_id: str
    kind: str  # payment (buying a challenge) or withdrawal (a payout)
    name: str
    adapter: str
    enabled: bool
    min_cents: int
    max_cents: int  # 0: no maximum
    fixed_charge_cents: int
    percent_charge: Fraction  # a share: 1/50 is 2 %
    instructions: str = ""
    user_fields: tuple[UserField, ...] = ()

    def __post_init__(self) -> None:
        if not METHOD_ID.fullmatch(self.method_id):
            raise PolicyError("method id: lowercase letters, digits and dashes, up to 40")
        if self.kind not in KINDS:
            raise PolicyError(f"kind must be one of {KINDS}")
        if not 1 <= len(self.name.strip()) <= 60:
            raise PolicyError("name: 1 to 60 characters")
        if self.adapter not in ADAPTERS:
            raise PolicyError(f"adapter must be one of {ADAPTERS}")
        if self.min_cents < 0 or self.max_cents < 0 or self.fixed_charge_cents < 0:
            raise PolicyError("amounts are whole cents, not negative")
        if self.max_cents and self.max_cents < self.min_cents:
            raise PolicyError("the maximum must be 0 (none) or at least the minimum")
        if not 0 <= self.percent_charge < 1:
            raise PolicyError("the percent charge must be from 0 to below 100")
        if len(self.instructions) > 2_000:
            raise PolicyError("instructions: up to 2000 characters")
        names = [f.name for f in self.user_fields]
        if len(names) != len(set(names)) or len(names) > 10:
            raise PolicyError("user fields: up to 10, each named once")
        for f in self.user_fields:
            if not SECRET_NAME.fullmatch(f.name) or not 1 <= len(f.label.strip()) <= 60:
                raise PolicyError("a user field has a lowercase name and a label")
            if f.type not in FIELD_TYPES:
                raise PolicyError(f"a user field's type is one of {FIELD_TYPES}")

    def allows(self, amount_cents: int) -> bool:
        return amount_cents >= self.min_cents and (
            self.max_cents == 0 or amount_cents <= self.max_cents
        )

    def charge_cents(self, amount_cents: int) -> int:
        """The method's charge on an amount: the fixed charge plus the percent,
        rounded UP to a whole cent (named rounding, CLAUDE.md: the firm never
        undercharges by a fraction of a cent)."""
        share = self.percent_charge * amount_cents
        return self.fixed_charge_cents + -(-share.numerator // share.denominator)


@dataclass(frozen=True, slots=True)
class MethodSecret:
    """One secret of a method, encrypted; the plain value is never kept."""

    method_id: str
    name: str
    nonce: str  # base64
    ciphertext: str  # base64

    def __post_init__(self) -> None:
        if not METHOD_ID.fullmatch(self.method_id) or not SECRET_NAME.fullmatch(self.name):
            raise PolicyError("a secret names its method and itself in lowercase")


class SecretsUnavailable(Exception):
    """No key to encrypt or decrypt secrets with (PROPFIRM_SECRETS_KEY)."""


class Vault:
    """AES-256-GCM with a 32-byte key from outside the database. The method id
    and secret name are bound to each ciphertext (associated data)."""

    def __init__(self, key: bytes | None) -> None:
        if key is not None and len(key) != 32:
            raise ValueError("the secrets key is 32 bytes")
        self._key = key

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Vault:
        raw = env.get("PROPFIRM_SECRETS_KEY", "")
        return cls(base64.b64decode(raw, validate=True) if raw else None)

    def _aes(self) -> AESGCM:
        if self._key is None:
            raise SecretsUnavailable("PROPFIRM_SECRETS_KEY is not set")
        return AESGCM(self._key)

    def seal(self, method_id: str, name: str, plain: str, nonce: bytes) -> MethodSecret:
        """`nonce`: 12 fresh random bytes from the caller (os.urandom(12))."""
        data = self._aes().encrypt(nonce, plain.encode(), f"{method_id}:{name}".encode())
        return MethodSecret(
            method_id, name, base64.b64encode(nonce).decode(), base64.b64encode(data).decode()
        )

    def open(self, secret: MethodSecret) -> str:
        nonce = base64.b64decode(secret.nonce)
        data = base64.b64decode(secret.ciphertext)
        aad = f"{secret.method_id}:{secret.name}".encode()
        return self._aes().decrypt(nonce, data, aad).decode()


# --- stored form -----------------------------------------------------------------------


def method_json(m: PaymentMethod) -> dict[str, Any]:
    return {
        "method_id": m.method_id,
        "kind": m.kind,
        "name": m.name,
        "adapter": m.adapter,
        "enabled": m.enabled,
        "min_cents": m.min_cents,
        "max_cents": m.max_cents,
        "fixed_charge_cents": m.fixed_charge_cents,
        "percent_charge": percent_text(m.percent_charge),
        "instructions": m.instructions,
        "user_fields": [
            {"name": f.name, "label": f.label, "type": f.type, "required": f.required}
            for f in m.user_fields
        ],
    }


def _cents(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PolicyError(f"{where} is whole cents")
    return value


def method_from_json(v: Mapping[str, Any]) -> PaymentMethod:
    percent = parse_percent(v["percent_charge"], "percent_charge")
    assert percent is not None
    enabled = v["enabled"]
    if not isinstance(enabled, bool):
        raise PolicyError("enabled is true or false")
    fields = v.get("user_fields", [])
    if not isinstance(fields, list):
        raise PolicyError("user fields are a list")
    return PaymentMethod(
        str(v["method_id"]),
        str(v["kind"]),
        str(v["name"]),
        str(v["adapter"]),
        enabled,
        _cents(v["min_cents"], "min_cents"),
        _cents(v["max_cents"], "max_cents"),
        _cents(v["fixed_charge_cents"], "fixed_charge_cents"),
        percent,
        str(v.get("instructions", "")),
        tuple(
            UserField(
                str(f["name"]),
                str(f["label"]),
                str(f.get("type", "text")),
                bool(f.get("required", True)),
            )
            for f in fields
        ),
    )


def secret_json(s: MethodSecret) -> dict[str, Any]:
    return {"method_id": s.method_id, "name": s.name, "nonce": s.nonce, "ciphertext": s.ciphertext}


def secret_from_json(v: Mapping[str, Any]) -> MethodSecret:
    return MethodSecret(str(v["method_id"]), str(v["name"]), str(v["nonce"]), str(v["ciphertext"]))


def masked_secret(s: MethodSecret) -> dict[str, Any]:
    """What any view shows of a secret: which one, never its value."""
    return {"method_id": s.method_id, "name": s.name, "value": "set (hidden)"}


# --- in force ---------------------------------------------------------------------------


def methods_in_force(settings: Iterable[object]) -> dict[str, PaymentMethod]:
    out: dict[str, PaymentMethod] = {}
    for s in settings:
        if isinstance(s, PaymentMethod):
            out[s.method_id] = s
    return out


def secrets_in_force(settings: Iterable[object]) -> dict[tuple[str, str], MethodSecret]:
    out: dict[tuple[str, str], MethodSecret] = {}
    for s in settings:
        if isinstance(s, MethodSecret):
            out[(s.method_id, s.name)] = s
    return out
