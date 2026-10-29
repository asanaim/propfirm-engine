# propfirm-engine

The firm's own trading engine: prices, fills, equity, rules, ledger and
replay. It is phase P1 of the plan: everything runs on synthetic prices, with
no vendors and no money involved.

Specification: *Prop Firm Operating Engine — End-to-End Functional Outline*
(Claude Docs) and *Prop Firm Plan v4 (Audited)*.

## Stack

| Part | Version |
| --- | --- |
| Python | 3.14 |
| PostgreSQL | 18.6 (container) |
| Valkey | 9.1.3 (container) |
| Package manager | uv 0.13.6 |

## Run the tests (Windows 11, Docker Desktop with WSL 2)

1. Start Docker Desktop.
2. Open a terminal in the repository folder.
3. Run:

   ```
   docker compose run --rm test
   ```

   This starts PostgreSQL and Valkey, builds the engine image and runs every
   test. The first run downloads images and takes a few minutes.
4. Stop the services afterwards:

   ```
   docker compose down
   ```

After changing `pyproject.toml` or `uv.lock`, rebuild with
`docker compose build test`.

## Run the tests without Docker

Needs [uv](https://docs.astral.sh/uv/). Unit tests run anywhere; integration
tests run only when `VALKEY_URL` points at a running Valkey and are skipped
otherwise.

```
uv sync
uv run pytest
uv run ruff check .
uv run mypy
```

## Conventions

See `CLAUDE.md`. In short: money is integer cents, prices are integer
points, times are integer UTC milliseconds, and nothing that decides money
or rules uses floating point.

## Phase P1 progress (gate G1: acceptance tests 1–5)

- [x] Step 1: repository skeleton
- [x] Step 2: synthetic price generator and Valkey Streams transport
- [x] Step 3: double-entry ledger (acceptance test 2)
- [ ] Step 4: pricing and fills, delay and gap guards (test 5)
- [ ] Step 5: rules engine (test 3)
- [ ] Step 6: event journal and replay (tests 1 and 4)
