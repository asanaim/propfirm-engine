"""Detects lost and repeated updates from per-symbol sequence numbers."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from propfirm._checks import require_int, require_text


class SeqStatus(StrEnum):
    IN_ORDER = "in_order"
    GAP = "gap"  # updates were lost; this update is still applied
    DUPLICATE = "duplicate"  # already seen (or older); must not be applied


@dataclass(frozen=True, slots=True)
class SeqResult:
    status: SeqStatus
    symbol: str
    seq: int
    missing_from: int | None = None  # first lost seq, for GAP
    missing_to: int | None = None  # last lost seq, for GAP

    @property
    def apply(self) -> bool:
        """Whether the update should be applied."""
        return self.status is not SeqStatus.DUPLICATE


class SequenceTracker:
    """Tracks the last sequence number seen per symbol.

    After a restart, construct it from the checkpointed `snapshot()` so the
    first update after the restart is not reported as a gap.
    """

    def __init__(self, last_seen: Mapping[str, int] | None = None) -> None:
        self._last: dict[str, int] = {}
        for symbol, seq in (last_seen or {}).items():
            require_text("symbol", symbol)
            self._last[symbol] = require_int(f"last_seen[{symbol}]", seq, minimum=0)

    def observe(self, symbol: str, seq: int) -> SeqResult:
        require_text("symbol", symbol)
        require_int("seq", seq, minimum=1)
        last = self._last.get(symbol, 0)
        if seq == last + 1:
            self._last[symbol] = seq
            return SeqResult(SeqStatus.IN_ORDER, symbol, seq)
        if seq > last + 1:
            self._last[symbol] = seq
            return SeqResult(SeqStatus.GAP, symbol, seq, last + 1, seq - 1)
        return SeqResult(SeqStatus.DUPLICATE, symbol, seq)

    def last_seen(self, symbol: str) -> int:
        return self._last.get(symbol, 0)

    def snapshot(self) -> dict[str, int]:
        return dict(self._last)
