"""Country gating, switched on or off in the policy file.

When `access.country_gating` is true, a customer is refused if any country
signal (declared residence, ID document, IP address, device) is in
`access.blocked_countries`.
When false, nobody is refused on country grounds. Registration and
verification (step 2) call this; the decision and its reasons are recorded
with the customer.
"""

import re
from dataclasses import dataclass
from datetime import date

from propfirm.policy import AccessPolicy

_COUNTRY = re.compile(r"[A-Z]{2}")


def age_on(date_of_birth: date, today: date) -> int:
    """Whole years completed on `today` (a 29 February birthday completes on
    1 March in non-leap years)."""
    years = today.year - date_of_birth.year
    if (today.month, today.day) < (date_of_birth.month, date_of_birth.day):
        years -= 1
    return years


@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    reasons: tuple[str, ...]  # e.g. ("document:BD",); empty when allowed


def check_country(
    policy: AccessPolicy,
    *,
    declared: str | None = None,
    document: str | None = None,
    ip: str | None = None,
    device: str | None = None,
) -> GateDecision:
    signals = {"declared": declared, "document": document, "ip": ip, "device": device}
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
