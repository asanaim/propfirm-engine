"""Engine load probe (review D3; acceptance test 6's measure).

Time from a price update to the breach decision, in process: N accounts on
the rule supervisor, two positions each, then price updates; prints the
median and 99th percentile milliseconds per update.

    uv run python scripts/load_probe.py --accounts 5000 --symbols 30
    uv run python scripts/load_probe.py --accounts 1600 --symbols 1   # the review's case

`--symbols 1`: every position on EUR/USD, so every update moves every
account (the worst case). `--symbols 30`: positions spread over the 30
launch symbols, updates in turn across them (the acceptance test's case).
"""

import argparse
import random
import statistics
import time

from propfirm.ledger import Ledger
from propfirm.market.guards import GuardConfig
from propfirm.market.symbols import LAUNCH_SYMBOLS
from propfirm.market.types import PriceUpdate
from propfirm.rules.products import PRODUCTS
from propfirm.rules.supervisor import RuleSupervisor
from propfirm.trading.engine import TradingEngine
from propfirm.trading.instruments import default_instruments
from propfirm.trading.orders import OrderRequest, OrderType, Side

START = 1_794_000_000_000  # a Tuesday
LITE = PRODUCTS["lite"][0]


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("--accounts", type=int, default=1600)
    args.add_argument("--symbols", type=int, default=1)
    args.add_argument("--updates", type=int, default=300)
    opts = args.parse_args()

    specs = default_instruments()
    engine = TradingEngine(
        specs,
        Ledger(),
        config_version="probe",
        guard_config=GuardConfig(default_gap_threshold_ms=10**9),
    )
    sup = RuleSupervisor(engine)
    rng = random.Random(6)
    ts = START
    price: dict[str, int] = {}
    seq: dict[str, int] = {}
    for listed in LAUNCH_SYMBOLS:
        name = listed.name
        price[name] = 10 ** specs[name].digits * 2  # 2.0 in quote currency: positions stay small
        seq[name] = 1
        sup.on_price(PriceUpdate(name, 1, ts, ts, price[name]))
    traded = [s.name for s in LAUNCH_SYMBOLS][: opts.symbols] if opts.symbols > 1 else ["EUR/USD"]
    for i in range(opts.accounts):
        acc = f"A{i}"
        sup.open_account(acc, LITE, 10_000_000, ts)
        for k in range(2):
            symbol = rng.choice(traded)
            side = rng.choice((Side.BUY, Side.SELL))
            sup.place_order(OrderRequest(f"{acc}-{k}", acc, symbol, side, OrderType.MARKET, 1), ts)
    times: list[float] = []
    for n in range(opts.updates):
        ts += 250
        symbol = traded[n % len(traded)]
        price[symbol] += rng.choice((-2, -1, 1, 2))
        seq[symbol] += 1
        update = PriceUpdate(symbol, seq[symbol], ts, ts, price[symbol])
        began = time.perf_counter()
        sup.on_price(update)
        times.append((time.perf_counter() - began) * 1000)
    times.sort()
    p99 = times[min(len(times) - 1, int(len(times) * 0.99))]
    print(
        f"accounts {opts.accounts}, symbols {len(traded)}, updates {opts.updates}: "
        f"median {statistics.median(times):.2f} ms, p99 {p99:.2f} ms, max {times[-1]:.2f} ms"
    )


if __name__ == "__main__":
    main()
