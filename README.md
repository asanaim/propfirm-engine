# propfirm-engine

The engine of **pipridge** (the brand, written all lowercase; a buyer of the
platform sets their own: `VITE_BRAND` for the portal, the realm's display
name in Keycloak, the sender name in `MAIL_FROM`, e.g.
`pipridge <no-reply@example.com>`). Internal names (the `propfirm` package,
schemas, the `propfirm` realm, `PROPFIRM_*` settings, journal formats) stay:
renaming them would change recorded data and hashes.

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
(employee) and `admin1@example.com` / `dev-admin-password` (admin), the
last two with an authenticator; a token for curl:
`python scripts/dev_token.py staff1@example.com dev-staff-password dev-totp-secret-staff1`
(see `config/keycloak/README.md`). `docker compose --profile platform down` stops
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
  accounts and orders, funded onboarding, payouts, news, security,
  open-source notices.
- Account dashboard (`/accounts/{acc}`), live: balance, equity, the daily
  and maximum loss left with their floors, the profit target, trading days,
  the countdown to the 17:00 New York reset with the daily floor from then
  and a warning when equity at the reset would breach (audit, M12), open
  positions and pending orders, prices, and a candle chart. Every figure
  comes from the engine's live state (`GET /accounts/{acc}`), so the portal
  computes no rule of its own (I14).
- Trading (4c): an order ticket (market, limit and stop; volume in lots,
  prices typed as decimals and sent as integer points, never floats; stop
  loss and take profit), open positions with the price each would close at
  and its profit (`pnl_cents`, each rounded on its own for display), close
  and change stops, pending orders and cancel, every engine refusal in
  words; history (`/accounts/{acc}/history`: closed trades, profit and
  commission, CSV export, and the account's whole record), the rule set
  (`/accounts/{acc}/rules`, from the rule set's own fields: target basis,
  flat to pass, tie rule) and the news calendar (`GET /news`: restricted
  windows not over yet).
- Trailing orders (M09, D43): the ticket's "Trailing" type places a trailing
  buy or sell (`POST /accounts/{acc}/trailing-orders`), and "Trail" on a
  position sets its trailing stop (`PUT .../positions/{pos}/trailing-stop`,
  replacing any it has); both are cancelled like any order. Distance and
  step are whole points (at least 1). They run in the engine, so they keep
  working while the trader is offline: buy-side orders follow the ask and
  sell-side orders the bid, a level moves only towards the price and only
  by a whole step, every move is an engine event with the price update that
  caused it, and each working trailing order shows its level and when it
  last moved. Frozen prices move and trigger nothing; inside a news window
  levels keep moving, entries are held and trailing stops execute like a
  stop-loss.
- Funded (4d, `/funded`, `/funded/{onb}`): each funded onboarding with its
  four steps. Identity check: start it (`POST /me/onboardings/{onb}/verification`)
  and continue at the provider (Didit, new tab), or complete it in the sandbox;
  a declined check can be tried again (3 attempts). Checks and review: the
  status, re-checked on demand. Agreement: the full text, a DRAFT warning
  while the firm's legal details are not set, its SHA-256; the portal hashes
  the text it received and allows signing only when it matches, then signs
  with the name typed as on the identity document and the hash
  (`POST /me/onboardings/{onb}/sign`). Then the funded account's link.
- Payouts (4d, `/payouts`): the payout wallet (USDT, TRC-20; shape checked in
  the portal, Base58Check by the API; a change asks for the authenticator
  code, step-up), and for each funded account what is payable now (profit,
  split, fee refund, the amount received), every payout check met or not,
  the request (`POST /accounts/{acc}/payouts`, step-up), the account's
  payouts with their status, and cancel while being approved.
- Live updates: `GET /accounts/{acc}/stream`, server-sent events (`account`
  and `quotes` when they change; `end` after 5 minutes, and the portal
  opens it again with a fresh token).
- Chart: TradingView Lightweight Charts 5.2.1 drawn from the firm's own
  prices: `GET /bars?symbol=EUR/USD&interval=1m` (1m, 5m, 15m, 1h, 4h, and
  1d by the 17:00 New York trading day), built by the projector from the
  price store into one-minute bars. The price source is named beside every
  chart and price table (`GET /market`; `PROPFIRM_PRICE_SOURCE` =
  `synthetic` or `twelve_data`: "Data provided by Twelve Data", with "Twelve
  Data" a followed link to twelvedata.com, as Twelve Data's attribution
  guidelines ask); the chart carries TradingView's attribution logo and
  link, and the notices page the NOTICE text of Lightweight Charts 5.2.1.
- Third-party notices: `npm run build` (and `npm run dev`) writes
  `public/third-party-notices.txt` with the licence and notice texts of every
  package shipped to the browser, and fails if one is not permissively
  licensed or has no text (`portal/notices/` keeps texts an npm package
  lacks, copied from the upstream release).
- The portal calls the API under `/api` on its own origin: Vite forwards it
  in development (`PROPFIRM_API_URL`, default http://127.0.0.1:8000), the
  web server in production; no CORS.
- Design (D44): Apple-inspired, nothing of Apple's or anyone else's: calm,
  one accent colour (the firm's own blue, not a platform system colour),
  light and dark themes, self-hosted Inter (SIL OFL) with no platform-font
  fallback, Lucide icons (ISC); a clearance test fails the build on Apple
  or other vendor names, fonts, icon sets or system colours in the portal.
  WCAG 2.2 AA: the colour tokens are checked by a contrast test; every page
  was checked with axe-core in both themes.

## Web apps in containers

`docker compose --profile platform up -d --build --wait` also builds and
serves both web apps (`web/Dockerfile`: Node builds, Caddy serves; the API
under `/api` on the same origin; strict Content-Security-Policy; read-only,
non-root): trader portal http://localhost:5173, back office
http://localhost:5174. They keep running with the platform; no Node or open
terminal windows needed. For hot reload while changing the apps, stop the
container (`docker compose stop portal` or `backoffice`) and run
`npm run dev` in the app's folder (same port).

## Demo data (development only)

```sh
docker compose --profile platform up -d --build --wait
docker compose --profile platform run --rm demo-seed
```

Refused unless `PROPFIRM_SANDBOX=1`. Safe to run again (missing data is
added; the demo logins are reset). Every demo login has the password
**12345**: the realm's password policy (12 characters) is bypassed for these
logins only, by importing the password already hashed; real users keep it.

| Who | Login |
|---|---|
| Owners (T5) | `owner-a@example.com`, `owner-b@example.com` |
| Admins (T4) | `admin-a@example.com`, `admin-b@example.com` |
| One per level in each section (support, kyc, risk, payouts, integrity, product, marketing, it) | `<section>-apprentice@`, `-associate@`, `-specialist@`, `-lead@example.com` |
| Traders (one paid evaluation account each) | `trader-a@` to `trader-e@example.com` |

Employees need an authenticator code: add the key printed by the seed
(`OBUXA4TJMRTWKLLEMVWW6LLUN52HA`, one entry serves every demo employee) to
an authenticator app. Traders sign in with the password only.

Also added: the directory built through its own rules (owners, admins
approved by the other owner; each section's people joined by admin A,
trained, approved by admin B; apprentices mentored by their section's
lead), an integrity case on trader A taken by the integrity lead, an
apprentice's note waiting for the mentor, and a level in Payouts for the
KYC associate waiting for an owner. Not included: payouts (they need a
funded account with profit).

## Back office (P2 step 5e)

`backoffice/`: react-admin 5.15.5 on MUI 9.5, React 19.3.1, Vite 8.3.4,
TypeScript 6.0 (strict). Node 22.

```sh
cd backoffice
npm ci
npm run dev          # http://localhost:5174, with the platform running
npm run typecheck && npm run lint && npm test && npm run build
```

- Sign-in: Keycloak client `backoffice`, Authorization Code with PKCE,
  tokens in memory only; the API refuses a staff login without a second
  factor. Development logins: `staff1@example.com`, `admin1@example.com`
  (TOTP secrets in the realm file).
- My work (home): access by section, the to-do list (each item opens its
  record), notifications (acknowledge), duty sheets (acknowledge the version
  in force), training (a missing pass blocks approvals in that section).
- Screens: payouts (approve: the API picks pass or authorise; deny), cases
  (take, note, clear, warn, propose a block; confirm or authorise, send
  back; reopen), identity reviews and funded reviews (approve, deny), staff
  directory (grants, history; access review keep/change; leaver; reinstate;
  recommend an apprentice), access requests (approve, deny, cancel,
  revoke), leaver cut-offs (confirm a manual disable), apprentice proposals
  (the mentor approves or rejects), setting proposals (approve, reject,
  withdraw) and changes, the anomaly report, the news desk (read-only).
- Every change is an action with a reason (the record keeps it); the data
  provider refuses generic create, update and delete. An approval that needs
  a fresh second factor (RFC 9470) offers "Confirm it is you" and signs in
  again with `max_age=0`.
- Screens are shown by permission for clarity only; the API checks every
  request. Apprentices get the same screens with personal fields masked by
  the API.
- Times are shown in UTC; money from integer cents, never rounded.
- Not in the screens yet (API only): proposing a void or a profit removal
  (needs the account and amount), joining a member, requesting a grant,
  moving a member, graduation, adding or cancelling news events.
- Known advisory: `decode-uri-component` 0.2.2 (via react-admin's
  `query-string` 7.1.3; GHSA-vcc3-ghjq-m6fr, GHSA-qr28-fpjm-wq7f), a
  denial of service on malformed percent-encoding in the page's own URL. No
  compatible fix: the fixed releases are ESM-only and `query-string` 10 drops
  the named exports react-admin imports. Impact: a crafted link can hang
  the staff member's own tab; no data is exposed. Recheck on each react-admin
  release.
- Third-party notices as in the portal; `css-mediaquery` 0.1.2 declares
  "BSD", read by hand as BSD-3-Clause (pinned to that version), and
  `(MPL-2.0 OR Apache-2.0)` packages are taken under Apache-2.0.

## Pre-employment screening [decided 7 Nov 2026]

Every employee (owners exempt) is screened at hire and every year
(`config/staff/screening.toml`): identity, right to work, address,
sanctions and PEP, background check (where lawful), references, conflicts
of interest, personal and household trading accounts (the staff trading
ban), signed agreements. Restricted data: the result and a reference only,
never the report or a copy of a document.

- A lead (T3) or above records each check (pass or fail, with a reference);
  never the person. An admin (T4) or owner approves or rejects the round;
  never the person, never one who recorded a check in it; approval needs
  every check passed and lasts `staff.screening_valid_months` (12).
- **No staff access until approved, and none after it expires**
  (`staff.screening_required`): the API answers 403 `screening_required`.
- To-do: T3+ see members to screen (incomplete, expired, or due within
  `staff.screening_due_days`, 30); T4+ see rounds waiting for approval.
- API: `GET /staff/members/{sid}/screening` (T3+ or the person),
  `POST .../screening/checks` `{check, result, reference}`,
  `POST .../screening/decide` `{decision, note}` (fresh second factor).
  Back office: the member's page.

## Who sees what

| View | Role | Sees and does |
|---|---|---|
| Public | none | products, sizes, prices, split, discounts; each stage's profit target, daily loss limit, drawdown limit, trading days and consistency rule; minimum age |
| Trader (portal) | `trader` | own CIF, orders and accounts (each account shows its own target, limits and floors); buys; trades own accounts; own payout wallet and payouts |
| Employee (back office) | directory tier T1–T3, or Keycloak `staff` (= T2) | every account, its history and inputs; cases; passes or denies payouts (T2+, never their own); trades none; no settings |
| Admin (back office) | directory T4–T5, or Keycloak `admin` (= T4) | what an employee sees; authorises payouts, voids and profit removals; the admin-only settings below |

What each employee may do comes from the staff directory (next section).

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
  (`one-step-s1-v2-a1`): accounts already open keep theirs.

Maker-checker [staff spec 5a-2; outline M07, M25]: each change above is
first a proposal (the PUT answers `"status": "proposed"`); it takes effect
only when a second admin (T4 or above, never its maker, second factor
entered in the last 5 minutes) approves it within 72 hours:
`POST /admin/settings/proposals/{id}/approve`. Another admin may `reject`
it, the maker may `withdraw` it; `GET /admin/settings/proposals`
(`?open=false` for all). The limits are checked when proposed and again
when approved; the change is recorded in the maker's name with the
approver's (`approved_by`, `proposal_id`). Admins see each proposal on
their staff page (to-do and a notification) until it is decided.
`[staff] settings_need_second_admin`, `proposal_expiry_hours` in the policy
(the policy file only: no setting can switch the check off).

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

## Restricted news events (M13)

No trade opens or closes on an affected symbol from 2 minutes before to
2 minutes after a listed release, in every phase; crypto is exempt.

- The list: one file per month in `config/news/` (`YYYY-MM.toml`), built
  from the publishers' own schedules (BLS, Federal Reserve, ECB, Bank of
  England, EIA); no paid calendar API. Each event has an id, a title, its
  scope (a currency, or the weekly oil inventory report), the publisher's
  local time and zone, and the source address. Times convert to UTC when
  the file is read; a local time that a daylight-saving change skips or
  repeats is refused. Every change is a new `version` in git.
- Publication is automatic: when the API starts, every calendar file is
  published (author `system:calendar`), so a new month goes live with the
  deploy that adds its file. Events the engine already shows and windows
  already started are skipped; anything else repeated is refused by the
  engine, so a cancelled event never comes back. An employee can publish a
  month again by hand (`POST /staff/news/calendars/{month}/publish`, with a
  reason).
- Unscheduled events: an employee adds a window with a reason
  (`POST /staff/news/events`). Bank of Japan decisions have no fixed time:
  when the statement is out, an employee marks it published
  (`POST /staff/news/published`) and the window runs 2 minutes from then.
- Cancelling lifts a restriction, so only an admin may
  (`POST /admin/news/events/{id}/cancel`, with a reason), and only before
  the window starts.
- Every action is logged with who, why, the input sent and the engine's
  answer (`GET /staff/news`, append-only `news_actions`). Traders see the
  windows with their titles (`GET /news`). The calendar is also public,
  as other firms publish theirs: `GET /calendar` (no sign-in; adds the
  release time, currency and the publisher's schedule page), the portal's
  News page, by day in the viewer's time zone or UTC, with a countdown to
  the next release.
- Until the back office (step 5) these are API calls.

### The news collector (FTMO's list, read from the publishers)

The restricted events follow FTMO's list (31 Oct 2026). The API reads the
publishers' own schedules at start and every 6 hours
(`propfirm.newsfeed`) and sends every upcoming event (45 days ahead) straight
to the engine, logged as `collector` with its source:

| Publisher | Events | Read from |
|---|---|---|
| Federal Reserve | rate decision and statement, FOMC minutes | monthly events pages |
| Statistics Canada | CPI, Labour Force Survey | key-indicator schedule (JSON) |
| Stats NZ | labour market, GDP, quarterly CPI | release calendar (iCalendar) |
| ONS | UK CPI | release calendar API |
| ABS | CPI, Labour Force, national accounts (GDP) | monthly release calendar |
| Bank of Canada, RBA, SNB, Bank of England, ECB | rate decisions | their schedule pages |
| BEA | advance GDP | release schedule |
| EIA | crude oil inventories | weekly schedule and holiday exceptions |

BLS (US jobs and CPI) and the Reserve Bank of New Zealand refused automated
reads when checked; the collector still tries them every run, and the
monthly files in `config/news/` carry them meanwhile. The files hold the
whole list too, with the same event ids, so a release in both is one event.

When a source cannot be read (refused, down, or its page changed), staff
and admins are emailed (`NEWS_ALERT_EMAILS`, comma-separated) which events
are no longer collected, with the publisher's page, so they add them by hand
(`POST /staff/news/events`); a reminder follows every 24 hours while it still
fails (`NEWS_ALERT_REMIND_HOURS`), and a note when it is read again. Open
alerts show in `GET /staff/news` (`source_alerts`); every alert is kept
(append-only `settings.news_source_events`). No third-party calendar is read (Forex Factory and similar:
their terms and the licence for showing their data; CC-5). A source that
fails is logged and skipped. A release the publisher moves gets a second
window at the new time (`<id>-at<HHMM>`); the old one stays until an admin
cancels it. Settings: `PROPFIRM_NEWS_COLLECTOR` (1/0), `PROPFIRM_COLLECT_HOURS`,
`PROPFIRM_COLLECTOR_AGENT` (the User-Agent naming the firm).

## Live prices: Twelve Data (P2 step 6)

`propfirm.service.twelvedata` holds one WebSocket connection to
`wss://ws.twelvedata.com/v1/quotes/price` (Twelve Data allows 3 per key),
subscribes the launch symbols (`config/market/twelve_data.toml` maps each to
Twelve Data's symbol), sends a heartbeat every 10 s, reconnects after 30 s
of silence or a drop (5 to 60 s backoff), and publishes each price to the
engine's input stream as a `PriceUpdate`.

```sh
# .env (never committed): TWELVE_DATA_API_KEY=...
docker compose -f compose.yaml -f compose.live.yaml --profile platform up -d --build --wait
```

- Prices are read as decimals and become integer points exactly; a price
  with more decimal places than the symbol's `digits` is refused and
  counted, never rounded. Source time: Twelve Data's whole UNIX seconds
  (so up to 1 s of each measured delay is truncation; the delay guard
  freezes at 2 s). Receive time: this machine's clock.
- Sequence numbers continue across restarts (kept in Valkey, written in
  the same transaction as the update; a crash gives a gap, never a reuse).
- `compose.live.yaml` sets `PROPFIRM_PRICE_SOURCE=twelve_data` everywhere
  (attribution "Data provided by Twelve Data" beside prices) and leaves the
  synthetic feeder out; the feeder refuses to run with a licensed source.
- `PROPFIRM_FEED_SYMBOLS=EUR/USD,XAU/USD` limits the subscription (credits).
- The API key is read from the environment only and never logged: errors
  show the error's kind, not its message (which may carry the URL).

**Trial (C10).** The connector counts per UTC day and symbol: prices,
refusals, the most decimal places streamed, delay buckets (receive minus
source: 1, 2, 5, 10, 30 s), the worst delay and the longest silence, plus
connection events (connects, drops, failed subscriptions). No prices are
kept in these counters. After the 12 days:

```sh
docker compose -f compose.yaml -f compose.live.yaml run --rm --no-deps twelvedata \
  /opt/venv/bin/python -m propfirm.service.trial 2026-11-09 2026-11-20
```

The report gives per symbol the verdict on `digits` ("ok" or "raise to N")
and the share of prices inside the delay guard. To settle in C10: `digits`
per symbol, the WTI and Brent symbols (`WTI/USD`, `XBR/USD` until
confirmed), the crypto exchange Twelve Data picks when none is named,
whether the delay threshold (2 s) and gap thresholds fit the real feed.

**Purge (licence).** Twelve Data prices are kept only while subscribed and
purged within 30 days of cancelling (CLAUDE.md). Stop the engine, projector
and connector, then:

```sh
docker compose -f compose.yaml -f compose.live.yaml run --rm --no-deps twelvedata \
  /opt/venv/bin/python -m propfirm.service.purge --licensed --confirm "purge licensed prices"
```

It takes the journal's and the projector's writer locks first (a running
one stops it before anything is deleted), refuses while the engine has
unread or unacknowledged inputs, then empties the input stream, the
journal's licensed tables (prices, price keys, snapshots: the chain still
verifies, replay is no longer possible), the projections (bars, account
histories; cursor moved to the head) and the live state. It prints the
rows and keys deleted: keep that output as the record of the purge.

## Security scans (P2 step 7a)

CI job `security`, on every pull request and push to main:

| Check | Tool | Fails on |
|---|---|---|
| Python dependencies, every group | pip-audit 2.10.1 on `uv export` (hashes required) | any known vulnerability |
| Python code | bandit 1.9.4 (`[tool.bandit]` in `pyproject.toml`) | any finding |
| Secrets in tracked files | detect-secrets 1.5.0 against `.secrets.baseline` | a finding not reviewed, or marked real |
| Browser dependencies (portal, back office) | `npm audit --omit=dev` + `scripts/security_gate.py` | moderate or above, unless accepted in `security/accepted-advisories.toml` (reason and review date; an expired acceptance fails) |
| Runtime image | Grype 0.121.0 (pinned digest) | a fixable High or Critical; also checks the image runs as uid 10001 |

- Bandit skips: B101 (asserts narrow types; images never run with `-O`) and
  B608 (SQL from strings): `tests/test_sql_strings.py` allows only reviewed
  identifiers inside SQL f-strings (validated schema names, constant table
  names and clauses); values are always `$n` parameters.
- The baseline holds development values only (the development realm's
  client secret, test passwords, test webhook secrets, alphabets). After
  adding one on purpose: `uvx detect-secrets==1.5.0 scan --baseline
  .secrets.baseline --exclude-files '(^|/)(package-lock\.json|uv\.lock)$'`,
  then set `"is_secret": false` on the new entry once reviewed.
- Images: `Dockerfile` builds `runtime` (the services: no development
  tools, no tests, no uv, user `pipridge`, uid 10001) and `test` (the
  suite). The base image is pinned by digest; take Debian security fixes by
  moving to a newer digest. `anchors-init` hands a volume made by an older
  root-run image to uid 10001 once (the only container run as root; it
  exits). `.dockerignore` keeps `.env`, keys and the web apps out of the
  build context.
- Not covered here (step 7): the yearly external penetration test, SAST
  for the web apps beyond `npm audit` and ESLint, a dynamic scan of the
  running API.

## Security audit trail (P2 step 7b)

Who did what through the API, kept so that nobody, the database's own
operators included, can change it unnoticed (`src/propfirm/audit/`).

- **Recorded:** every refusal (401, 403) on any route; every request to
  `/staff/*` and `/admin/*`, reads included (staff reading personal data);
  every other write (orders, payout requests, wallet changes, webhooks).
  Not recorded: trading writes (the event journal records them in full)
  and traders' and the public's reads. Each record: time, login subject,
  method and route template, path parameters, status, client address,
  request id (returned as `X-Request-Id`). Never a request or response body.
- **Append-only and chained:** schema `audit`; each record's hash covers the
  one before. The database refuses UPDATE, DELETE and TRUNCATE, and any row
  that does not continue the chain. A failed audit write is logged; it does
  not fail the request (the action has already happened).
- **Anchored and checked:** the `auditor` service checks every new record,
  copies the head outside the database (`PROPFIRM_AUDIT_ANCHOR_PATH`) at
  most every `PROPFIRM_AUDIT_ANCHOR_MS` (5 minutes), and compares the latest
  anchor with the log: an edit, removed records or a replaced log is
  reported (`audit.chain_broken` alert to the SIEM, red banner in the back
  office) and anchoring stops. One-off check:
  `python -m propfirm.audit.service --verify` (exit 1 if broken).
- **SIEM:** records are shipped as Elastic Common Schema JSON lines, with
  seq and hash, to `PROPFIRM_SIEM_SINKS`: `stdout` (default),
  `file:/path`, `syslog+tls://host:6514` (RFC 5425) or
  `syslog+tcp://host:514`. Each sink keeps its own cursor; a sink that is
  down is retried. Which SIEM the firm runs is an open decision.
- **Who reads it:** admins and owners, back office → Security log
  (`GET /staff/audit`).
- **Client address:** `PROPFIRM_CLIENT_IP_HEADER` (X-Forwarded-For behind
  Caddy): the last entry, the one the proxy added. Only set it when the API
  is reachable through the proxy alone.
- **Open for production (step 7c):** anchors on storage the database's
  operators cannot write (object storage with a retention lock); separate
  database roles (the API may only insert, the auditor only read); the SIEM
  choice and its alert rules (repeated refusals, approvals out of hours).

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
  never moves it), V profit removed for violations by confirmed case
  decisions and not yet taken out by a payout;
- the trader's share s·P rounded down to the cent (s the product's split),
  the firm's share the rest; a fee refund (1-Step: with the 3rd payout) on
  its own line; `payouts.maximum_cents` caps the trader's amount (refund
  included; it may not be set below a refundable fee); `debit_cents`, what
  leaves the account: P + V (capped: G + V, G = the share paid ÷ s rounded
  up to the cent, the rest of P stays payable);
- checks: funded and active; the first payout from the trading day 14
  days after that of the first trade on the account (as FTMO; trading days
  end at 17:00 New York), a later one 14 days after the last
  (`interval_days`);
  trading days in the period; the account's own consistency rule over the
  period; the $50 minimum; no open position or pending order
  (`payouts.flat_required`); an approved Tier 2 check and a customer not
  blocked; the country gate (when on); no case under review (shown to the
  trader only as "held for review"); no payout of the account in progress;
  a payout wallet (past its waiting period, if one is set);
- `opens_at`: when a payout may be requested (on request, as FTMO and
  FundedNext; no fixed pay days).

The payout settings (D22, D23) are in `[payouts]` of the policy: closed
balance, flat at request, and, as FTMO and FundedNext, no minimum trading
days and no maximum.

## Staff directory and approvals (back office 5a)

Access is a tier, functional roles and time-limited grants, each approved
by someone else (staff spec, 31 Oct 2026). Tiers: T0 Apprentice, T1
Associate, T2 Specialist, T3 Team lead, T4 Admin, T5 Owner. Roles:
`support`, `kyc`, `risk`, `payouts`, `integrity`, `product`, `marketing`,
`it`; below T4 a permission needs its role (`propfirm.staff.model`).

Sections [decided 6 Nov 2026]: each role is a section with its own ladder,
Apprentice (T0), Associate (T1), Specialist (T2), Team lead (T3): one person
may be Lead in KYC, Specialist in Payouts and Apprentice in Risk at once.
Admin (T4) and Owner (T5) are firm-wide. A section level is a grant
(`"grant": "level", "value": "kyc:2"`) replacing the previous level of that
section. Assignment [decided 6 Nov 2026, clarified the same day,
`staff.sections_approval = "home_section"`]: in the member's own section
(their team) an employee of that section (level T1+,
`home_proposer_min_level`) proposes and an admin approves; in another section
an admin proposes and an owner approves; for an admin, one owner proposes and
the other approves; never the proposer. Moving someone to another team that
is a section needs an admin. Staff and admins may hold several sections.
(`"owner"`: every section as another section; `"admin"`: the section's lead
or an admin asks, an admin approves.) A section lead may still revoke a
level in their section (removing access needs no second
person). `GET /staff/me` shows `access.sections`. Joiners: `"sections":
{"kyc": 1}` (so only an admin or owner joins someone with sections). A
graduate starts as Associate (T1) in each section learnt as an apprentice:
proposed by the graduating admin, in force once another admin (their own
section) or an owner (another section) approves. The full path: Apprentice (APP login) → graduation → Associate
→ Specialist → Lead, per section → Admin (two owners) → Owner.

| Change | Asked by | Approved by | Ends |
|---|---|---|---|
| tier (promotion) | T3+ | T4+; T4 or T5: two owners | when another tier is approved |
| section in the member's own section (team), for T3 or below | an employee of that section (T1+) or an admin | an admin, not the proposer | level: until replaced; role: optional date |
| section in another section, for T3 or below | an admin (T4+) | an owner, not the proposer | as above |
| section (a charge) for an admin | an owner | the other owner | as above |
| admin power (`settings.view`, `settings.change`, `news.cancel`) for T3 or below | T3+ | a second admin, or an owner if asked below T4 | required, at most 90 days |
| acting cover (up to the asker's tier, T3 at most) | T3+ | T4+ | required, at most 30 days |

Nobody asks for, approves or revokes their own access, and the asker never
approves. Grants end on their own. A leaver (`POST /staff/members/{sid}/leave`,
by a higher tier) has no access at once and every grant ends.

Leavers' logins [decided 6 Nov 2026; 5d]: the login of every member who
left, graduated or reached an end date is disabled at Keycloak and all its
sessions ended, at once and then every minute until done (a Keycloak
service account with `manage-users` only: `STAFF_IDP_URL`,
`STAFF_IDP_CLIENT_ID`, `STAFF_IDP_CLIENT_SECRET`; the development realm's
`staff-cutoff`). Not done within 60 minutes (`leaver_cutoff_alert_minutes`):
an urgent notification to leads and admins to do it by hand and confirm it
(`POST /staff/cutoffs/{sid}/done`); again at 24 hours, the hard limit.
`GET /staff/cutoffs`: each one pending, late, overdue or done, and how. A
leaver's open cases show on every case worker's to-do list to take over.

Oversight [staff spec 5d]:
- escalation: an urgent notification nobody acknowledged within 4 hours
  (`escalate_hours`) is raised to leads and admins, or a member's own to
  their lead; once each;
- dormant accounts: no sign-in (a day's first staff request with MFA) for
  30 days (`dormant_days`): access suspended (status `dormant`) until an
  admin reinstates it (`POST /staff/members/{sid}/reinstate`, step-up);
  owners are exempt;
- access reviews: each lead reviews their team every 90 days, owners each
  admin every 30 (`review_days_lead`, `review_days_admin`): due ones on the
  reviewer's to-do list; `POST /staff/members/{sid}/review`
  (`keep` or `change`, with a note; grants are changed as usual);
- anomaly report (`GET /staff/reports/anomalies?days=7`, admins and
  owners): payout, case and grant approvals outside office hours (Dhaka,
  Sunday to Thursday, 09:00 to 18:00: `office_*`), and the same maker and
  checker together `pair_threshold` (3) times or more. Owners are recorded once at start-up from
`STAFF_OWNERS="<keycloak id>=Owner A;<keycloak id>=Owner B"`; an owner's
tier is never changed through the API.

Apprentices (`APP00000001`, their own login): a T2 or T3 mentor, a term of
3 to 6 months (`ends_at`), T0 in the sections they learn [staff spec 5c]:
- they read the same back-office records as staff, with trader names,
  documents, wallets and contact details masked (`•••`); money and trading
  figures visible (`propfirm.staff.masking`);
- they never act on a live record: they propose (`POST /staff/apprentice/proposals`,
  a case note or a case decision), and their mentor's approval carries it
  out in the mentor's name, naming the apprentice (`[proposed by APP…]`);
  the mentor sees each proposal on their to-do list;
- the training gate: a level of T1 or above in a section, and graduation,
  need the section's course (`config/staff/courses.toml`) passed within 12
  months (`training_gate`, `training_valid_months`), recorded by a T2+ of the
  section or an admin (`POST /staff/members/{sid}/training`); a graduate
  keeps the apprentice's passes; `GET /staff/me` shows `training`, and the
  to-do list a missing or lapsing course. The mentor recommends (`/recommend`); an admin other than the mentor
graduates them (`/graduate` with the new staff login): a new T1 member
linked to the apprentice record.

Decided 6 Nov 2026: every staff and admin login needs a second factor, not
a password alone (`login_needs_mfa`; 401 `mfa_required` otherwise); staff
and apprentices never buy or trade (`staff_may_trade = false`: a login with
staff access gets no trader view, 403 `staff_trading_banned`; households
are declared, 5d); a funded review is approved by a T2 or above on the risk
desk (`progression.decide`).

Payouts, refunds and voids [decided 31 Oct 2026]: a T2 or above passes
(`payouts` role for payouts, `integrity` for voids), an admin authorises; no
amount is approved below admin. The fee refund is paid with a payout and
follows it. Every approval (payouts, voids and profit removals, funded and
identity reviews, grants, graduation, settings, news cancels) needs a second
factor entered in the last 5 minutes (`[staff]` in the policy; 401
`mfa_required` or `reauth_required`, RFC 9470).

Endpoints: `GET /staff/me` (access, grants, what waits for my approval),
`GET|POST /staff/members`, `GET /staff/members/{sid}`,
`POST /staff/members/{sid}/move|leave|recommend|graduate`,
`GET|POST /staff/grants`, `POST /staff/grants/{grt}/approve|deny|cancel|revoke`.
Records are append-only (`staff.member_events`, `staff.grant_events`).
A login with no directory record keeps its Keycloak role (`staff` T2 with
every role, `admin` T4) until `STAFF_LEGACY_ROLES=0`.

Staff page (5b), `GET /staff/me`: besides access and grants,
- the **to-do list** (`GET /staff/me/todo`), built from every queue the
  login's permissions reach, soonest due first: payouts waiting for its
  step (pass, or authorise), cases to take or work and decisions to confirm
  or authorise, identity and funded reviews, grants and graduations to
  approve, its own grants ending within 7 days, blocked news sources to
  fill, duty sheets not yet acknowledged; never its own request or a payout
  it already approved;
- **duties**: the written responsibilities of each sheet that applies (each
  role held; apprentice, admin, owner), versioned in `config/staff/duties.toml`;
  `POST /staff/me/duties/{sheet}/ack` records that the version in force was read;
- **notifications** (`GET /staff/notifications`, `?all=true` with the done
  ones; `POST /staff/notifications/{id}/ack`): each has an owner, a
  permission (whoever holds it now) or one member, and stays until that
  person acknowledges it or the work it points to closes. A news source the
  collector cannot read notifies the `news.desk` holders (risk desk and
  admins) and emails them (directory emails, plus `NEWS_ALERT_EMAILS`);
  reading it again closes it. Escalation to the lead comes with 5d;
- **messages**: internal threads on one record (`GET|POST /staff/threads/{ref}`,
  `ref` a CAS, PO, ONB, PRG, ACC, CIF, GRT, STF or APP id; `GET /staff/threads`:
  the latest discussed), append-only, never sent outside.

Locally: `staff1` and `admin1` have authenticators in the development
realm; `scripts/dev_token.py` signs them in with their code and prints a
token that passes the staff rules, step-up included.

## Payouts

USDT on TRON (TRC-20) to the trader's wallet (M06, M19), `PO00000001`:

1. Wallet: `POST /me/wallet` `{"address": "T..."}` (a TRON address,
   checked by its Base58Check checksum); a new one replaces the old and is
   usable at once, as at FTMO, FundedNext and The5ers
   (`wallet_cooling_off_hours` = 0; a wait can be set). Every wallet added
   or changed emails the customer's registered address ("Your payout wallet
   was changed": the new and previous wallet, the time, what to do if it was
   not them), as exchanges do for a new withdrawal address. The email is
   written in the same transaction as the wallet (an outbox) and sent by the
   background round, retried every minute; through SMTP when `SMTP_HOST` and
   `MAIL_FROM` are set (`SMTP_PORT` 587, `SMTP_TLS` `starttls` or
   `implicit`, `SMTP_USERNAME`, `SMTP_PASSWORD`; any transactional provider
   such as Amazon SES or Postmark), otherwise through the sandbox mailer,
   which sends nothing.
2. Request: `POST /accounts/{acc}/payouts`, when eligible. The request
   keeps the eligibility it was based on, the trader's amount and the
   debit. One payout in progress per account; until it is debited (or
   denied, cancelled, failed) the account takes no new orders
   (`trading_during_payout = "paused"`, 409 `payout_pending`). The trader
   may cancel until it is fully approved (`POST /me/payouts/{po}/cancel`).
3. Approval: `POST /staff/payouts/{po}/approve`, twice: a pass by a T2
   or above (`payouts` role), then the authorisation by an admin, another
   person (`approval = "pass_authorise"`, decided 31 Oct 2026); never on
   their own customer record; one `deny` (T2+) ends it. `GET /staff/payouts`, `GET /staff/payouts/{po}` (wallet and
   history).
4. Once approved (the API's background round, every second): eligibility
   is computed again;
   anything changed (a check failing, the amount, the wallet) fails the
   payout and nothing is taken. Otherwise the engine takes `debit_cents`
   out of the account (input `DebitPayout`, event `PayoutDebited`, ledger
   entry `payout:PO…` to `firm:payouts`); it refuses a second debit of the
   same payout, open positions, and more than the profit above the
   baseline. The day's loss base and the high-water mark drop by the same
   amount (D42 [DEFAULT]: a trailing floor restarts from the balance after
   the payout).
5. Sending: the payout provider sends the trader's amount; its
   confirmation marks the payout paid. Until NOWPayments is connected (C6),
   only the sandbox provider exists (`PROPFIRM_SANDBOX=1`; it sends
   nothing); without it the payout endpoints answer 503.

Changing the wallet and requesting a payout need the second factor
entered in the last 5 minutes (`require_mfa`, `step_up_minutes`; step-up,
as Binance asks for a 2FA code when a withdrawal address is added and
FundedNext an OTP for each payout request). Otherwise the API answers 401
with `WWW-Authenticate: Bearer error="insufficient_user_authentication",
max_age=300` (RFC 9470), and the portal signs the trader in again. It reads
the access token's `amr` and `auth_time` claims; the development realm's
tokens carry neither yet (realm setup comes with the portal login), so
these requests are refused until it does. `payout_events` and `wallets` are
append-only; the payout's facts (amount, debit, wallet) never change. The
funded agreement states these terms (`funded-agreement-v2`; contracts
signed on v1 keep their text).

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

## News rule (as FTMO, decided 31 Oct 2026)

- Who: restricted news windows bind funded accounts only; evaluation stages
  (Lite, Classic and 1-Step s1/s2) are exempt. Each rule set says so
  (`news_restricted`), so the rulebook, the portal and the engine read one
  value; the evaluation stages are version 2 for this change.
- Which symbols: FTMO's affected instruments, as the versioned instrument
  map `ftmo-1`: a currency event blocks the forex pairs with that currency,
  plus gold for USD events; the weekly oil inventory report blocks WTI and
  Brent; crypto is exempt. Events recorded under the original map (`v1`,
  which also blocked silver and oil on USD events) replay unchanged.
- What happens inside a window on a funded account: orders and closes are
  refused; pending orders wait; a stop-loss, take-profit or trailing stop
  still fills, and that fill is a `NewsViolation`. The API turns each one,
  once, into a high-severity integrity alert on the customer's case; as
  FTMO, a violation can end the account, and staff decide (a void needs a
  second employee). Breach and void close-outs are never violations.
- Journal compatibility: `added_field` lets a journaled type gain a field
  with a default; it is left out of the encoding while it holds that
  default, so earlier records keep their exact text and hash.

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
- [x] Step 3: lifecycle Stage 1 → Stage 2 → Funded, payouts
  - [x] 3a: stage progression (next evaluation account, funded review)
  - [x] 3b: funded onboarding (Tier 2 verification, contract, signature)
  - [x] 3c: payable profit and payout eligibility
  - [x] 3d: payout processing (two-person approval; sandbox payout provider until C6)
- [ ] Step 4: trader portal
  - [x] 4a: foundation, login (PKCE, optional 2FA, step-up), registration, checkout, accounts
  - [x] 4b: account dashboard, live prices, chart
  - [x] 4c: order ticket, positions, history, rulebook, news calendar
  - [x] Trailing orders (engine, API, portal)
  - [x] News calendar input: monthly files, risk-desk publishing, manual and Bank of Japan windows
  - [x] 4d: funded onboarding, wallet, payouts
- [x] Step 5: back office
  - [x] 5a: staff directory, tiers, roles, grants, four eyes, step-up on approvals,
    pass then admin authorisation of payouts, voids and profit removals
  - [x] 5a-2: maker-checker on admin settings (a second admin)
  - [x] 5b: staff page: to-do from the work queues, duty sheets, notifications by
    permission, messages on records
  - [x] 5c: apprentices' masked views, proposals to the mentor, training gate
  - [x] 5d: oversight: leaver cut-off, escalation, dormant accounts, access reviews,
    anomaly report
  - [x] 5e: back-office screens (react-admin)
- [ ] Step 6: Twelve Data connector and 12-day trial (C10)
  - [x] 6a: connector, exact points, continuing sequence numbers, trial counters and
    report, licensed-data purge
  - [ ] 6b: the 12-day trial on a paid key (C10), then `digits` and thresholds settled
- [ ] Step 7: staging, backups and restore drill, security scans
  - [x] 7a: security scans in CI (dependencies, code, secrets, image) and a non-root
    runtime image
  - [x] 7b: security audit trail: hash-chained, anchored, checked, shipped to a SIEM;
    back-office security log
