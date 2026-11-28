"""Extensions: live chat, captcha and analytics (rule AC22; D-15.3).

One slot each, with one provider each, chosen for the lowest cost:
- chat: Crisp (free plan), configured by its public website id;
- captcha: Cloudflare Turnstile (free, no puzzle for most visitors), by its
  public site key and its secret key;
- analytics: Cloudflare Web Analytics (free, no cookies, so no consent
  banner), by its public beacon token.

An extension is switched on, off or reconfigured through a settings
proposal that a second admin or owner approves (AC00). Its secret (the
captcha's secret key) is write-only and encrypted like a payment method's
(AC14), under `ext-captcha` / `secret_key`; like any secret, an owner
approves it (AC02).

The portal's content security policy names these providers' hosts only;
a slot with another provider would need that policy changed too.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from propfirm.policy import PolicyError

EXTENSION = "extension"
PROVIDERS = {"chat": "crisp", "captcha": "turnstile", "analytics": "cloudflare"}
# each provider's public id: a Crisp website id (a UUID), a Turnstile site
# key, a Cloudflare Web Analytics token
PUBLIC_ID = {
    "crisp": re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
    "turnstile": re.compile(r"[0-9]x[0-9A-Za-z_-]{10,64}"),
    "cloudflare": re.compile(r"[0-9a-f]{32}"),
}
SECRETS = {"captcha": "secret_key"}  # the slots with a secret, and its name


def secret_owner(slot: str) -> str:
    """The id a slot's secret is sealed under (with the methods' vault)."""
    return f"ext-{slot}"


@dataclass(frozen=True, slots=True)
class Extension:
    slot: str
    enabled: bool
    public_id: str

    def __post_init__(self) -> None:
        provider = PROVIDERS.get(self.slot)
        if provider is None:
            raise PolicyError(f"slot is one of {sorted(PROVIDERS)}")
        if not isinstance(self.enabled, bool):
            raise PolicyError("enabled is true or false")
        needs_id = self.enabled or self.public_id
        if needs_id and not PUBLIC_ID[provider].fullmatch(self.public_id):
            raise PolicyError(f"not a {provider} id")

    @property
    def provider(self) -> str:
        return PROVIDERS[self.slot]


def extension_json(e: Extension) -> dict[str, Any]:
    return {"slot": e.slot, "enabled": e.enabled, "public_id": e.public_id}


def extension_from_json(v: Mapping[str, Any]) -> Extension:
    return Extension(str(v["slot"]), v["enabled"], str(v["public_id"]))


def extensions_in_force(settings: Iterable[object]) -> dict[str, Extension]:
    out: dict[str, Extension] = {}
    for s in settings:
        if isinstance(s, Extension):
            out[s.slot] = s
    return out


def public_view(extensions: Mapping[str, Extension]) -> dict[str, Any]:
    """What the portal reads (public): the enabled slots and their public
    ids; never a secret."""
    return {
        slot: (
            {"provider": e.provider, "public_id": e.public_id}
            if (e := extensions.get(slot)) is not None and e.enabled
            else None
        )
        for slot in PROVIDERS
    }
