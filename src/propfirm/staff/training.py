"""The training gate [staff spec 5c]: one course per section
(`config/staff/courses.toml`); a level of T1 or above in a section, and an
apprentice's graduation, need that section's course passed within
`training_valid_months` (policy, default 12)."""

import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from propfirm.staff.model import ROLES, add_months

COURSES_FILE = Path(__file__).resolve().parents[3] / "config" / "staff" / "courses.toml"


class TrainingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Course:
    section: str
    course: str
    title: str
    version: int


@dataclass(frozen=True, slots=True)
class Passed:
    sid: str
    section: str
    course: str
    version: int
    passed_at: int
    assessor: str
    note: str


def load_courses(path: Path = COURSES_FILE) -> dict[str, Course]:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    if set(raw) != set(ROLES):
        raise TrainingError(f"one course per section {sorted(ROLES)}; got {sorted(raw)}")
    out = {}
    for section, t in raw.items():
        if not isinstance(t, dict) or set(t) != {"course", "title", "version"}:
            raise TrainingError(f"[{section}]: course, title and version")
        if not all(isinstance(t[k], str) and t[k].strip() for k in ("course", "title")):
            raise TrainingError(f"[{section}]: course and title are text")
        if not isinstance(t["version"], int) or isinstance(t["version"], bool) or t["version"] < 1:
            raise TrainingError(f"[{section}] version: a whole number from 1")
        out[section] = Course(section, t["course"], t["title"], t["version"])
    return out


@cache
def courses() -> dict[str, Course]:
    return load_courses()


def valid_until(p: Passed, valid_months: int) -> int:
    return add_months(p.passed_at, valid_months)


def current(passes: list[Passed], section: str, now: int, valid_months: int) -> Passed | None:
    """The latest pass of the section's course still valid at `now`."""
    found = [p for p in passes if p.section == section and now < valid_until(p, valid_months)]
    return max(found, key=lambda p: p.passed_at, default=None)
