"""Security gates for CI (P2 step 7a). Standard library only.

    npm audit --omit=dev --json | python scripts/security_gate.py npm <app>
    python scripts/security_gate.py secrets .secrets.baseline

`npm`: fails on any advisory of moderate severity or above that
security/accepted-advisories.toml does not accept for this app, and on any
acceptance past its review date (so a waiver is never forgotten).

`secrets`: run after `detect-secrets scan --baseline .secrets.baseline`, which
adds new findings to the baseline without a verdict: fails while any finding
has no `is_secret` (reviewed) field, and on any finding marked a real secret.
Line moves of known findings pass.
"""

import json
import sys
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ACCEPTED = ROOT / "security" / "accepted-advisories.toml"
LEVELS = ("info", "low", "moderate", "high", "critical")
FAIL_FROM = LEVELS.index("moderate")


def advisories(report: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """GHSA id -> (package, severity), from `npm audit --json` (v7+)."""
    out: dict[str, tuple[str, str]] = {}
    for name, vuln in report.get("vulnerabilities", {}).items():
        for via in vuln.get("via", []):
            if isinstance(via, dict):
                url = str(via.get("url", ""))
                gid = url.rsplit("/", 1)[-1] if "GHSA-" in url else str(via.get("source"))
                out[gid] = (str(via.get("name", name)), str(via.get("severity", "")))
    return out


def npm_gate(
    report: dict[str, Any], app: str, accepted: list[dict[str, str]], today: date
) -> list[str]:
    problems = []
    waivers = {a["id"]: a for a in accepted if a.get("app") == app}
    for gid, (package, severity) in sorted(advisories(report).items()):
        if severity not in LEVELS or LEVELS.index(severity) < FAIL_FROM:
            continue
        waiver = waivers.get(gid)
        if waiver is None:
            problems.append(f"{app}: {gid} ({package}, {severity}) is not accepted")
        elif date.fromisoformat(waiver["review_by"]) < today:
            problems.append(f"{app}: the acceptance of {gid} expired on {waiver['review_by']}")
    return problems


def secrets_gate(baseline: dict[str, Any]) -> list[str]:
    problems = []
    for path, items in baseline.get("results", {}).items():
        for item in items:
            where = f"{path}:{item.get('line_number')} ({item.get('type')})"
            if "is_secret" not in item:
                problems.append(f"possible secret not reviewed: {where}")
            elif item["is_secret"]:
                problems.append(f"a real secret is committed: {where}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "npm":
        with ACCEPTED.open("rb") as f:
            accepted = tomllib.load(f).get("advisory", [])
        today = datetime.now(UTC).date()
        problems = npm_gate(json.load(sys.stdin), argv[2], accepted, today)
    elif len(argv) == 3 and argv[1] == "secrets":
        problems = secrets_gate(json.loads(Path(argv[2]).read_text()))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for p in problems:
        print(p, file=sys.stderr)
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
