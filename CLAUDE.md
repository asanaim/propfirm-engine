# Engine conventions

These rules bind every change to this repository.

## Numbers

- Money: `int` US cents. Never `float`, never `Decimal` stored as money.
- Prices: `int` points with an explicit `digits` per symbol
  (price = points / 10**digits). Never `float`.
- Volume: `int` hundredths of a lot (0.01 lot = 1).
- Time: `int` milliseconds since the Unix epoch, UTC. Local times only at
  the edge (display, the 17:00 America/New_York reset calculation).
- Any rounding is explicit, named and tested. No implicit rounding.

## Rules and records

- The double-entry ledger is the record; balances are a cache rebuilt
  from it. Only the engine writes the ledger.
- Every rule outcome records the rule-set version (RSV) it used.
- Records are append-only. Corrections are new linked records.
- Same inputs must give identical outputs (deterministic replay). No
  wall-clock reads or unseeded randomness inside decision code.

## Data licensing

- Synthetic prices are for development and tests only.
- Twelve Data prices may be stored only while subscribed and must be
  purgeable within 30 days of cancelling. Keep licensed prices in their own
  store, keyed by update id.

## Work

- Every behaviour change ships with tests. `ruff check`, `ruff format
  --check`, `mypy` (strict) and `pytest` must pass.
- Branch from `main`; merge through a pull request.
- Open policy decisions (D-numbers in `docs/functional-outline.md`) are
  configuration, not hard-coded choices. Dated owner decisions are in
  `docs/decisions.md` and win where the two differ.
