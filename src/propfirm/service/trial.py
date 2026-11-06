"""The Twelve Data trial report (confirmation C10).

    python -m propfirm.service.trial 2026-11-09 2026-11-20

Reads the connector's daily counters (`propfirm.service.twelvedata`) for the
days given (inclusive, UTC) and prints, per launch symbol: prices per day,
refusals, the most decimal places streamed against the symbol's `digits`,
the share of prices received within 1, 2 and 5 seconds of their source time
(Twelve Data's times are whole seconds, so up to one second of each delay
is rounding), the worst delay, and the longest silence between two prices.
Then the connection events (connects, reconnects, failed subscriptions).

The verdict on `digits` per symbol: "ok" when nothing streamed had more
places; otherwise "raise to N" (prices with more places were refused).
"""

import argparse
import asyncio
import os
from dataclasses import dataclass
from datetime import date, timedelta

import valkey.asyncio as avalkey

from propfirm.market.symbols import SYMBOLS
from propfirm.service.config import ServiceConfig
from propfirm.service.twelvedata import DELAY_BUCKETS_MS, stats_key

Counters = dict[str, int]


@dataclass(frozen=True, slots=True)
class Row:
    symbol: str
    days: int
    prices: int
    refused: int
    places: int | None
    digits: int
    within_ms: dict[int, int]  # bucket upper bound -> prices at or under it
    delay_max_ms: int | None
    silence_max_ms: int | None

    @property
    def digits_verdict(self) -> str:
        if self.places is None:
            return "no data"
        return "ok" if self.places <= self.digits else f"raise to {self.places}"

    def share(self, bound: int) -> str:
        if self.prices == 0:
            return "-"
        # integer per mille, rounded down, shown as a percentage (display only)
        per_mille = self.within_ms[bound] * 1000 // self.prices
        return f"{per_mille // 10}.{per_mille % 10}%"


def rows(days: list[Counters]) -> list[Row]:
    out = []
    for name, spec in SYMBOLS.items():
        total = {k: 0 for k in ("prices", "refused")}
        within = dict.fromkeys(DELAY_BUCKETS_MS, 0)
        places: int | None = None
        delay_max: int | None = None
        silence: int | None = None
        seen = 0
        for c in days:
            if f"prices:{name}" in c:
                seen += 1
            total["prices"] += c.get(f"prices:{name}", 0)
            total["refused"] += c.get(f"refused:{name}", 0)
            running = 0
            for b in DELAY_BUCKETS_MS:
                running += c.get(f"delay_le{b}:{name}", 0)
                within[b] += running
            for key, current in (
                (f"places:{name}", places),
                (f"delay_max:{name}", delay_max),
                (f"silence_max_ms:{name}", silence),
            ):
                if key in c:
                    value = c[key] if current is None else max(current, c[key])
                    if key.startswith("places"):
                        places = value
                    elif key.startswith("delay_max"):
                        delay_max = value
                    else:
                        silence = value
        out.append(
            Row(
                name,
                seen,
                total["prices"],
                total["refused"],
                places,
                spec.digits,
                within,
                delay_max,
                silence,
            )
        )
    return out


def events(days: list[Counters]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in days:
        for key, n in c.items():
            if ":" in key and key.split(":", 1)[1] in SYMBOLS:
                continue
            out[key] = out.get(key, 0) + n
    return dict(sorted(out.items()))


def render(day_names: list[str], days: list[Counters]) -> str:
    head = (
        "| Symbol | Days | Prices/day | Refused | Places | Digits | Verdict "
        "| ≤1 s | ≤2 s | ≤5 s | Worst delay | Longest silence |"
    )
    lines = [
        f"# Twelve Data trial (C10): {day_names[0]} to {day_names[-1]} UTC",
        "",
        head,
        "|" + "---|" * 12,
    ]
    for r in rows(days):
        per_day = "-" if r.days == 0 else str(r.prices // r.days)
        lines.append(
            f"| {r.symbol} | {r.days} | {per_day} | {r.refused} "
            f"| {'-' if r.places is None else r.places} | {r.digits} | {r.digits_verdict} "
            f"| {r.share(1_000)} | {r.share(2_000)} | {r.share(5_000)} "
            f"| {_secs(r.delay_max_ms)} | {_secs(r.silence_max_ms)} |"
        )
    lines += ["", "Connection events:", ""]
    lines += [f"- {k}: {n}" for k, n in events(days).items()] or ["- none"]
    return "\n".join(lines) + "\n"


def _secs(ms: int | None) -> str:
    return "-" if ms is None else f"{ms // 1000} s"


async def load(config: ServiceConfig, first: date, last: date) -> tuple[list[str], list[Counters]]:
    client = avalkey.Valkey.from_url(config.valkey_url)
    names, days = [], []
    try:
        d = first
        while d <= last:
            raw: dict[bytes, bytes] = await client.hgetall(stats_key(config.stream, d.isoformat()))
            names.append(d.isoformat())
            days.append({k.decode(): int(v) for k, v in raw.items()})
            d += timedelta(days=1)
    finally:
        await client.aclose()
    return names, days


def main() -> None:
    parser = argparse.ArgumentParser(description="Twelve Data trial report (C10)")
    parser.add_argument("first", type=date.fromisoformat)
    parser.add_argument("last", type=date.fromisoformat)
    args = parser.parse_args()
    if args.last < args.first:
        raise SystemExit("the last day is before the first")
    names, days = asyncio.run(load(ServiceConfig.from_env(os.environ), args.first, args.last))
    print(render(names, days), end="")


if __name__ == "__main__":
    main()
