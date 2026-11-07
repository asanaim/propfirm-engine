"""Which API requests go into the audit trail, and how one is described.

Recorded: every refusal (401, 403) on any route; every request to the staff
and admin routes, reads included (staff reading personal data is logged);
every other write. Not recorded: trading writes (orders, positions,
trailing orders), which the event journal already records in full, and
the traders' and the public's reads. Never a request or response body.
"""

import re
from collections.abc import Mapping

READS = frozenset({"GET", "HEAD", "OPTIONS"})
PRIVILEGED = ("/staff", "/admin")
# journaled by the engine with every detail (P1); not repeated here
TRADING = (
    "/accounts/{account_id}/orders",
    "/accounts/{account_id}/positions/",
    "/accounts/{account_id}/trailing-orders",
)
UNMATCHED = "/<unmatched>"


def audited(method: str, route: str, status: int) -> bool:
    if route == "/health":
        return False
    if status in (401, 403):
        return True
    if route == PRIVILEGED[0] or route.startswith(tuple(p + "/" for p in PRIVILEGED)):
        return True
    if method in READS:
        return False
    return not route.startswith(TRADING)


_UNSAFE = re.compile(r"[^ -~]")


def clean(text: str, limit: int) -> str:
    """Printable ASCII, at most `limit` characters (values come from the
    URL, so from the caller)."""
    return _UNSAFE.sub("?", text)[:limit]


def target(params: Mapping[str, str]) -> str:
    return clean("&".join(f"{k}={v}" for k, v in sorted(params.items())), 500)


def client_ip(header: str | None, peer: str | None) -> str:
    """The address a trusted proxy put in `header` (its last entry: the one
    the proxy itself added), else the peer's."""
    if header:
        return clean(header.rsplit(",", 1)[-1].strip(), 64)
    return clean(peer or "", 64)
