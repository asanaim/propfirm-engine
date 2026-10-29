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

## Run the platform locally (P2, synthetic prices)

```
docker compose --profile platform up -d
```

This starts Keycloak (login, http://localhost:8080), the engine, the
projector, the API on http://127.0.0.1:8000, a synthetic price feeder and
the clock/checkpoint scheduler. Payments and screening are sandbox
stand-ins (no money moves). Test users: `trader1@example.com` /
`dev-trader-password`, `staff1@example.com` / `dev-staff-password`
(employee) and `admin1@example.com` / `dev-admin-password` (admin); see
`config/keycloak/README.md`. `docker compose --profile platform down` stops
them; add `-v` to delete the journal and start over.

## Who sees what

| View | Role | Sees and does |
|---|---|---|
| Public | none | products, sizes, prices, split, discounts; each stage's profit target, daily loss limit, drawdown limit, trading days and consistency rule; minimum age |
| Trader (portal) | `trader` | own CIF, orders and accounts (each account shows its own target, limits and floors); buys; trades own accounts |
| Employee (back office) | `staff` | every account, its history and inputs; trades none; no settings |
| Admin (back office) | `admin` | what an employee sees, plus the admin-only settings below |

Admin-only settings, changed through the API (each change is recorded with
who, when and why: `GET /admin/settings/changes`):

- country gating, viewed and changed by admins only: `GET /admin/access`,
  `PUT /admin/access/country-gating` with
  `{"country_gating": true, "blocked_countries": ["BD", "IN"], "reason": "..."}`;
- a stage's profit target, daily loss limit and drawdown limit, public to
  view (`GET /catalogue`), changed by admins only:
  `PUT /admin/products/{product}/stages/{s1|s2|funded}/limits`
  with `{"profit_target_percent": "8", "daily_loss_percent": "4",
  "max_loss_percent": "5", "max_loss_kind": "static", "reason": "..."}`
  (`null` target for a funded stage; the daily limit may not exceed the
  drawdown limit). The change becomes a new rule-set version
  (`one-step-s1-v1-a1`): accounts already open keep theirs.

The policy file holds the starting values of these settings; once an
admin has changed one, the admin value wins.

## Change products, prices and switches

Every other number and switch the firm can change lives in
`config/policy.toml`: trading days, consistency, sizes, prices, split and
discounts; leverage, lot caps, spreads and feed guards; the minimum age;
news windows, payouts and scaling.

1. Edit the file. For a product stage, raise its `version`; for anything
   under `[trading]`, set a new `config_version`. (Published versions never
   change: open accounts keep theirs, new accounts get the new one.)
2. Restart the engine: `docker compose restart engine api`.

The engine applies the change through its journal and refuses an edit that
reuses a version number. A typo (unknown key, a number written as 6.0
instead of "6") stops the start with a message naming the line.

## Run the tests without Docker

Needs [uv](https://docs.astral.sh/uv/). Unit tests run anywhere; integration
tests run only when `VALKEY_URL` and `DATABASE_URL` point at running Valkey
and PostgreSQL, and are skipped otherwise.

```
uv sync
uv run pytest
uv run ruff check .
uv run mypy
```

## Event journal

Every input (price, order, close, clock tick, account opening) reaches the
engine through one Valkey stream and is journaled in PostgreSQL with the
events and ledger entries it caused, in one transaction, before it is
acknowledged. Records are hash-chained from the engine setup. On start the
engine replays the journal and checks that every record comes out identical.
Prices are kept in a separate, purgeable table and referenced by a keyed
digest (HMAC-SHA256); purging deletes the prices, their keys and the
snapshots together.

- News events and new rule-set versions arrive as inputs, so the calendar
  and the product catalogue change without a new journal.
- A `Checkpoint` input stores a snapshot of the whole engine state, pinned
  by its digest in the chain; start-up resumes from the latest snapshot and
  replays only the records after it.
- After each checkpoint the head hash is anchored outside the database
  (`FileAnchorStore` for now); start-up refuses a journal that was rolled
  back or replaced. An anchor outage does not stop trading.
- Audits: `verify_chain` (hashes only, works after a purge) and a full
  replay (`rebuild(store, full=True)`).

See `src/propfirm/journal/`.

## Conventions

See `CLAUDE.md`. In short: money is integer cents, prices are integer
points, times are integer UTC milliseconds, and nothing that decides money
or rules uses floating point.

## Phase P1 progress (gate G1: acceptance tests 1–5)

- [x] Step 1: repository skeleton
- [x] Step 2: synthetic price generator and Valkey Streams transport
- [x] Step 3: double-entry ledger (acceptance test 2)
- [x] Step 4: pricing and fills, delay and gap guards (test 5)
- [x] Step 5: rules engine (test 3)
- [x] Step 6: event journal and replay (tests 1 and 4)

## Phase P2 progress (gate G2: challenge lifecycle on staging, scans, restore drill)

- [x] Step 1: engine service, live state, history, trading API, policy file
- [x] Step 2: customers and access (Keycloak login, CIF, country-gating switch, checkout,
  trader / employee / admin views, admin-only settings)
- [ ] Step 3: lifecycle Stage 1 → Stage 2 → Funded, payouts
- [ ] Step 4: trader portal
- [ ] Step 5: back office
- [ ] Step 6: Twelve Data connector and 12-day trial (C10)
- [ ] Step 7: staging, backups and restore drill, security scans
