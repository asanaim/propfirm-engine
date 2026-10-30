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
   docker compose run --rm --build test
   ```

   This starts PostgreSQL, Valkey and Keycloak, rebuilds the test image if
   `pyproject.toml` or `uv.lock` changed (a `git pull` can change them) and
   runs every test. The first run downloads images and takes a few minutes.
   Without `--build`, an image built before a new dependency fails with
   `ModuleNotFoundError` before any test runs.
4. Stop the services afterwards:

   ```
   docker compose down
   ```

If only the Keycloak test fails to log in a test user, the Keycloak volume
holds an older realm: remove it as `config/keycloak/README.md` describes.

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

## Trader portal (P2 step 4)

`portal/`: React 19.3.1, Vite 8.3.4 and TypeScript 6.0 (strict). Node 22.

```
cd portal
npm ci
npm run dev          # http://localhost:5173, with the platform running
npm test             # unit tests (Vitest, jsdom)
npm run typecheck && npm run lint && npm run build
```

- Login: Keycloak, Authorization Code with PKCE (OAuth 2.0 for
  Browser-Based Apps); tokens stay in memory, a reload signs in again
  silently through the Keycloak session. Two-factor authentication
  (authenticator app) is optional at login, as at FundedNext, and required
  with a fresh code for wallet changes and payouts: when the API answers
  RFC 9470 `insufficient_user_authentication`, the portal sends the trader
  to sign in again, or to set up an authenticator.
- Pages: overview (registration on the first visit), challenges (the
  catalogue and checkout; sandbox payment under `PROPFIRM_SANDBOX=1`),
  accounts and orders, security, open-source notices.
- Account dashboard (`/accounts/{acc}`), live: balance, equity, the daily
  and maximum loss left with their floors, the profit target, trading days,
  the countdown to the 17:00 New York reset with the daily floor from then
  and a warning when equity at the reset would breach (audit, M12), open
  positions and pending orders, prices, and a candle chart. Every figure
  comes from the engine's live state (`GET /accounts/{acc}`), so the portal
  computes no rule of its own (I14).
- Live updates: `GET /accounts/{acc}/stream`, server-sent events (`account`
  and `quotes` when they change; `end` after 5 minutes, and the portal
  opens it again with a fresh token).
- Chart: TradingView Lightweight Charts 5.2.1 drawn from the firm's own
  prices: `GET /bars?symbol=EUR/USD&interval=1m` (1m, 5m, 15m, 1h, 4h, and
  1d by the 17:00 New York trading day), built by the projector from the
  price store into one-minute bars. The price source is named beside every
  chart and price table (`GET /market`; `PROPFIRM_PRICE_SOURCE` =
  `synthetic` or `twelve_data`: "Prices by Twelve Data"); the chart carries
  TradingView's attribution link, and the notices page its notice.
- The portal calls the API under `/api` on its own origin: Vite forwards it
  in development (`PROPFIRM_API_URL`, default http://127.0.0.1:8000), the
  web server in production; no CORS.
- Design (D44): calm, one accent colour, light and dark themes, Inter (SIL
  OFL) and Lucide icons (ISC); no platform-vendor fonts, icons or names.
  WCAG 2.2 AA: the colour tokens are checked by a contrast test; every page
  was checked with axe-core in both themes.

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

## Customer and order records

Every step of an order is an event in `order_events`, oldest first:
created, invoiced, paid, payment_repeated, account_assigned,
provision_attempted (one per try), provisioned, failed. The status shown
for an order is a cache of that history, written in the same transaction
and rebuilt when the API starts; a customer's status is likewise rebuilt
from `cif_events`. The database refuses edits and deletions of the order
history, of account ownership (`accounts`) and of an order's facts
(product, size, prices, discount, rule set). Customer personal data (`cif`,
`cif_events`, `screenings`) is never edited by the code but is not locked,
so it stays erasable on a lawful request. See `src/propfirm/customers/`.

## Stage progression

When the engine reports a pass, the API's background loop (every second)
records a progression (`PRG00000001`) with the next stage, taken from the
product as the policy in force defines it at that moment:

- an evaluation stage (Lite and Classic: S1 → S2): the next account opens
  at once on that stage's rule set, with the same size, under the same
  order, retried until the engine has it;
- the funded stage (Lite and Classic after S2, 1-Step after S1): an
  employee approves or denies it (`GET /staff/progressions`,
  `POST /staff/progressions/{prg}/approve` or `/deny` with a reason); nobody
  decides their own. Funded onboarding follows an approval (below);
- no next stage in the policy: the progression is blocked for an employee.

Each step is an event in `progression_events` (append-only); the trader
sees their progressions in `GET /me`.

## Funded onboarding

A funded account is earned by an approved funded review, or by paying for
an Instant account. Either way an onboarding (`ONB00000001`) starts, and the
account opens only at its end (an Instant order stays `paid` until then):

1. **Tier 2 identity check** (`POST /me/onboardings/{onb}/verification`
   answers the provider's link). Didit when `DIDIT_API_KEY`,
   `DIDIT_WORKFLOW_ID` and `DIDIT_WEBHOOK_SECRET` are set (its workflow must
   include AML screening); the sandbox check under `PROPFIRM_SANDBOX=1`
   (`POST /dev/kyc/{session}/complete`). Didit's webhook
   (`POST /webhooks/didit`) is checked by its signature and only prompts a
   fresh read of the outcome from Didit; cases without a webhook are polled.
   A declined check may be retried, three sessions at most; a customer
   verified before reuses that check.
2. **Checks** at that moment: AML screening (a hit blocks the customer), the
   minimum age on the verified date of birth, the country gate on the
   document country when switched on. A verified name or date of birth
   that differs from the customer record goes to an employee
   (`GET /staff/onboardings`, `POST /staff/onboardings/{onb}/approve` or
   `/deny`); nobody decides their own.
3. **Contract**: the account's ACC id is allocated and the funded-trader
   agreement generated from the policy in force
   (`config/contracts/funded-agreement-v1.txt`, `[contract]` in the policy);
   its SHA-256 is kept. Until the firm name (C14) and governing law
   (counsel) are set, contracts are issued only in the sandbox, as DRAFT.
4. **Signature** (`GET /me/onboardings/{onb}/contract`, then
   `POST /me/onboardings/{onb}/sign` with the name as on the identity
   document and the text's SHA-256): only the verified person's login; the
   login, time, network address and device are recorded.
5. **The funded account** opens on the product's funded rule set.

Each step is an event in `onboarding_events` (append-only, like
`contracts`); identity-check data (`kyc_cases`, `kyc_results`) is never
edited by the code but stays erasable.

## Payable profit and payout eligibility

`GET /accounts/{acc}/payout` (the owner, or an employee) shows a funded
account's payable profit and every eligibility check, at that moment:

- payable profit P = X − B − V, never below 0: X the closed balance (or the
  equity, `payouts.basis`), B the baseline (the initial capital; a payout
  never moves it), V profit removed for violations (none yet);
- the trader's share s·P rounded down to the cent (s the product's split),
  the firm's share the rest; a fee refund (1-Step: with the 3rd payout) on
  its own line; `payouts.maximum_cents` caps the trader's amount;
- checks: funded and active; 14 days since funding for the first payout;
  trading days in the period; the account's own consistency rule over the
  period; the $50 minimum; no open position or pending order
  (`payouts.flat_required`); an approved Tier 2 check and a customer not
  blocked; the country gate (when on); no case under review (shown to the
  trader only as "held for review"). The payout wallet (at request, step
  3d) is shown as not decided here;
- the next pay day (the 15th or the last day of the month, UTC dates).

The payout settings (D22, D23) are in `[payouts]` of the policy: closed
balance, flat at request, and, as FTMO and FundedNext, no minimum trading
days and no maximum.

## Integrity, fraud and compliance cases

One case system for surveillance, fraud and AML (M16, M17, M20). An alert
joins the customer's case that is not closed, or opens one (`CAS00000001`);
a case's severity is its highest alert's, and its due time comes from
`[cases.sla_hours]` (high 24 h, medium 72 h, low 168 h).

- Alerts: employees (`POST /staff/cases`), and the platform itself: a
  screening hit (at the first purchase or at funding; AML, high) and the
  same verified identity on two customers (fraud, medium).
- Work: `GET /staff/cases` (by status or customer, soonest due first,
  `overdue=true`), `GET /staff/cases/{cas}` (alerts and history), `assign`,
  `notes`, `decide`, `confirm`, `reject`, `reopen` (appeal).
- Decision: cleared or warning by one employee; actions need a second
  employee to confirm (four eyes): `remove_profit` (V, never paid),
  `void_account` (the engine closes its positions and locks it: input
  `VoidAccount`, event `AccountVoided`), `block_customer`. Nobody handles
  a case about themselves.
- While a customer has a case not closed: no funded review is approved, no
  funded contract is issued, no payout is eligible.
- Customers never see cases (no tipping off). `case_events` and `alerts`
  are append-only.

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
  - [x] 3a: stage progression (next evaluation account, funded review)
  - [x] 3b: funded onboarding (Tier 2 verification, contract, signature)
  - [x] 3c: payable profit and payout eligibility
  - [ ] 3d: payout processing (two-person approval)
- [ ] Step 4: trader portal
  - [x] 4a: foundation, login (PKCE, optional 2FA, step-up), registration, checkout, accounts
  - [x] 4b: account dashboard, live prices, chart
  - [ ] 4c: order ticket, positions, history, rulebook, news calendar
  - [ ] 4d: funded onboarding, wallet, payouts
- [ ] Step 5: back office
- [ ] Step 6: Twelve Data connector and 12-day trial (C10)
- [ ] Step 7: staging, backups and restore drill, security scans
