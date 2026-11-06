"""The CI security gates (scripts/security_gate.py)."""

import importlib.util
import tomllib
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "security_gate", ROOT / "scripts" / "security_gate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report(*vias: tuple[str, str, str]) -> dict[str, Any]:
    vulns: dict[str, Any] = {}
    for package, severity, ghsa in vias:
        vulns[package] = {"via": [{"name": package, "severity": severity,
                                   "url": f"https://github.com/advisories/{ghsa}"}]}  # fmt: skip
        vulns[f"{package}-parent"] = {"via": [package]}  # a dependent, not an advisory
    return {"vulnerabilities": vulns}


ACCEPTED = [{"id": "GHSA-aaaa", "app": "backoffice", "review_by": "2026-12-06"}]
TODAY = date(2026, 11, 6)


def test_unaccepted_advisories_of_moderate_and_above_fail() -> None:
    g = gate()
    assert g.npm_gate(report(("x", "low", "GHSA-low")), "backoffice", ACCEPTED, TODAY) == []
    assert g.npm_gate(report(("x", "high", "GHSA-aaaa")), "backoffice", ACCEPTED, TODAY) == []
    problems = g.npm_gate(report(("y", "moderate", "GHSA-bbbb")), "backoffice", ACCEPTED, TODAY)
    assert problems == ["backoffice: GHSA-bbbb (y, moderate) is not accepted"]
    # accepted for another app only
    assert g.npm_gate(report(("x", "high", "GHSA-aaaa")), "portal", ACCEPTED, TODAY) != []


def test_an_acceptance_past_its_review_date_fails() -> None:
    problems = gate().npm_gate(
        report(("x", "critical", "GHSA-aaaa")), "backoffice", ACCEPTED, date(2026, 12, 7)
    )
    assert problems == ["backoffice: the acceptance of GHSA-aaaa expired on 2026-12-06"]


def test_the_accepted_file_is_complete() -> None:
    with (ROOT / "security" / "accepted-advisories.toml").open("rb") as f:
        entries = tomllib.load(f)["advisory"]
    for e in entries:
        assert set(e) == {"id", "package", "app", "review_by", "reason"}
        assert date.fromisoformat(e["review_by"])
        assert len(e["reason"]) > 40


def test_new_or_real_secrets_fail_reviewed_ones_pass() -> None:
    g = gate()
    reviewed = {
        "results": {"a.py": [{"line_number": 3, "type": "Secret Keyword", "is_secret": False}]}
    }
    assert g.secrets_gate(reviewed) == []
    new = {"results": {"b.py": [{"line_number": 9, "type": "Base64 High Entropy String"}]}}
    assert g.secrets_gate(new) == [
        "possible secret not reviewed: b.py:9 (Base64 High Entropy String)"
    ]
    real = {"results": {"c.py": [{"line_number": 1, "type": "AWS Access Key", "is_secret": True}]}}
    assert g.secrets_gate(real) == ["a real secret is committed: c.py:1 (AWS Access Key)"]


def test_the_baseline_has_only_reviewed_findings() -> None:
    import json

    baseline = json.loads((ROOT / ".secrets.baseline").read_text())
    assert gate().secrets_gate(baseline) == []
