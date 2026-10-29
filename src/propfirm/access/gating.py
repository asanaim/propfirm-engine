"""Country gating, switched on or off in the policy file.

When `access.country_gating` is true, a customer is refused if any country
signal (ID document, IP address, device) is in `access.blocked_countries`.
When false, nobody is refused on country grounds. Registration and
verification (step 2) call this; the decision and its reasons are recorded
with the customer.
"""

import re
from dataclasses import dataclass

from propfirm.policy import AccessPolicy

_COUNTRY = re.compile(r"[A-Z]{2}")


@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    reasons: tuple[str, ...]  # e.g. ("document:BD",); empty when allowed


def check_country(
    policy: AccessPolicy,
    *,
    document: str | None = None,
    ip: str | None = None,
    device: str | None = None,
) -> GateDecision:
    signals = {"document": document, "ip": ip, "device": device}
    for name, code in signals.items():
        if code is not None and not _COUNTRY.fullmatch(code):
            raise ValueError(f"{name} country must be ISO alpha-2, got {code!r}")
    if not policy.country_gating:
        return GateDecision(True, ())
    reasons = tuple(
        f"{name}:{code}"
        for name, code in signals.items()
        if code is not None and code in policy.blocked_countries
    )
    return GateDecision(not reasons, reasons)
