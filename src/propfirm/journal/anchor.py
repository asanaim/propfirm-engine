"""External anchors: copies of the journal head kept outside the database.

The hash chain proves a journal is internally consistent, not that it is the
journal that was written: whoever controls the database could replace it
with a different, fully consistent one, or roll it back to an earlier head.
An anchor is (journal fingerprint, seq, hash) written somewhere that party
cannot change. The runner publishes one after each checkpoint commits, and
on start requires the journal to contain every anchored record, with the
same hash. A rewrite or a rollback before the latest anchor is then
detected.

`FileAnchorStore` appends to a local file and syncs it to disk. It protects
only if the file sits outside the database operator's control (another
host, write-once storage) [DEFAULT; where production anchors live is an
open decision, e.g. object storage with a retention lock].
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from propfirm._checks import require_int

_HEX64 = re.compile(r"[0-9a-f]{64}")


class AnchorError(Exception):
    """The anchor store is unreadable or holds an invalid entry."""


@dataclass(frozen=True, slots=True)
class Anchor:
    journal: str  # setup fingerprint
    seq: int
    hash: str

    def __post_init__(self) -> None:
        require_int("seq", self.seq, minimum=1)
        for name in ("journal", "hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _HEX64.fullmatch(value):
                raise ValueError(f"{name} must be 64 lowercase hex digits")


class AnchorStore(Protocol):
    async def publish(self, anchor: Anchor) -> None: ...

    async def anchors(self, journal: str) -> list[Anchor]:
        """Every anchor of this journal, in seq order."""
        ...


class FileAnchorStore:
    """One JSON object per line, appended and synced to disk."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def publish(self, anchor: Anchor) -> None:
        line = json.dumps(
            {"journal": anchor.journal, "seq": anchor.seq, "hash": anchor.hash},
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._path.open("a", encoding="ascii") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    async def anchors(self, journal: str) -> list[Anchor]:
        if not self._path.exists():
            return []
        found: dict[int, Anchor] = {}
        for number, line in enumerate(self._path.read_text("ascii").splitlines(), start=1):
            try:
                data = json.loads(line)
                if not isinstance(data, dict) or set(data) != {"journal", "seq", "hash"}:
                    raise ValueError("fields")
                anchor = Anchor(data["journal"], data["seq"], data["hash"])
            except (ValueError, TypeError) as exc:
                raise AnchorError(f"{self._path}:{number}: invalid anchor") from exc
            if anchor.journal != journal:
                continue
            if found.setdefault(anchor.seq, anchor) != anchor:
                raise AnchorError(f"{self._path}:{number}: two hashes for record {anchor.seq}")
        return [found[seq] for seq in sorted(found)]
