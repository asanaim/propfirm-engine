"""The images copy every file the project's own build needs.

`uv sync` builds the project, and the build reads the files pyproject.toml
names (`readme`, `license-files`). The engine image failed to build on
13 Nov 2026 because LICENSE, named since pull request 90, was not copied.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _named_by_project() -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    named = {"pyproject.toml", "uv.lock"}
    readme = project.get("readme")
    if isinstance(readme, str):
        named.add(readme)
    named.update(project.get("license-files", []))
    return named


def _copied_before_the_first_build() -> set[str]:
    """Files the `base` stage copies (both images start from it)."""
    text = (ROOT / "Dockerfile").read_text("utf-8")
    base = text.split(" AS base", 1)[1].split("\nFROM ", 1)[0]
    copied: set[str] = set()
    for line in base.splitlines():
        match = re.match(r"COPY (?!--from)(.+) \./?$", line.strip())
        if match:
            copied.update(match.group(1).split())
    return copied


def test_the_base_stage_copies_every_file_the_build_reads() -> None:
    missing = _named_by_project() - _copied_before_the_first_build()
    assert not missing, f"Dockerfile base stage must COPY {sorted(missing)}"


def test_no_named_file_is_excluded_from_the_build_context() -> None:
    ignored = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert not (_named_by_project() & ignored)
    for name in _named_by_project():
        assert (ROOT / name).is_file(), name
