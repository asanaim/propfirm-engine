"""Pre-employment screening [decided 7 Nov 2026]: every check in
`config/staff/screening.toml` at hire and again every
`staff.screening_valid_months` (12). A lead (T3) or above records each check
(pass or fail, with a reference: never the report); an admin (T4) or owner,
not a recorder of that round and not the person, approves the whole screening
once every check has passed. Until it is approved, and after it expires, the
person has no staff access (owners exempt). Restricted data: seen by T3 and
above only.

A round is the checks recorded since the last decision: approving or
rejecting closes it; the next round starts with the next check recorded.
"""

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

CHECKS_FILE = Path(__file__).resolve().parents[3] / "config" / "staff" / "screening.toml"
RESULTS = ("pass", "fail")
DECISIONS = ("approved", "rejected")
Status = Literal["approved", "pending", "incomplete", "expired", "rejected"]


class ScreeningError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class CheckRecord:
    sid: str
    check: str
    result: str
    reference: str
    recorded_by: str
    ts: int


@dataclass(frozen=True, slots=True)
class Decision:
    sid: str
    decision: str
    note: str
    decided_by: str
    ts: int
    valid_until: int | None  # approved: the expiry


def load_checks(path: Path = CHECKS_FILE) -> tuple[Check, ...]:
    with path.open("rb") as f:
        raw = tomllib.load(f).get("check", [])
    out = []
    for t in raw:
        if not isinstance(t, dict) or set(t) != {"id", "title", "text"}:
            raise ScreeningError("each [[check]] has id, title and text")
        if not all(isinstance(t[k], str) and t[k].strip() for k in ("id", "title", "text")):
            raise ScreeningError("id, title and text are text")
        out.append(Check(t["id"], t["title"], t["text"]))
    ids = [c.id for c in out]
    if not out or len(set(ids)) != len(ids):
        raise ScreeningError("at least one check, ids unique")
    return tuple(out)


@cache
def checks() -> tuple[Check, ...]:
    return load_checks()


@dataclass(frozen=True, slots=True)
class Screening:
    status: Status
    valid_until: int | None  # the current approval's expiry
    round: dict[str, CheckRecord]  # check -> its latest record in the open round
    missing: tuple[str, ...]  # required checks without a passing record this round
    recorders: frozenset[str]  # who recorded this round (never the approver)
    last_decision: Decision | None


def status(
    required: Sequence[str],
    records: Sequence[CheckRecord],
    decisions: Sequence[Decision],
    now: int,
) -> Screening:
    """Records and decisions oldest first."""
    last = decisions[-1] if decisions else None
    since = last.ts if last is not None else -1
    round_: dict[str, CheckRecord] = {}
    for r in records:
        if r.ts > since or (last is None):
            round_[r.check] = r
    missing = tuple(c for c in required if c not in round_ or round_[c].result != "pass")
    recorders = frozenset(r.recorded_by for r in round_.values())
    approved = next((d for d in reversed(decisions) if d.decision == "approved"), None)
    valid = approved is not None and approved.valid_until is not None and now < approved.valid_until
    st: Status
    if valid and (last is None or last.decision == "approved"):
        st = "approved"
    elif last is not None and last.decision == "rejected" and not round_:
        st = "rejected"
    elif not missing:
        st = "pending"  # every check passed: waiting for an approver
    elif approved is not None and not valid:
        st = "expired"
    else:
        st = "incomplete"
    return Screening(
        st, approved.valid_until if valid and approved else None, round_, missing, recorders, last
    )
