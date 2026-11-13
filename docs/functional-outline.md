# Prop Firm Operating Engine — End-to-End Functional Outline

> **Provenance.** Imported on 13 Nov 2026 from the Claude Doc of the same
> name (revision 290, last edited 28 Oct 2026), build plan step 0. From this
> import the repository copy is the specification of record; the Claude Doc
> is kept as an archive and no longer edited. Three diagrams do not export
> to Markdown and are marked where they stood.
>
> **Later decisions win.** Where this outline and `docs/decisions.md`
> disagree, the dated decision applies (for example D-13.9, the news rule,
> and D-13.17, the product line). Change this file only through a pull
> request that also records the decision.


Oct 29, 2026 · @Sayed Naim

## 0. Scope, Conventions and Flags

The engine needs 32 modules (M01–M32), now configured with the firm's confirmed values (Confirmed Configuration, CC-1 to CC-9). Appendix A maps the flows, Appendix B marks which of the 44 operator decisions the project record settles, and Appendix C disposes of the 28 Oct 2026 audit.

**Scope**: functional and operational requirements of a retail evaluation-model proprietary trading firm, from registration to account closure, configured for this project. Sources: Prop Firm Plan v4 (Audited, 20 Oct 2026), the pricing thread (final list, 16 Oct 2026), the capital thread (17 Oct 2026), the legal verification audit (16 Oct 2026) and the external audit of 28 Oct 2026. Excluded: legal opinions and source code.

**Conventions**

- Stages: Stage 1 (S1), Stage 2 (S2), Funded (FND), as instructed; each product's stage path is in CC-2.
- Identifiers: CIF Customer Information Form (the master customer record) · ACC trading account · PRG program · RSV rule-set version · ORD order · INV invoice · PAY pay-in · PO payout · KYC verification case · CASE risk/fraud/compliance case · TKT support ticket · EVT event · UPD price update.
- Time: stored in UTC at millisecond precision; the server day runs from 17:00 to 17:00 America/New\_York (M11).
- Money: whole cents (integers) in USD; never floating point.
- IB: initial balance (initial capital) of the account's current stage.
- Tags: \[CONFIRMED\] settled in the project record; \[DEFAULT\] a Plan v4 proposal, or a value chosen on your instruction to decide for you, that you can change; \[OPEN\] no decision recorded; \[LIVE\] only with A-book routing, deferred to expansion; (MAY) optional component.
- References: Dn operator decision (Appendix B); In invariant (M32); CC-n confirmed configuration; Cn open confirmation (CC-9).

**Instruction flags**

- F-1 “Funding” is resolved by the project record: a hybrid book in which evaluations and funded accounts are simulated (B-book) at launch. A-book routing for proven funded traders is deferred to expansion and needs a broker or liquidity-provider agreement (CC-1, D1).
- F-2 Conflict: the instruction requires a minimum of three stages, but two confirmed products have fewer — 1-Step (S1 → FND) and Instant (FND only). The engine supports any stage count, and Lite and Classic use the three-stage path. If three stages is a hard rule, 1-Step and Instant must be withdrawn; that choice is yours and is not made here.
- F-3 Where the project record labels a value “default”, this outline carries it as \[DEFAULT\], not \[CONFIRMED\]; where the record is silent, the item is \[OPEN\].

**Self-review flags**

- Legal, tax and licensing duties remain determinations for counsel at gate G0 (CC-8, C8).
- Detection thresholds in M15–M17 are uncalibrated; fit and back-test them on the firm's own data before enforcement.
- Project-record figures (vendor prices, fees, calendar dates) are as of 16–20 Oct 2026 and were not re-checked today; the Pricing Study document is stale on two 1-Step prices (CC-2).
- Corrected after the audit: GDPR Art. 17(3)(b) covers only EU or member-state legal duties, and the EU 30-day prior-price rule covers goods only (Appendix C). FATF R.11 and the 2026 DST windows were independently confirmed by the audit.
- MT5 references were removed: the confirmed platform is the firm's own engine and portal.

## Confirmed Configuration (Project Record)

The firm launches four products on its own engine, prices every trade from one licensed feed (Twelve Data), takes payment only in USDT on TRC-20, and refuses Bangladesh and India residents. Values come from Prop Firm Plan v4 (Audited, 20 Oct 2026), the pricing thread (final list, 16 Oct 2026), the capital thread (17 Oct 2026) and the legal verification audit (16 Oct 2026). Tags: \[CONFIRMED\] settled; \[DEFAULT\] a Plan v4 proposal, or a value chosen on your instruction to decide for you, that you can change; \[OPEN\] no decision recorded.

### CC-1 Settled inputs

| Input | Settled value \[CONFIRMED\] | Decided |
| --- | --- | --- |
| Structure | A Bangladesh software company builds, hosts and licenses the platform; an offshore operating company owned by a non-resident partner runs the firm (entity not yet formed) | Oct 2026 |
| Customers | Global; Bangladesh and India residents excluded | Oct 2026; India 19 Oct 2026 |
| Payment rails | Crypto only at launch (USDT on TRC-20); cards later through a prop-specialist processor; no local rails | 19 Oct 2026 |
| Timing | Build only until the founder's launch condition is met; no public or paid activity before then | Oct 2026 |
| Legal | An earlier review found Bangladeshi law did not support the business; clearance is assumed at your instruction, so gate G0 (licensed counsel) comes before the first paid spend | 17 Oct 2026 |
| Book model | Hybrid: B-book for evaluations; A-book only for proven funded traders, deferred to expansion | Oct 2026 |
| Platform | Own engine and own trader portal; not MT5 and not a CodeCanyon script | 17 Oct 2026 |
| Software | Open source, lowest workable cost | 17 Oct 2026 |
| Prices | One licensed feed (Twelve Data) prices every trade from the first paid challenge; free sources never price trades | 18 Oct 2026 |
| Chart | TradingView Lightweight Charts drawn from the firm's own feed (TradingView's data is display-only) | 18 Oct 2026 |
| Products | Lite, Classic, 1-Step and Instant; no Swing | 16–20 Oct 2026 |
| Budget | ৳300,000 founder capital pays for building and hosting; the partner pays the operating company's running costs | 20 Oct 2026 |
| Build tools | Claude Pro, Gemini Pro (2 accounts), ChatGPT Go, DeepSeek API | 17 Oct 2026 |
| Build machine | HP 280 G4: i3-8100, 16 GB RAM, Windows 11, virtualization enabled; Docker Desktop to be installed | 19–20 Oct 2026 |

### CC-2 Product line and prices

Four products, all at an 80% profit split; loss limits are percentages of initial capital \[CONFIRMED\]. The profit-target basis is \[OPEN\] (D10).

| Product | Stage path (from targets) | Profit targets | Max loss | Daily loss | Consistency | Fee refund | Sizes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Lite | S1 → S2 → FND | 6% then 6% | 6% static | 3% | None | None | 5K–200K |
| Classic | S1 → S2 → FND | 10% then 5% | 8% static | 4% | None | None | 10K–200K |
| 1-Step | S1 → FND | 10% | 6% static | 3% | Best day ≤ 50% of profit | With the 3rd payout | 5K–200K |
| Instant | FND only | None | 5% trailing; stops trailing at the starting balance | 3% | Best day ≤ 50% of profit | None | 5K–25K |

| Size | Lite (USD) | Classic (USD) | 1-Step (USD) | Instant (USD) |
| --- | --- | --- | --- | --- |
| 5K | 24 | Not offered | 37 | 109 |
| 10K | 44 | 54 | 79 | 199 |
| 25K | 99 | 119 | 199 | 479 |
| 50K | 179 | 209 | 299 | Not offered |
| 100K | 319 | 409 | 499 | Not offered |
| 200K | 639 | 799 | 999 | Not offered |

Lite, 1-Step and Instant prices are the pricing thread's final list (16 Oct 2026); its 1-Step 5K $37 and 100K $499 supersede $39 and $519 in the Pricing Study document, which was never updated. Classic was priced on 19 Oct 2026 at 0.85–1.02× FundedNext Lite: margin +20% to +25% in the normal case, −12% to −28% in a payout spike.

**Discount and pricing rules**

- Codes: up to 10% off a first Lite, Classic or 1-Step account; no codes on Instant. \[CONFIRMED\]
- Repeat accounts: 10% off the 2nd and 15% off the 3rd; Instant 10% and 10%. \[CONFIRMED\]
- Never combined; no affiliate commission on repeat-account sales; no other discounts. \[CONFIRMED, pricing thread\]
- Instant: the fee after any discount must cover a no-skill trader's maximum withdrawal, which is why its repeat discount stops at 10%. \[CONFIRMED, pricing thread\]
- Not offered: Swing; the 10%-loss 2-Step (on hold). \[CONFIRMED\]
- 90% split add-on at +12% of list: keep or drop is your decision C16; until then 90% comes only through scaling. \[OPEN\]
- Reset or retake price: the Pricing Study document lists 80% of list, the later discount rules allow no other discounts, and Plan v4 is silent. \[OPEN, conflict; D27\]

### CC-3 Trading rules

The same rules apply to every product unless CC-2 says otherwise.

- Minimum trading days: 4 per evaluation phase; no time limit. \[CONFIRMED\]
- Daily loss: resets at 17:00 America/New\_York; the limit is measured from the higher of balance and equity at the reset, and equity is tested against it continuously. \[CONFIRMED\]
- News: no trade opened or closed from 2 minutes before to 2 minutes after a listed event on an affected symbol, in every phase. Affected: pairs containing the event's currency; XAU, XAG, WTI and Brent for US events; WTI and Brent for the weekly EIA oil report; crypto exempt. \[CONFIRMED\] Inside a window, pending entries (limit, stop, trailing) are held and checked on the first price after it. Exits (stop-loss, take-profit, trailing stop) still execute; any profit they make is excluded from target and payout figures, and losses stand. Breach close-outs are exempt. \[DEFAULT\]
- Expert advisors and copy trading: not allowed at launch. \[CONFIRMED\]
- Weekend and holiday holding: evaluations may hold; funded accounts may not hold a position through a market break longer than 2 hours (FTMO's rule). \[DEFAULT; your decision C15\]
- Breach: on the first price where equity is at or below a limit, close every position at that price, lock the account and record the update id; a gap past the limit closes at the actual price, and the extra loss is recorded, not capped. \[DEFAULT\]
- Delayed prices: trades that exploit delayed platform prices are voided, and the account can be closed. \[CONFIRMED\]
- Disputes must be raised within 30 days of the trade. \[CONFIRMED\]
- Orders: market, limit and stop; stop-loss and take-profit on positions; no partial fills; no slippage model in v1; lot caps per order and per open position for each symbol, scaled to account size and set in P3. \[DEFAULT\] Trailing buy and sell orders are available. \[CONFIRMED\]
- Trailing orders: per order, the trader chooses a trailing stop on an open position or a trailing entry (trailing buy or trailing sell), and sets the distance and step in whole points, each at least 1. \[CONFIRMED\] Buy-side orders follow the ask and sell-side orders the bid, the prices they fill at, so a level is never triggered by a price the order could not trade at. The level moves only in the trader's favour, and only by at least the step. A triggered order fills like a stop order; news windows as M13 sets. While a guard freezes the symbol or its market is closed, the level neither moves nor triggers; the next valid price updates it first, then checks the trigger, and a gap past the level fills at that price. \[DEFAULT\] Formulas and records: M09, D43.
- Fills: market orders fill at the bid or ask of the latest price update when the server receives them; pending orders trigger when the bid (sells) or ask (buys) crosses the level; every fill stores its update id plus source, receive and fill timestamps. \[CONFIRMED\]
- Contract sizes: forex 100,000 units per lot; XAU/USD 100 oz; XAG/USD 5,000 oz; WTI and Brent 1,000 barrels; crypto 1 coin. \[DEFAULT\]
- Swaps and commissions on simulated accounts: not defined in the project record. \[OPEN, D41\]

| Asset class | Leverage \[CONFIRMED\] |
| --- | --- |
| Forex | 1:100 |
| Metals (XAU, XAG) | 1:50 |
| Oil (WTI, Brent) | 1:20 |
| Crypto | 1:3.33 |

Forex, metals and crypto match FTMO's symbol specifications (read 20 Oct 2026). Oil is 1:20, below FTMO's 1:50, because oil gaps on inventory reports and the feed can lag by up to 2 minutes.

### CC-4 Payouts, split and scaling

The first payout comes 14 days after funding, then on the 15th and the last day of each month, in USDT on TRC-20 to a verified wallet, with a $50 minimum and two-person approval \[CONFIRMED\].

- Rail: NOWPayments mass payouts, 0% service fee (vendor-stated) plus the network fee. \[CONFIRMED; onboarding pending C6\] Bank wire is not offered.
- Split: 80% on every product; 90% from the second scale-up. \[CONFIRMED\]
- Fee refund: 1-Step only, paid with the 3rd payout. \[CONFIRMED\]
- Payout liability (risk desk): sum over funded accounts of max(0, equity − initial capital) × split; new funded accounts pause while liability exceeds the payout reserve. \[CONFIRMED\]

Scaling applies to Lite, Classic and 1-Step funded accounts, not Instant \[CONFIRMED\]:

| Condition | Value |
| --- | --- |
| Review | Every 4 months |
| Net profit in the period | ≥ 10% |
| Paid payouts in the period | ≥ 3 |
| Record | Positive balance; no consistency, news or delayed-price violation |
| Increase per step | +20% of initial capital |
| 90% split | From the second scale-up |
| Maximum allocation | $400,000 per trader |

Whether a scale-up creates a new ACC or changes the account in place is \[OPEN\] (D26).

### CC-5 Market data and price model

One licensed feed prices every trade, and the chart shows exactly the prices the engine fills at; no free source ever prices a trade \[CONFIRMED\].

- Feed: Twelve Data Business Venture, $149/month billed monthly; 20% startup discount for 12 months ($119.20) once the operator is incorporated and approved (C12). \[CONFIRMED\]
- Licence: display to logged-in users and non-display use (simulated fills, rule checks) for FX, crypto and commodities at no extra fee; employees and contractors covered; 500 WebSocket credits at 1 per subscribed stream, so 30 symbols use 30. \[CONFIRMED, Twelve Data support, 19–20 Oct 2026\]
- Attribution: “Data provided by Twelve Data” (followed link) beside every chart and price table; the logo only under its brand rules. \[CONFIRMED\]
- Input: one price per update; no bid or ask on any tier; not tick-by-tick and not on a fixed interval; average delay about 170 ms, up to about 2 minutes. \[CONFIRMED\]
- Synthetic spread: bid and ask = price ∓ half the spread; spread = the larger of a per-symbol minimum and k × short-term volatility, widened inside news windows and set from the real update interval; calibrated in P3. \[DEFAULT\]
- Delay guard: when an update's source timestamp is more than 2 s behind the server clock, the symbol freezes (no new orders; existing stops and limits held); the threshold is reset from the trial (C10). \[DEFAULT\]
- Gap guard: a symbol with no update for longer than its measured 99th-percentile gap for that session freezes the same way. \[DEFAULT\]
- Feed outage: no new orders; open positions keep their last price; checks resume on the first fresh update; the outage is logged. \[CONFIRMED\]
- Reference price: the trader terms state that platform prices are the only reference for fills and rule checks. \[CONFIRMED\]
- Storage: allowed while subscribed; within 30 days of cancelling, raw prices — including fill prices reconstructable from trade records — are deleted; backups containing prices are kept 30 days at most; order ids, timestamps, balances and payouts stay. Whether per-trade P&L counts as reconstructable is counsel question C8. \[CONFIRMED\]
- Upgrade path: growth adds a second vendor as a cross-check (iTick Professional $159 or TraderMade £599), and divergence beyond a set threshold halts new orders on that symbol; long delays in London or New York hours trigger a vendor change; scale options are Twelve Data Enterprise ($1,099) or Pyth Pro ($10,000). \[CONFIRMED\]
- Not used: TradingView data (display-only), Deriv API (bars storing feed data and sublicensing), Binance, Coinbase, Gemini, OKX, Bybit, Pyth free tier, CoinMarketCap free plan, AllTick. \[CONFIRMED\]

| Group | Launch symbols (30) \[CONFIRMED; final count set by load test 6\] |
| --- | --- |
| Forex majors (7) | EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD |
| Forex crosses (14) | EUR/GBP, EUR/JPY, EUR/CHF, EUR/AUD, EUR/CAD, EUR/NZD, GBP/JPY, GBP/CHF, GBP/AUD, GBP/CAD, AUD/JPY, AUD/CAD, CAD/JPY, CHF/JPY |
| Metals (2) | XAU/USD, XAG/USD |
| Oil (2) | WTI, Brent |
| Crypto (5) | BTC/USD, ETH/USD, SOL/USD, XRP/USD, LTC/USD |

Indices are left out: outside US market data they need an add-on or a direct licence.

| Restricted event \[CONFIRMED\] | Release time | Rest of 2026 | 2027 | Source |
| --- | --- | --- | --- | --- |
| US jobs report (NFP), CPI | 08:30 New York | From the BLS calendar feed | BLS calendar feed | [BLS schedule](https://www.bls.gov/schedule/news_release/) |
| FOMC decision | 14:00 New York | 28 Oct, 9 Dec | 27 Jan, 17 Mar, 28 Apr, 9 Jun, 28 Jul, 15 Sep, 27 Oct, 8 Dec | [Federal Reserve](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) |
| ECB decision | 14:15 Frankfurt | 29 Oct, 17 Dec | 4 Feb, 18 Mar, 29 Apr, 10 Jun, 22 Jul, 9 Sep, 28 Oct, 16 Dec | [ECB](https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html) |
| Bank of England decision | 12:00 London | 5 Nov, 17 Dec | 4 Feb, 18 Mar, 29 Apr, 17 Jun, 29 Jul, 16 Sep, 4 Nov, 16 Dec | [Bank of England](https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates) |
| Bank of Japan decision | No fixed time | 30 Oct, 18 Dec | 22 Jan, 18 Mar, 28 Apr, 11 Jun, 22 Jul, 22 Sep, 29 Oct, 17 Dec | [Bank of Japan](https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm) |
| US oil inventory (WTI, Brent only) | Wednesdays 10:30 New York; holiday weeks Thursday 12:00 | Weekly; next holiday shift 12 Nov | Weekly; holiday shifts not yet published | [EIA](https://www.eia.gov/petroleum/supply/weekly/schedule.php) |

Dates are decision days in each publisher's local time, read 20 Oct 2026; the engine converts them across daylight-saving changes, and FOMC dates stay tentative until the preceding meeting confirms them. The Bank of Japan window opens when the risk desk marks the statement published and runs 2 minutes, with no window before it \[DEFAULT\]. The operator updates the list monthly and publishes it to traders; only dates and times are stored \[CONFIRMED\].

### CC-6 Vendors, stack and platform policies

Thirteen vendor and tooling decisions are recorded; NOWPayments is blocked on C6 and hosting waits on stock (C11).

| Function | Choice | Status |
| --- | --- | --- |
| Market data | Twelve Data Business Venture (CC-5) | Confirmed |
| Card payments | None at launch; a prop-specialist processor (Visa, Mastercard, wallets) at expansion; Stripe lists funded prop trading as prohibited | Confirmed |
| Crypto deposits and payouts | NOWPayments, USDT on TRC-20: 1% on single-currency deposits, 1.5% multi-currency, 0% payout service fee (vendor-stated); BTCPay Server optional for Bitcoin | Blocked on C6 |
| ID check | Didit full check (document, passive liveness, face match, device and IP): $0.33; $10 free credit a month from 1 Nov 2026 | Confirmed; account terms pending C7 |
| AML screening | Didit watchlists (sanctions, PEP, adverse media; 1,300+ lists): $0.20 per screening; daily rescreening $0.07 per person a year | Confirmed |
| Hosting | OVHcloud VPS-2 (4 vCores, 8 GB) in a European datacentre for production; founder's PC for staging until launch, then VPS-1 | Waiting on availability (C11) |
| Offsite backups | Backblaze B2 via pgBackRest: $6.95/TB-month, first 10 GB free | Confirmed |
| Transactional email | Amazon SES: $0.10 per 1,000 emails | Confirmed |
| Runtime | Python 3.14 (bug-fix support; end of life Oct 2030) | Confirmed |
| Login and identity | Keycloak (traders and staff) | Confirmed |
| Secrets | SOPS + age | Confirmed |
| Support desk | FreeScout (AGPL-3.0, run unmodified) | Confirmed |
| A-book routing | Deferred to expansion; needs a broker or liquidity-provider agreement | Deferred |

Stack, licences read at source on 18 Oct 2026: FastAPI, Uvicorn and Pydantic (API); PostgreSQL with SQLAlchemy, asyncpg and Alembic (ledger and event journal); Valkey Streams (price fan-out); React, Vite and Lightweight Charts (portal; its NOTICE text and a link to tradingview.com must appear); react-admin (back office); Caddy (TLS); Docker Engine and Compose (production); Prometheus, Grafana (internal only) and Uptime Kuma (monitoring); pgBackRest; SOPS and age; pytest and Hypothesis; Trivy, Semgrep CE, ZAP and ScanCode (security scans). Rule: anything traders use is permissively licensed; the two AGPL tools run unmodified.

**Platform policies \[CONFIRMED\]**

- AML screening at first purchase; full ID check before a funded account is issued, so ID checks are paid only for traders who reach funding.
- Bangladesh and India residents are refused: document country from the ID check plus device and IP signals.
- Every payout needs two-person approval and is paid in crypto to a wallet verified for that trader.
- No contests with cash or material prizes.
- Didit's paid plans are optional: Growth $99/month cuts prices 4%; Scale $299/month cuts 10%.

### CC-7 Capital, costs and payout reserve

Year-1 fixed costs are $132.24 (৳16,267) a month, and the payout reserve is sized at 1.8× monthly net sales but not yet funded \[CONFIRMED\].

| Fixed monthly cost | Paid by | From | USD | BDT |
| --- | --- | --- | --- | --- |
| OVHcloud VPS-2, production (4 vCores, 8 GB) | Founder | P3 | 8.50 | 1,046 |
| OVHcloud VPS-1, staging (2 vCores, 4 GB) | Founder | Launch | 4.54 | 558 |
| Backblaze B2, under 10 GB | Founder | P3 | 0.00 | 0 |
| Twelve Data Venture, 20% startup discount, months 1–12 | Partner | P3 | 119.20 | 14,663 |
| Total, year 1 |  |  | 132.24 | 16,267 |

- From month 13 Twelve Data costs $149.00, lifting the total to $162.04 (৳19,933). Rate: $1 = ৳123.013 (16 Oct 2026). OVHcloud prices are “from” prices before tax; the European price and term are checked at checkout (C11).
- Split: ৳300,000 founder capital pays building and hosting; the partner pays Twelve Data, NOWPayments fees, Didit, its own counsel and the penetration test.
- Variable costs: ID check $0.33 per funded candidate; AML screening $0.20 per buyer (Didit's $10 credit covers about 18 traders taking both); 1% of single-currency deposits; network fees on payouts; $0.10 per 1,000 emails; $6.95 per TB-month of backups above 10 GB.
- Payout reserve: 1.8× monthly net sales; gate G4 requires it funded; new funded accounts pause while payout liability exceeds it (CC-4).
- Break-even on fixed costs alone: 4 sales a month of Lite 10K ($44), or 2 of Classic 25K ($119), or 1 of 1-Step 25K ($199); discounts, payouts, KYC and payment fees come on top.
- Not yet priced: forming the operating company, its counsel (G0), the penetration test (C9) and the reserve (partner); forming the Bangladesh software company and the domain (founder).

### CC-8 Legal status, phases and launch gates

No paid activity starts before gate G4, and the G0 legal opinion must be in hand before P3's first paid spend \[CONFIRMED\].

**Bangladesh law (verified in the project's legal audit, 16 Oct 2026)**

- Gambling Prevention Act 2026 (Act No. 98 of 2026) replaced the Public Gambling Act 1867; passed 30 Jun 2026, in force 1 Jul 2026. Operating an online gambling or betting platform carries up to 7 years and a BDT 50 million fine; all offences are cognisable, non-bailable and non-compoundable.
- Cyber Security Ordinance 2025 s.20: gambling in cyberspace carries up to 2 years, a Tk 1 crore fine, or both; creating or operating a portal, app or device, participating, assisting and advertising are all covered.
- Unresolved: whether a paid challenge counts as gambling under these laws, and whether the Act reaches the Bangladesh software company or its crypto payments (C8, counsel at G0).

**Commercial position \[CONFIRMED\]**: the operating company holds and pays for every customer-facing account; the software company runs the platform. Bangladesh and India residents are refused; there are no local payment rails and no prize contests.

[Diagram in the Claude Doc, not exported: launch roadmap · 6 phases, 4 gates (Plan v4)]

Each phase ends on its gate, not on a date: P1 engine core, P2 platform and 12-day feed trial, P3 feed qualification, P4 closed beta with no money or prizes, P5 launch, P6 expansion.

### CC-9 Open confirmations

Eleven checks remain open from Plan v4 (C1–C5 were answered by Twelve Data on 19–20 Oct 2026); four of them are your decisions.

- [ ] C6 · NOWPayments: will it onboard the operator as a prop firm, and are the 1% deposit and 0% payout fees contractual? Blocks the payment decision; needed before P3.
- [ ] C7 · Didit: post-1 Nov 2026 prices on the operator's account; sign its data-processing terms. Needed before launch.
- [ ] C8 · Counsel (G0, before P3): the vendor's software exports from Bangladesh; the operator's jurisdiction, licences, consumer and AML duties; whether the Gambling Prevention Act 2026 reaches the vendor or its crypto payments; the trader terms, including voided delayed-price trades and the 30-day dispute window; whether per-trade P&L counts as reconstructable price data.
- [ ] C9 · Penetration tester: scope and quote for an external test before launch.
- [ ] C10 · Twelve Data trial (P2): whether every streamed price carries a source timestamp; delay distribution per symbol and session.
- [ ] C11 · OVHcloud: VPS-2 stock in a European datacentre, its price there and the commitment term.
- [ ] C12 · Twelve Data: startup discount approved on the perks page after incorporation, before subscribing.
- [ ] C13 · Your decision: which company holds the Twelve Data account, the operator or the software company.
- [ ] C14 · Your decision: brand name and domain; the Twelve Data account and its discount need a company email.
- [ ] C15 · Your decision: weekend and holiday holding on funded accounts (FTMO's rule is the default).
- [ ] C16 · Your decision: keep or drop the 90% profit-split add-on (+12% of list).

## M01 Architecture and Module Map

The engine is a set of event-driven modules, each the single owner of its data; lifecycle, rule and money decisions are deterministic and replayable.

[Diagram in the Claude Doc, not exported: engine architecture · 3 layers + event log]

Three layers stack top to bottom — channels (trader portal, admin console, support desk, notifications), core services in five domains, and an integration layer (Twelve Data feed, payment rails, KYC and screening, news calendar) — with the event log beside them. Channels never write state directly: they call core services, which record every decision in the event log that drives replay and reporting. Security (M28), incidents (M30), governance (M31) and invariants (M32) cut across all layers.

**Architecture rules**

- One owner per data set; other modules read through events or APIs, never by writing to another module's store.
- Transactional outbox for event publication; consumers idempotent on event ID; ordering guaranteed per ACC and per CIF.
- Multi-step processes (purchase → provisioning; pass → KYC → contract → funded; payout) run as sagas with compensating actions.
- Sources of truth: the firm's own engine for prices applied, fills, rule outcomes and lifecycle state (there is no external trading platform); the ledger for money; vendors for evidence, the engine for decisions.
- Low-latency path (ticks and trades → rules and risk) separated from the analytical path (warehouse).
- Versioned, effective-dated configuration; every decision stores the configuration and logic versions used.
- Segregated environments; no production personal data outside production.

| Module | Owns (source of truth) | Key events emitted |
| --- | --- | --- |
| M03 CIF | Customer identity, identity graph | Customer registered, merged, status changed |
| M04 Access | Login identities, MFA state, sessions, gate decisions | Session opened; MFA changed; gate refused |
| M05 Verification | KYC cases, screening results | KYC approved, rejected; screening hit |
| M06 Payment methods | Instruments, wallet ownership evidence | Instrument verified, changed |
| M07 Catalog | Programs, RSVs | RSV published |
| M08 Billing | Orders, invoices, refunds, chargebacks | Order paid; refund issued; chargeback opened |
| M09 Engine core | Price updates applied, synthetic spread, orders, fills, positions | Price update applied; order filled; position closed; symbol frozen |
| M10 Lifecycle | ACC state | State changed |
| M11 Time | Timers, calendars, baselines | Day rolled; deadline reached |
| M12 Rules | Rule outcomes | Warning, violation, breach, target met |
| M13 News | Restriction windows | Window published; order blocked |
| M14 Event log | Append-only history; licensed price store | Stores all events |
| M15 Risk | Exposure, liability, limits | Firm limit hit; kill switch changed |
| M16, M17 Surveillance and fraud | Cases, scores | Integrity alert; fraud alert; case decided |
| M18 Funded | Contracts, splits, scaling | Contract signed; scaling approved |
| M19 Payouts | Payout requests | Payout requested, approved, paid, failed |
| M20 Compliance | AML cases, policies, restricted lists | AML alert; list updated |
| M21 Ledger | Journals, reconciliations | Journal posted; reconciliation break |
| M23 Notifications | Delivery log | Notification sent |
| M24 Support | Tickets, disputes | Ticket opened; dispute decided |
| M25 Admin | Staff actions, approvals | Action requested, approved |
| M26 Reporting | Metric catalogue, as-of snapshots, SLO alerts | Alert raised; report published |
| M27 Affiliates and CRM | Affiliates, attributions, commissions, promotion codes | Referral attributed; commission accrued, clawed back |
| M30 Incidents | Incident records, remediation actions | Incident opened, closed; extension granted |

**Edge cases**

- Out-of-order or duplicate feed updates → sequence numbers in Valkey Streams, reorder buffer, deduplication on update ids, late-event reconciliation job.
- Saga step fails mid-way → compensating action (e.g., refund once provisioning retries are exhausted).
- Clock drift between services → synchronized clocks; engine fill timestamps authoritative for trades.

## M02 Core Data Model and Identifiers

Every record hangs off a CIF, and every trading decision hangs off an ACC bound to one immutable RSV.

| Entity | Key | Belongs to | Notes |
| --- | --- | --- | --- |
| Customer | CIF | Root | One per natural person after KYC (M03) |
| Login identity | User ID (Keycloak) | CIF | Email or SSO; several allowed per CIF |
| Verification case | KYC | CIF | Tiered (M05); Didit results |
| Payment instrument | Instrument ID | CIF | Pay-in or payout; at launch USDT TRC-20 wallets (M06) |
| Order, invoice | ORD, INV | CIF | One order may create several ACCs; discounts recorded per line |
| Pay-in, refund, chargeback | PAY | ORD | Ledger-linked (M21); chargebacks only once cards are accepted |
| Program, rule-set version | PRG, RSV | Root | RSV immutable once published (M07) |
| Trading account | ACC | CIF, ORD, RSV | One stage instance in the firm's own engine; lineage pointer to parent ACC |
| Order, fill, position | Engine IDs | ACC | Each fill references the price update it used |
| Price update | UPD (feed sequence number) | Symbol | Licensed price store; retention bound by the licence (M14) |
| Calendar event, restriction window | Event ID, window ID | Symbols | Versioned (M13) |
| Payout | PO | ACC, CIF, instrument | M19 |
| Contract | Contract ID | CIF, ACC | Funded trader agreement (M18) |
| Affiliate, attribution, commission | Affiliate ID | CIF or entity | M27 |
| Promotion code | Code ID | PRG | Eligibility and limits (M27, CC-2) |
| Case | CASE | CIF and/or ACC | Integrity, fraud, AML (M16, M17, M20) |
| Ticket | TKT | CIF; optional ACC, ORD, PO | M24 |
| Incident | Incident ID | Root | Linked to affected ACCs and time extensions (M30) |
| Event | EVT | Any entity | Append-only (M14) |
| Ledger entry | Journal ID | Source record | Double-entry, whole cents (M21) |

**Rules**

- IDs are opaque, non-sequential and never reused (e.g., UUIDv7 or ULID); customer-facing references carry a check digit.
- Lineage follows the product's stage path (CC-2): Lite and Classic S1 → S2 → FND; 1-Step S1 → FND; Instant FND only. Resets and scale-ups add ACCs with parent pointers, so each stage record stays immutable (D2, D26).
- Soft deletion only; hard deletion runs only through the retention schedule or an erasure workflow (M20).
- Every decision record stores the RSV and logic version used, so it can be replayed (M14).
- Multi-entity or multi-brand operation (MAY): tenant ID on every record.

**Edge cases**

- One order buys several accounts → one ORD, several ACCs; refunds and chargebacks allocated per ACC line.
- Stage advance on the same account (D2 alternative) → stage instances remain separate records with their own baselines and timers.

## M03 Customer Information Form (CIF) and Identity Resolution

One natural person maps to exactly one permanent CIF, which links every account, verification record, payment, payout, ticket and case.

**Project configuration**: customers are global except Bangladesh and India residents (CC-1). Repeat-account discounts (10% and 15% on the 2nd and 3rd account, CC-2) make cross-account hedging cheaper, so the identity graph and M16 watch a customer's own accounts as closely as linked ones. Corporate customers are not decided \[OPEN\].

**Linked to each CIF**

- Accounts in every stage and status; orders, invoices, refunds, chargebacks.
- Pay-in and payout instruments; payouts; contracts; tax forms.
- KYC cases, documents, screening results, customer risk rating.
- Consents and terms acceptances (document version, timestamp, IP).
- Devices, IP sessions, portal logins; tickets; notifications; cases and flags; affiliate relationships.

**Customer-level aggregates (maintained continuously)**

- Fees paid, refunds, chargebacks, payouts; net revenue = fees − refunds − chargebacks − payouts.
- Active accounts by stage; total funded allocation; breach count; payout count.
- KYC tier, risk score, restriction flags.

**Processes**

1. Registration creates a provisional CIF (state Registered).
2. Pre-KYC duplicate scoring on email, phone, device fingerprint, IP, payment fingerprint, name and date of birth. A match creates a review flag, never an automatic merge.
3. At KYC (M05), deterministic matching on document number + issuing country and on name + date of birth. A match to another verified CIF blocks approval and opens a merge case.
4. Merge (four-eyes): child records re-pointed, both histories kept, alias map stored, caps re-evaluated. Split reverses an erroneous merge.
5. Closure (customer- or firm-initiated): open accounts closed per policy, pending payouts settled or held, data moved to the retention schedule (D35).

**Identity graph (links between CIFs, not merges)**

- Edges from shared device, IP or subnet within a time window, address, pay-in instrument, payout destination, document, phone, and trading correlation (M16).
- Each edge stores type, weight, first and last seen, and evidence.
- Consumers: cluster-level caps (M18, D21), hedging and copy detection (M16), fraud and AML monitoring (M17, M20).

**Status model**: Registered → Verified (tier n) → Active → Restricted (purchase, trading or payout blocked individually) → Suspended → Closed · Terminated · Banned. Banned identifiers (hashed document, device and instrument fingerprints) enter a blocklist.

**Controls**

- CIF is immutable and never reassigned.
- One CIF per verified person; duplicates blocked at KYC (I1, I15).
- Merge, split and unban require four-eyes and a reason code.
- Account, allocation and purchase caps evaluated per CIF and, where configured, per identity cluster (D21).
- Accounts and funded allocations are non-transferable between CIFs.

**Edge cases**

- Same person, two emails, funded accounts on both → merge; apply allocation cap; excess handled per D21.
- Household members on one device or IP → linked, not merged; heightened surveillance for hedging and copying.
- Name or document change → KYC update on the same CIF.
- Account takeover → freeze instrument changes and payouts, force re-authentication, restore under the same CIF.
- Erasure request during an AML retention period → erase non-mandatory data; retain mandated records on a valid basis. GDPR Art. 17(3)(b) covers only duties under EU or member-state law, so an offshore operator's own AML retention may rest on Art. 17(3)(e) (legal claims) instead; counsel to confirm.
- Death or incapacity → freeze; estate claim workflow (D36).
- Corporate customers (MAY) → entity CIF linked to the CIFs of its beneficial owners (KYB, M05); entity CIFs are a separate type, outside I1 and I15.

## M04 Registration, Authentication and Jurisdiction Gating

A CIF is created only after jurisdiction, age and bot checks pass, and every sensitive action requires multi-factor authentication.

**Processes**

1. Sign-up through Keycloak (email/password or SSO); email verification; phone OTP (configurable).
2. Capture legal name, country of residence, date of birth, and citizenship where sanctions rules need it.
3. Record acceptance of terms, privacy policy and risk disclosure: document version, timestamp, IP, device. Marketing consent captured separately.
4. Jurisdiction gate: block if declared residence, IP geolocation or phone country is on the restricted list (D32: Bangladesh and India confirmed; any other countries \[OPEN\]). Card BIN country is checked at purchase once cards are accepted (M08).
5. Age gate: block below the age of majority of the residence country.
6. Sanctions/PEP screen at first purchase, not at registration (M05 Tier 1, Didit AML screening) \[CONFIRMED\].
7. MFA enrolment; mandatory before payout-instrument changes and payouts.

**Controls**

- Bot defence, rate limits, disposable-email and VPN/proxy/hosting-network detection; the risk score drives step-up or denial.
- Session controls: device binding, concurrent-session limits, idle timeout, re-authentication for sensitive actions.
- Breached-password screening; lockout with identity-verified recovery.
- Every gate decision logged with the restricted-list version applied.

**Edge cases**

- Declared country differs from IP country → risk flag; proof of address required at KYC.
- Customer moves to a restricted country mid-lifecycle → wind-down protocol (D32).
- Lost MFA device → recovery by selfie re-match against the KYC record.
- One email shared by two people → refused; one login identity maps to one CIF.

## M05 Verification: KYC, Screening and Other Checks

Verification is tiered so each tier unlocks one capability; no funded contract or payout proceeds without a valid Tier 2 and a clean screen at that moment.

**Project configuration**: T1 is Didit AML screening at first purchase ($0.20; daily rescreening $0.07 per person a year); T2 is Didit's full check ($0.33) before any funded account is issued \[CONFIRMED\]. Residence is judged from the document country plus device and IP signals. Proof of address is not part of the chosen check, and tax forms depend on the operator's jurisdiction \[OPEN, C8\].

| Tier | Trigger (D28) | Checks | Unlocks |
| --- | --- | --- | --- |
| T0 | Registration | Email/phone verification, age declaration, jurisdiction gate, device/IP risk | Portal access |
| T1 | First purchase | Didit AML screening (sanctions, PEP, adverse media) with daily rescreening; payment risk score | Buying evaluations |
| T2 | Before any funded account is issued: Lite and Classic at S2 pass, 1-Step at S1 pass, Instant after payment | Didit full check: government ID authenticity, passive liveness and face match, device and IP; proof of address within a recency limit; full sanctions/PEP/adverse-media screen; age; residence; duplicate-identity check; tax form; payout-instrument ownership | Funded contract, payouts |
| T3 (EDD) | PEP, high-risk country, fees or payouts above thresholds, adverse media, alerts | Source of funds/wealth, extra documents, senior approval | Payouts above thresholds |

**Ongoing verification**

- Delta re-screening of all CIFs on every sanctions-list update; full re-screen periodically.
- Re-screen of CIF and payout destination at every payout execution (M19).
- Document expiry tracked; an expired document holds the next payout until re-verified.
- Re-KYC triggers: residence change, name change, instrument name mismatch, risk alert.

**Other required checks**

- Payment-instrument ownership (M06).
- Contract signatory = the T2-verified person (M18).
- Tax residency and forms per entity jurisdiction (e.g., IRS W-9/W-8BEN where the payer has US reporting duties) (M20).
- Affiliates: KYC or KYB before any commission (M27).
- Corporate customers (MAY): registry extract, directors, beneficial owners.

**Case management**

- States: Not started → Submitted → Auto-approved · Manual review → Approved · Rejected (reason code) · Resubmission requested → Expired.
- Attempt limits, SLA timers, reviewer ID and rationale stored.
- Four-eyes to approve EDD or to override a vendor rejection.
- Evidence retained: images, vendor reports, decisions (D35).

**Controls**

- KYC data encrypted, need-to-know access, masked in support views (M28).
- Secondary vendor or manual review path during a vendor outage.
- Sanctions hits escalate to the compliance officer without tipping off the customer (M20).

**Edge cases**

- Transliterated or reordered names → fuzzy-match thresholds plus manual review.
- Sanctions false positive → documented discount with reviewer rationale.
- True sanctions match after funding → freeze all ACCs and payouts; report per jurisdiction.
- KYC fails after the last evaluation stage (restricted country, minor, forged document) → funded account refused; refund per D28 \[OPEN\]. A Bangladesh or India resident who reaches funding is caught here at the latest, because the document country is read only at T2.
- Same document submitted under a second CIF → fraud case (M17).
- Document expires between T2 and a payout → payout held until re-verified.

## M06 Payment Methods Management

Every instrument belongs to one CIF, is verified before use, and changes only under step-up authentication with a cooling-off hold before any payout.

**Project configuration (launch)**: pay-in is USDT on TRC-20 through NOWPayments (1% on single-currency deposits; onboarding pending C6) or Bitcoin through self-hosted BTCPay Server (optional); no cards and no local rails. Payouts go only in USDT on TRC-20 to a wallet verified for that trader; no bank wire \[CONFIRMED\]. The wallet-verification method and who bears network fees are \[OPEN\] (D31). Card rows below apply only from expansion.

| Direction | Types (D31) | Verification | Engine stores |
| --- | --- | --- | --- |
| Pay-in | Cards; bank transfer/open banking; e-wallets; local methods; crypto | 3-D Secure where applicable; BIN country and card type; name match where available | PSP token, BIN, last 4, expiry, fingerprint; never full card number or security code |
| Payout | Bank account (IBAN/SWIFT/local scheme); e-wallet; payout platform; crypto wallet | Account-holder name match, open banking or micro-deposit; wallet signed message or test transfer; chain and address format | Masked details, ownership evidence, screening result |

**Lifecycle**: Added → Pending verification → Verified → Active (one default per direction) → Suspended → Removed (soft-deleted, retained for audit).

**Processes (add or change)**

1. Step-up authentication (MFA).
2. Format validation: IBAN checksum, BIC, wallet address checksum and chain.
3. Screening: sanctions on the account holder; blockchain-analytics risk on wallets.
4. Ownership verification against the T2 identity (M05).
5. Cooling-off hold before the first payout to a new or changed instrument; notice to registered email and phone.
6. Activation event logged (M14).

**Controls**

- Payouts only to verified instruments owned by the CIF; no third-party payouts.
- Instrument fingerprints unique across CIFs; a shared instrument creates a graph link and a case (M03, M17).
- Refunds return to the original pay-in instrument where possible.
- Limits: instruments per CIF, changes per period, per-method minimums and maximums, country availability.
- Each PO stores a snapshot of its instrument; later edits never alter a pending PO.

**Edge cases**

- Card replaced or expired → network token update; card payouts only where the rail supports push-to-card.
- Crypto: wrong network, wrong token, underpayment, overpayment, payment after invoice expiry, payment from an exchange omnibus address (no reliable refund address) → refunds go only to a wallet verified for the customer.
- Bank payout returned (closed account, name mismatch) → reversal entry, instrument suspended, customer notified.
- Fee paid with a third party's card → rejected where the cardholder name is available; otherwise ORD flagged (chargeback risk).
- Instrument removed while a PO is pending → PO held until the customer confirms an active verified instrument.

## M07 Program Catalog and Rule-Set Versioning

Each account is bound at purchase to an immutable rule-set version (RSV), and the customer-facing rulebook is generated from the same RSV the engine enforces.

**Project configuration**: the four products and the price list are in CC-2, rules and leverage in CC-3, scaling in CC-4. Swing, the 10%-loss 2-Step and prize contests are not offered \[CONFIRMED\].

**Program definition**

- Identity: PRG ID, name, asset classes, account currency, sizes, price per size, availability (countries, dates), purchase limits per CIF.
- Stage list, ordered per product (CC-2): Lite and Classic S1 → S2 → FND; 1-Step S1 → FND; Instant FND only; each stage has its own rule set (F-2).
- Commercial: reset price (D27), add-ons (price + rule delta), size upgrades (MAY), allowed discounts, refund terms (D29).
- Account options (MAY): a swap-free variant, once swaps are defined at all (D41 \[OPEN\]).
- Program types: the four confirmed products with one to three stages; free trial (MAY: non-progressing, no payout, no prize); contests with cash or material prizes are prohibited \[CONFIRMED\].

| Parameter group | Per-stage parameters (all versioned in the RSV) |
| --- | --- |
| Targets | Profit target and basis; positions-closed requirement (D10) |
| Loss limits | Daily loss amount, base and reset instant (D7, D8); max loss type and lock level (D9) |
| Time | Minimum trading days and day definition (D6); max duration (D4); inactivity (D5); clock start (D3) |
| Exposure | Leverage per asset class; permitted symbols; trading-session restrictions; max lots per order, per symbol and in total; max open positions and pending orders; margin-use cap |
| Behaviour | News rule (D15); weekend holding (D16); EA policy (D17); copy policy (D18); consistency (D19); min hold time, order rate, max risk per trade (D20); stop-loss requirement |
| Funded only | Split tiers, payout frequency, first-payout delay, min/max payout, fee refund (D22); scaling (D26); allocation-cap weight (D21) |

**Versioning rules**

- An RSV is immutable after publication; a correction publishes a new RSV.
- An account binds to the RSV current at purchase; an add-on creates a derived RSV.
- Active accounts change RSV only where terms permit, with notice and logged acceptance.
- Rulebook text, portal metrics and engine checks read the same RSV parameters (I14).

**Publish gate**

- Daily loss amount < max loss amount; target > 0; minimum trading days ≤ max duration when finite.
- Leverage within CC-3; every listed symbol is licensed and streaming (CC-5).
- Trailing lock level consistent with the floor formula (M12).
- Economics simulation attached: expected pass rates and payout cost (M21); every Instant price, after any discount, must cover a no-skill trader's maximum withdrawal \[CONFIRMED rule\].
- Maker-checker approval with a diff against the prior RSV (M25, M31).

**Edge cases**

- Price change while carts are open → quoted price honoured until quote expiry.
- Add-on bought after the stage started → derived RSV effective from purchase time, recorded on the ACC timeline.
- Program retired with active accounts → those accounts continue under their RSV.
- Contract specification changes mid-stage (contract size, leverage) → logged as an engine configuration change, customer notified; RSV unchanged.

## M08 Checkout, Billing, Refunds and Chargebacks

An account is provisioned only after funds are confirmed, and every order, refund and chargeback posts to the ledger with its external reference.

**Project configuration (launch)**: invoices in USD, paid in USDT on TRC-20 through NOWPayments or in Bitcoin through BTCPay; no card checkout until a prop-specialist processor is added at expansion \[CONFIRMED\]. Crypto pay-ins cannot be charged back, so the chargeback flow below stays dormant until cards exist. Discounts follow CC-2: a code of up to 10% on a first Lite, Classic or 1-Step account; 10% and 15% off the 2nd and 3rd account (Instant 10% and 10%); never combined; no affiliate commission on repeat-account sales. The refund policy is \[OPEN\] (D29).

**Order states**: Created → Pending payment → Paid → Fulfilling → Fulfilled · Failed · Cancelled · Refunded (partial or full) · Charged back.

**Checkout**

1. Cart: program, size, add-ons, currency, discount code, affiliate attribution.
2. Price: base + add-ons − discounts (never combined, CC-2) + indirect tax by customer-location evidence, with FX conversion.
3. Pre-authorization checks: jurisdiction, T1 screen, fraud score, velocity per CIF, device and instrument, CIF purchase caps.
4. Payment: at launch, USDT on TRC-20 (or Bitcoin) accepted after the confirmation threshold; from expansion, card authorization with 3-D Secure where applicable, then capture.
5. Webhook verified (signature, amount, currency) and processed idempotently.
6. Invoice numbered per the entity's tax rules; receipt in portal and by email.
7. "Order paid" event triggers provisioning (M09, M10).

**Refunds**

- Eligibility engine: statutory withdrawal rights by jurisdiction, policy windows, no-trade condition, goodwill within the authority matrix (D29).
- Refund to the original instrument (crypto: a wallet verified for the customer); ACC deactivated; ledger posting; affiliate commission clawed back (M27).
- The 1-Step fee refund, paid with the 3rd payout, is handled in M18, not here.

**Chargebacks**

1. PSP dispute webhook → linked to ORD, ACC and CIF.
2. Restriction applied at the scope set by D30.
3. Evidence pack built automatically: terms acceptance log, IP and device, KYC status, account-ready notice, portal logins, trading activity.
4. Representment before the network deadline; outcome posted to the ledger.
5. Chargeback after a payout → offset or recovery per terms; CIF restricted.

**Recurring billing (MAY, subscription programs)**: billing cycles, dunning and retries, cancellation, proration, billing stop on pass or fail.

**Controls**

- Idempotency key per checkout and per webhook.
- Daily PSP settlement reconciliation (M21).
- Four-eyes on refunds above threshold and on manual order fulfilment (M25).

**Edge cases**

- Double charge → duplicate refunded automatically after reconciliation.
- Payment captured, webhook lost → reconciliation job fulfils the order.
- Payment confirmed, provisioning fails → retries, then refund within a fixed SLA; never a duplicate ACC.
- Partial crypto payment, or payment after quote expiry → top-up request or refund; no fulfilment on partial funds.
- FX moves between quote and settlement → quoted price honoured within quote validity.
- Tax-location evidence conflicts (IP vs billing country) → documented tie-break rule; evidence used is stored.

## M09 Trading Engine Core and Account Provisioning

The firm's own engine — not MT5 and not a script — alone decides prices, fills, equity and breaches from one licensed feed; every other component displays or records its output (CC-1, CC-5).

**Price path \[CONFIRMED\]**

- Twelve Data → feed connector (licence flags enforced at start-up; delay, gap and quality checks) → Valkey Streams (every update sequence-numbered, so gaps are detected) → engine (spread, fills, equity, rules) → PostgreSQL ledger and event journal → FastAPI → portal and back office.
- The firm's own synthetic generator feeds development and every engine test; it never prices paid accounts.
- One connector per source behind a single interface: switching vendor is a configuration change, and a source prices paid accounts only after passing P3.

**Pricing and fills**

- Synthetic bid and ask around the single feed price; delay and gap guards freeze a symbol; a feed outage stops new orders (CC-5). \[DEFAULT spread and guards; CONFIRMED outage rule\]
- Market orders fill at the bid or ask of the latest update when the server receives them; pending orders trigger when the bid (sells) or ask (buys) crosses the level; each fill stores its update id and source, receive and fill timestamps. \[CONFIRMED\]
- Order types, no partial fills, no slippage model in v1, lot caps per order and per open position (CC-3). \[DEFAULT\]
- Trailing orders run in the engine, not in the trader's browser, so they keep working while the trader is offline. With distance d and step s in points: a buy position's stop is the highest bid since it was set minus d, and closes the position when the bid reaches it; a sell position's stop is the lowest ask plus d, triggered by the ask. A trailing buy fills when the ask reaches the lowest ask since placement plus d; a trailing sell fills when the bid reaches the highest bid since placement minus d. A level moves only when the new value beats it by at least s. A fixed stop-loss can sit beside a trailing stop; whichever is reached first closes the position. Lot caps and margin are checked at the trigger, and a rejection is recorded. Every level move is an engine event carrying the price update id that caused it, so replay reproduces it; d, s and every level are integer points, so no rounding is involved. \[CONFIRMED order kinds, d and s set by the trader; DEFAULT the rest\] (CC-3, D43)

**Accounts**

- Account currency USD; amounts in whole cents; the double-entry, append-only ledger is the record and balances are a cache rebuilt from it. \[CONFIRMED\]
- Leverage per asset class \[CONFIRMED\]; contract sizes \[DEFAULT\]; swaps and commissions \[OPEN, D41\].
- Traders log in through the portal with Keycloak; there are no separate platform credentials.

**Provisioning**

1. Receive “Order paid”, “Stage advanced”, “Reset purchased” or “Scaling approved”.
2. Create the ACC in the engine, idempotent on the triggering event id; post the initial balance as a ledger entry.
3. Bind the RSV; set leverage, symbol permissions and lot caps.
4. Initialize rule state: start-of-day baseline, high-water mark = IB, timers (M11, M12).
5. Emit “Account active”. An Instant ACC stays inactive until T2 KYC and the contract clear (M10).

**Deprovisioning**: on breach, expiry, void or closure, cancel pending orders, close positions at the breach price \[DEFAULT\], lock the account and keep records under the licence and retention rules (M14). ACC ids are never reused.

**Controls**

- Only the engine writes the ledger (I10); acceptance tests 1–7 gate every release (M31).
- Licence flags per source (commercial pricing, display, storage, retention after cancellation, redistribution), enforced at start-up.
- “Data provided by Twelve Data” beside every chart and price table.

**Edge cases**

- Crash and restart mid-stream → zero lost updates by sequence check; results equal an uninterrupted run (acceptance test 4).
- Update older than the delay threshold, or overdue → the symbol freezes; stops and limits are held (test 5).
- Daily and max limits breached on one update → one breach event recording both reasons (test 3).
- Gap past a limit → close at the actual price; the extra loss is recorded, not capped \[DEFAULT\].
- Vendor change → run both feeds side by side; cancel Twelve Data only once the 30-day purge is ready (M14).
- &#91;LIVE\] A-book routing at expansion needs a broker or LP bridge and a routing flag per account (D39).

## M10 Account Lifecycle State Machine (Stage 1 → Stage 2 → Funded)

Each account holds exactly one state at a time; every transition is guarded, event-sourced and attributable, and failed outcomes are never edited in place.

[Diagram in the Claude Doc, not exported: Lite and Classic path · 9 states; terminal states grouped, full list in the table]

Lite and Classic follow the path drawn here. 1-Step goes from Stage 1 review straight to KYC + contract; Instant goes from provisioning to KYC + contract and trades only once both clear. The diagram groups six terminal states into two outcomes and omits SUPERSEDED and CLOSED or TERMINATED; the table below is complete. Integrity voids can also end an active stage. Advancing, resetting or scaling creates a new ACC and marks the old one SUPERSEDED (D2).

| From | To | Trigger | Guards | Actions |
| --- | --- | --- | --- | --- |
| PENDING\_PAYMENT | PROVISIONING | Order paid (M08) | Funds confirmed on chain; caps respected | Start provisioning saga |
| PROVISIONING | S1\_ACTIVE (Lite, Classic, 1-Step) | ACC created in the engine (M09) | Initial balance posted to the ledger | Portal access, timers, rule state |
| PROVISIONING | KYC\_CONTRACT (Instant) | ACC created, inactive | Initial balance posted | Tier 2 KYC request; contract generated |
| S1\_ACTIVE | S1\_REVIEW | Target met (M12) | 4 minimum trading days; consistency (1-Step); positions closed if D10 requires | Read-only; metrics snapshot; M16 scan; replay audit |
| S1\_REVIEW | S2\_ACTIVE, new ACC (Lite, Classic) | Review cleared | No open case | Provision S2; S1 ACC SUPERSEDED |
| S1\_REVIEW | KYC\_CONTRACT (1-Step) | Review cleared | No open case | Tier 2 KYC request; deadlines (M11) |
| S2\_ACTIVE | S2\_REVIEW | Target met | As S1 | As S1 |
| S2\_REVIEW | KYC\_CONTRACT (Lite, Classic) | Review cleared | No open case | Tier 2 KYC request; deadlines (M11) |
| KYC\_CONTRACT | FUNDED\_ACTIVE (new ACC; Instant activates its ACC) | T2 KYC approved and contract signed | Signer = verified person; not a Bangladesh or India resident; activation fee paid if one exists; caps (D21) | Provision or activate FND; payout timers |
| FUNDED\_ACTIVE | PAYOUT\_PENDING | Payout requested (CC-4 schedule) | Eligibility (M18) | Trading per D23; checks (M19) |
| PAYOUT\_PENDING | FUNDED\_ACTIVE | Paid or denied | Ledger and engine balance consistent | Balance reduced to B; baseline B unchanged; notice |
| FUNDED\_ACTIVE | SUPERSEDED, scaled ACC (not Instant) | Scaling approved (CC-4) | $400,000 per-trader cap | New ACC or in-place change (D26) |
| Any active state | BREACHED | Hard breach (M12) | Delay and gap guards passed (D13) | Close positions at the breach price, lock, evidence, notice, retake offer (D27) |
| Any active state | EXPIRED | Inactivity limit (D5); no stage time limit | Stage not already passed | As breach, reason code “expiry” |
| Any review or active state | VOIDED | Integrity decision (M16), including delayed-price exploitation | Second reviewer | Lock; profits forfeited per terms |
| Pre-funding states | CANCELLED | Unpaid order, provisioning failure, KYC rejection or lapse | Refund rule (D28, D29) | Refund to a verified wallet, or close; notice |
| Any non-terminal state | CLOSED or TERMINATED | Customer or firm request | Pending payouts settled or held | Archive (M09) |

**Rules**

- Transitions are atomic and record actor (system or staff), reason code, RSV and evidence pointer (I3).
- Pass and breach in the same evaluation instant → precedence per D11, stated in the terms.
- Manual overrides (reinstate, manual pass, extension) need a reason code and four-eyes; a reinstatement adds a new event and keeps the breach record (I4).
- Concurrent purchases create independent lifecycles; caps aggregate at funding.

**Edge cases**

- Target reached on floating profit, then reversed before close → no pass under a closed-balance basis (D10).
- Target met before the 4 minimum trading days → stage continues; loss rules still apply.
- Pass occurs at a weekend or holiday → review SLA clock per M11.
- Gap at market open breaches with no trader action → breach stands after price validation (D13).
- Advancement requested with positions open → blocked until closed where D10 requires.
- KYC never completed → funded offer lapses at the deadline (D28).

## M11 Time Management and Market Calendar

Every time rule derives from one definition: UTC storage, the engine's trading-day zone (America/New\_York), and a configured daily reset instant (D7).

**Project configuration**: reset at 17:00 America/New\_York; 4 minimum trading days per evaluation phase; no stage time limit; first payout 14 days after funding, then the 15th and the last day of each month; scaling review every 4 months \[CONFIRMED\]. The stage clock start, inactivity limit, trading-day definition, and KYC and contract deadlines are \[OPEN\] (D3, D5, D6, D28).

**Time model**

- Storage in UTC; conversions by IANA time-zone rules; traders see local time and server time side by side.
- Server day = the interval between consecutive daily reset instants. Here the day runs from 17:00 America/New\_York to the next 17:00 \[CONFIRMED\]; in UTC the reset falls at 21:00 during US daylight time and 22:00 in winter.
- DST: 23- and 25-hour days handled explicitly. US and EU clocks change on different dates, shifting European against US references by one hour; 2026 windows: 8–29 March and 25 October–1 November. The New York reset is unaffected, but ECB and Bank of England windows shift by one hour against New York time in those weeks.
- Market calendar per symbol: sessions, breaks, holidays, early closes, rollover times; FX weekend closure; 7-day crypto trading; roll dates for futures-based CFDs.

| Timer | Starts | Fires when | Action |
| --- | --- | --- | --- |
| Stage clock | Purchase or first trade (D3) | Stage ends | Anchors duration and day counts |
| Max duration | Stage start | Deadline reached (D4) | Expire unless passed; warnings beforehand |
| Inactivity | Last qualifying trade | N days elapse (D5) | Warn, then expire |
| Minimum trading days | Stage start | Count reaches N (D6) | Pass eligibility |
| Daily reset | Each reset instant (D7) | Every server day | Snapshot balance, equity, margin as the daily-loss base (D8) |
| Weekend/holiday cutoff | Before market close | Cutoff reached (D16) | Warn, auto-close or record violation |
| Review SLAs | Stage pass, KYC submission, payout request | SLA elapses | Escalate |
| KYC and contract deadlines | Last evaluation pass (Instant: payment) | Deadline elapses | Funded offer lapses (D28) |
| Payout cycle | Funded start or first funded trade | Eligibility date (D22) | Payout window opens |
| Scaling review | Funded start | Review date (D26) | Scaling evaluation |
| Activation window (MAY) | Purchase | No login or trade within X days | Remind or expire |

**Resets**

- Daily reset: automated baseline snapshot; trading-day counter, inactivity and duration checks run.
- Stage reset product (D27): new ACC; every stage timer restarts.
- Cycle resets: payout-cycle and consistency windows restart after each payout (D19, D22); scaling cycle per D26.

**Extensions and pauses**

- Admin extension: reason code, four-eyes, logged.
- Automatic extension equal to a firm-caused outage, linked to the incident record (M30).

**Scheduler requirements**

- Durable and distributed; exactly-once per (ACC, timer, period) key.
- Missed runs after downtime execute with their original effective timestamps.
- A late daily snapshot is rebuilt from trade and price history at the reset instant, never from current state.
- Synchronized clocks on all services; engine fill timestamps, with the feed's source timestamps, are authoritative for trading events.

**Edge cases**

- Account created minutes before a reset → whether the short first day counts as a trading day is fixed by D6.
- Positions open across a reset → floating P&L enters the next day's base under equity-based rules (D8).
- Trade opened on day D and closed on D+1 → day attribution by execution timestamp per D6.
- Partial holiday sessions; crypto weekend activity → counted toward trading days only if D6 says so.
- Scheduler outage spanning a reset → baselines backfilled from history; rule evaluations re-run.
- Leap day, year-end, a feed vendor changing its timestamp convention.

## M12 Trading Rules Engine

Every account is evaluated continuously against its RSV; each outcome is classed as pass, hard breach, soft violation, warning or pre-trade block, with its inputs stored for replay.

**Valuation standard**

- Equity = balance + unrealised P&L, in USD through feed cross rates; swaps and commissions enter only once defined (D41).
- Longs valued at the synthetic bid, shorts at the synthetic ask (CC-5); cross-currency P&L converted at the current conversion rate.
- Evaluated on every price update for open positions and on every trade or balance event. Snapshot polling alone misses intra-interval extremes and is insufficient.
- At launch there is one feed, and the trader terms make platform prices the only reference: a breach stands on the first price at or below a limit once the delay and gap guards pass. From growth, a second vendor cross-checks prices and divergence halts new orders (D13).

**Formulas** (IB initial balance; T, D, M, C, Λ from the RSV)

```latex
\text{Target met:}\quad X_t \ \ge\ IB\,(1+T),\qquad X_t \in \{\,B^{\text{closed}}_t,\ E_t\,\}\ \text{per D10}
```

```latex
\text{Daily breach:}\quad E_t \ \le\ \text{Base}_d - L_d,\qquad \text{Base}_d \in \{\,B^{\text{SOD}}_d,\ E^{\text{SOD}}_d,\ \max(B^{\text{SOD}}_d, E^{\text{SOD}}_d)\,\},\qquad L_d \in \{\,D\cdot IB,\ D\cdot \text{Base}_d\,\}
```

```latex
\text{Static max-loss breach:}\quad E_t \ \le\ IB\,(1-M)
```

```latex
\text{Trailing max-loss breach:}\quad E_t \ \le\ F_t,\qquad F_t=\min\big(HWM_t - M\cdot IB,\ \Lambda\big),\qquad HWM_t=\max_{s\le t} X_s
```

Under the trailing rule, X is intraday equity, or end-of-day balance or equity for the EOD variant; Λ is the lock level (D9). The floor rises with the high-water mark until it reaches Λ, then stays fixed.

Project values \[CONFIRMED\]: Base\_d = the higher of balance and equity at 17:00 New York; L\_d = D × IB with D = 3% (Classic 4%); static max loss for Lite (6%), Classic (8%) and 1-Step (6%); Instant trails 5% on intraday equity and locks at Λ = IB.

```latex
\text{Withdrawal of gross amount } G \text{ at time } \tau \text{ on day } d:\quad \text{Base}_d \leftarrow \text{Base}_d - G\quad \text{for } t \ge \tau
```

A payout is a balance deduction, not a trading loss, so the daily base drops with it. The trailing floor needs a separate decision (D42, \[OPEN\]): cap the withdrawal so equity stays a set buffer above F\_t, or restart the trailing floor from the post-payout balance.

```latex
\text{Profit concentration:}\quad \frac{\max_d\,(\pi_d - v_d)}{\sum_d\,(\pi_d - v_d)} \ \le\ C,\qquad \text{evaluated only when } \sum_d\,(\pi_d - v_d) > 0
```

π\_d is the net closed profit of server day d and v\_d the profit removed for violations that day, within the evaluation window (D19). Project: C = 50% for 1-Step and Instant \[CONFIRMED\]; the window (stage, payout period) and how a position closed in parts across days is attributed are \[OPEN\].

| Rule | Default class (configurable) | Engine action |
| --- | --- | --- |
| Daily loss, max loss | Hard breach | Disable trading, cancel pending orders, close-out per D14, state BREACHED |
| Target + minimum days + other pass conditions | Pass | Read-only; review (M10) |
| Max duration, inactivity | Expiry | State EXPIRED |
| Lot, position, exposure limits | Pre-trade block in the engine | Reject the order or record the violation |
| Symbol and leverage permissions | Engine configuration | Enforced per RSV in the engine (M09) |
| News window (M13) | Pre-trade block in the engine; stops and targets still execute \[DEFAULT\] | Per D15 |
| Weekend/holiday holding | Auto-close or violation (D16) | Per D16 |
| Min hold time, order rate (D20) | Soft violation; repeats open an M16 case | Profit removal |
| Consistency (D19) | Gate on pass and payout | Pass or payout deferred until satisfied |
| Stop-loss required, max risk per trade | Soft violation or breach, as configured | Record; escalate repeats |

**Action pipeline**: detect → validate data and reference price → classify → act → notify → store evidence bundle (prices, ticks, positions, formula and RSV versions).

**Proximity alerts**: configurable thresholds of daily and max loss consumed → trader notification and risk-desk view.

**Precision**: decimal arithmetic; per-currency rounding rules; one code path shared by the real-time engine and the replay engine (M14, I11).

**Edge cases**

- Bad price causing a breach → at launch there is no second feed: delay and gap guards, then incident review and replay (M30); from growth, second-feed divergence halts new orders (D13).
- Spread widening on an equity basis → the synthetic spread widens only with volatility and inside news windows (CC-5). Because it is symmetric, the mid equals the feed price, so excluding spread from breach equity (D12) would mean valuing at the raw feed price.
- Swap or commission debit crossing the floor → arises only once swaps or commissions are defined (D41).
- Reset-instant breach → a position carried across 17:00 New York with a floating loss beyond the daily limit breaches at the reset itself, because the new base is the higher of balance and equity; the portal warns beforehand (M22).
- Payout deduction during the day → the daily base drops by the withdrawn amount, so a withdrawal is never read as a trading loss.
- Instant payout after the trailing floor has locked at the starting balance → withdrawing all profit leaves equity on the floor and would breach; the payout is capped or the floor restarts (D42) \[OPEN\].
- Weekend gap through the floor → breach at the first valid price; trader liability is capped at fees paid (stated in terms); \[LIVE\] the firm bears the real loss.
- Pending orders triggered after a breach → cancelled at breach; any fill after the breach timestamp is excluded.
- Partial closes; opposite positions within one account → net or gross exposure for limits defined in the RSV.
- One winner closed in parts across several days to pass the consistency rule → attribute profit per position or per day (D19) \[OPEN\].
- Oil quotes from a feed that rolls futures contracts → tagged adjustment, treated per D12 \[OPEN\].
- Conversion-rate spikes on non-USD quotes → the guards apply to conversion rates too.
- Equity disagreement between components → the engine is the only source of truth (D38).
- Target reached on floating profit, then reversed → no pass under a closed-balance basis (D10).

## M13 News Trading Restriction Service

Restriction windows are generated from a versioned economic calendar, and the windows shown to traders are the same records the engine enforces.

**Project configuration \[CONFIRMED\]**: the firm's own list of restricted events comes from official schedules (BLS, Federal Reserve, ECB, Bank of England, Bank of Japan, EIA; CC-5). The window runs from 2 minutes before to 2 minutes after each event, in every phase; no trade is opened or closed on an affected symbol; crypto is exempt; the list is updated monthly and published to traders. Because the firm runs its own engine, orders inside a window are rejected before they execute, so the consequence choice in D15 applies only to stops and targets, which still execute \[DEFAULT\].

**Data**

- Calendar event: ID, title, country/currency, impact level, scheduled time (UTC), status (scheduled, revised, released, cancelled), revision history.
- Instrument map: currency → affected symbols (pairs containing it, plus mapped indices, metals, energy and crypto), owned by risk and versioned.
- Restriction config per RSV and stage (D15): impact levels, window \[t − a, t + b\], actions covered (open, close, SL/TP trigger, pending fill), consequence, holding through news allowed or not.

**Processes**

1. Build the list monthly from the official schedules (BLS dates from its calendar feed) and recheck each date before the event; no paid calendar API is used, because display would need a separate licence.
2. Version every change; alert risk when an event time changes within 24 hours of the event.
3. Publish windows to the portal calendar (M22), pre-trade blocking in the engine (M09), and the post-trade detector.
4. Detect: each fill on an affected symbol timestamped inside a window → violation record (deal, event, window, P&L).
5. Apply the consequence: the violating fill's profit is excluded from target and payout calculations and its loss stands (positive-only removal); a violation is not a breach (D15) \[DEFAULT\].
6. Unscheduled events (e.g., emergency central-bank action): risk desk creates a manual window with a reason; application per D37. The Bank of Japan, which has no fixed time, works the same way: its window opens when the risk desk marks the statement published and runs 2 minutes \[DEFAULT\].

**Controls**

- Trader-visible windows = enforced windows (same records, same versions).
- Every violation stores the calendar version and instrument-map version used.
- Detection back-fills when calendar data arrives late.

**Edge cases**

- Event rescheduled after a position was opened → judged against the window in force at fill time; both versions recorded.
- Stop-loss or take-profit fill inside a window → the fill stands and is a violation: its profit is excluded and its loss stands (D15) \[DEFAULT\].
- Trailing stop triggered inside a window → treated like a stop-loss: the fill stands, its profit is excluded and its loss stands. A trailing entry is held like any pending entry and checked on the first price after the window. Level moves continue inside a window, and placing, changing or cancelling a trailing order is allowed \[DEFAULT\].
- Breach close-out fills inside a window → excluded from news violations.
- Overlapping events → windows merged per symbol.
- Event at the daily reset boundary → violation attributed by fill timestamp.
- DST transitions → event times stored in UTC; only display converts.

## M14 Per-Account Logging, Audit Trail and Tracking

Every trading account has an append-only, tamper-evident event history from which any rule outcome, metric or balance can be reproduced.

| Stream | Contents | Key fields |
| --- | --- | --- |
| L1 Lifecycle | Every state transition | From/to state, trigger, actor, reason code, RSV, evidence pointer |
| L2 Trading | Orders (placed, modified, cancelled, expired, rejected); fills; positions; SL/TP changes; trailing settings and level moves; balance operations | Engine order, fill and position IDs; requested vs filled price; slippage; volume; ms timestamps; commission; swap; order source (web or mobile portal; EAs not allowed at launch); price update id used |
| L3 Risk state | Start-of-day baselines; high-water mark; floors; per-interval equity min/max; margin level; distance to each limit | Interval, values, price source |
| L4 Rule evaluations | Passes, warnings, violations, breaches | Inputs (prices, ticks), formula and RSV versions, validation result |
| L5 Access | Portal sessions (Keycloak); credential resets; MFA events | IP, network (ASN), geolocation, device/terminal ID, client type |
| L6 Commercial | Order, payment, refund, chargeback and payout links | ORD, PAY, PO references |
| L7 Staff actions | Every admin or support action on the account | Staff ID, approval reference, justification |
| L8 Notifications | Messages sent | Type, channel, template version, delivery status |
| L9 Data quality | Gaps, late arrivals, corrections, reconciliation results | Sequence numbers, affected ranges |

**Record standard**: event ID; ACC; CIF; type; occurred\_at (source time); recorded\_at (ingest time); source system; actor; versioned payload schema; correlation ID; hash of the previous record (hash chain per stream).

**Derived tracking (per ACC, per day and cumulative)**

- Balance and equity curves; daily P\&L; high-water mark; max intraday drawdown; share of each limit consumed.
- Trading days counted; time remaining; progress to target.
- Trade count, win rate, profit factor, mean and median hold time, lot-size statistics, symbol and session mix.
- Share of trades in news windows; share below minimum hold time; automation signals; best-day share; correlation score against other accounts (M16).
- One derived store feeds the trader portal, the admin timeline and reports, so definitions cannot diverge (I14).

**Replay engine**: recomputes any ACC's rule state at any timestamp from L2 + price history + RSV + logic version. Used for disputes (M24), payout audits (M19), rule-change impact tests (M31) and incident remediation (M30). Replay needs the licensed prices: after a vendor change and purge, past decisions rest on stored outcomes and fill records, not recomputation (I11); whether per-trade P&L may be kept is counsel question C8.

**Retention (D35)**: prices follow the Twelve Data licence. They are stored while the subscription runs; within 30 days of cancelling, raw prices, including fill prices reconstructable from trade records, are deleted, and backups containing prices are kept 30 days at most \[CONFIRMED\]. Order ids, timestamps, balances and payouts stay for the AML period: at least five years (FATF R.11 minimum: transaction records after the transaction, due-diligence records after the relationship ends), longer where local law requires. Disputes must be raised within 30 days of the trade, and crypto pay-ins carry no chargeback window at launch.

**Controls**

- No update or delete API on log stores; corrections are new linked events (I12).
- Per-ACC sequence-gap detection; completeness checks against the event journal.
- Access to logs is itself logged; personal data minimized in log payloads.
- Test and internal accounts flagged, excluded from metrics, and blocked from payouts (D40).

**Edge cases**

- Staff correction of a historical fill → correction event linked to the original; replay re-run; a changed outcome needs approval.
- Missing tick data for a period → replay-uncertainty flag on affected decisions.
- High-frequency accounts → equity aggregated per interval, always keeping interval min and max so extremes are never lost.

## M15 Real-Time Risk Engine and Firm-Level Exposure

In the simulated model the firm's market-driven risk is payout liability, which behaves like a short call on each funded trader's P\&L; it must be measured, limited and stress-tested in real time.

**Project configuration \[CONFIRMED\]**: every account is simulated (B-book) at launch, and A-book routing waits for expansion. The risk desk shows live exposure per symbol across funded accounts and payout liability = Σ max(0, equity − initial capital) × split; new funded accounts pause while that liability exceeds the payout reserve, sized at 1.8× monthly net sales.

**Account level**: equity, margin, floors and limit proximity per ACC, feeding M12.

**Price integrity**

- At launch there is one feed, policed by delay and gap guards per symbol (CC-5); from growth, a second vendor is compared against deviation thresholds.
- Stale-feed and spike detection; incident mode suspends auto-breach on affected symbols and queues decisions for review (D13).

**Firm-level exposure**

- Net and gross positions by symbol, asset class and direction across S1, S2 and FND, and per identity cluster (M03).
- Payout liability per funded account i, where s is the trader's split, E equity, B the payout baseline, V profit removed for violations, and R a fee refund that counts only once its condition is met (1-Step: the 3rd payout):

```latex
PL_i = s_i\cdot \max\big(0,\ E_i - B_i - V_i\big) + R_i\cdot \mathbf{1}[\text{refund condition met}],\qquad PL=\sum_i PL_i
```

The project's risk-desk formula is this one without V and R. V lowers liability and R raises it once due, so the reserve test can keep the simpler form while the M21 provision uses the full one.

- Trader losses below the baseline cost the firm nothing beyond the fee already earned, while gains create payouts. Liability is therefore convex in market moves and concentrated where traders hold the same direction.
- Sensitivity of PL to each symbol, plus stress scenarios: large moves in heavily held symbols, correlated USD moves, weekend gaps, mass-pass events (S2 accounts weighted by pass probability).
- Pass rates by program and stage monitored against the economics model (M21); a spike signals a rule exploit or a market regime.

**Limits (set by the risk committee, M31)**: liability per CIF and per cluster; funded allocation per CIF (D21); symbol-direction concentration; new funded allocation per period against reserves (D34).

**Mitigation tools**

- New RSVs for future sales (never retroactive); sales pause per program; lower caps; manual payout review above thresholds.
- &#91;LIVE\] At expansion: mirroring a funded account at hedge ratio h makes the firm's result h·X − s·max(0, X) on the trader's P&L X. The firm then loses h·|X| on every losing stretch and keeps only (h − s)·X of gains; full copying (h = 1) bears 100% of real losses for (1 − s) of the profit. Real margin, capital at risk and hedge slippage must be modelled before D39 is switched on.
- Kill switches under an authority matrix: per ACC, cluster, symbol, server or program; halt provisioning; halt payouts (incident only, with customer notice).

**\[LIVE\] Market risk**: real positions, margin and credit limits at the liquidity provider, counterparty risk, simulated-vs-real execution differences, daily reconciliation with broker statements.

**Edge cases**

- Flash crash causing mass breaches → incident mode (M30), price validation, bulk replay.
- Strong trend causing mass passes and large payouts → liquidity stress (M21); sales pause available.
- Feed outage on one symbol → breach decisions for that symbol frozen; affected accounts marked.
- &#91;LIVE\] Liquidity provider rejects hedge orders; latency between simulated fill and hedge fill.

## M16 Trading Integrity Surveillance (Prohibited Strategies)

Prohibited strategies are defined in the terms and the RSV, detected by versioned models, and decided by human review before any adverse action beyond automatic rule breaches.

**Project configuration \[CONFIRMED\]**: expert advisors and copy trading are not allowed at launch; trades that exploit delayed platform prices are voided and the account can be closed. The risk desk flags profitable trades opened just before or during a symbol freeze, very short winning holds, one device or IP across accounts, mirrored order timing across accounts, and positions opened just before listed news. Repeat-account discounts make hedging across a customer's own accounts cheaper, so the hedging check covers same-CIF accounts first.

| Behaviour | Detection signal | Consequence (configurable) |
| --- | --- | --- |
| Latency or feed arbitrage | Profitable entries just before or during a symbol freeze; entries timed to feed delay; very short winning holds | Profit removal; void stage; ban |
| Tick scalping, HFT | Hold-time distribution below D20; order-to-trade ratio; order rate | Profit removal; repeats → void |
| Cross-account hedging (own or linked CIFs) | Opposite positions of similar size and time across graph-linked accounts | Void all involved accounts; ban cluster |
| Hedging against other firms | Not directly observable; proxies: one-directional max-risk trades, no stop-loss, binary outcomes | Review case |
| Copy trading, signal groups | Near-identical entries, exits and SL/TP across unrelated CIFs, clustered in time | Void; group ban |
| Third-party management, account passing or sale | Login location or device inconsistent with the KYC holder; behaviour change at stage transitions | Void; ban |
| All-in sizing | Single trade risking most of the remaining loss allowance without a stop | Warning or violation (D20) |
| Martingale or grid (if prohibited) | Size progression after losses; averaging-down sequences | Violation |
| Gap or weekend exploitation | Positions opened just before scheduled gaps | Profit removal |
| News straddling | Opposing pending orders around events | Violation (M13) |
| Error exploitation | Fills at prices outside the reference range; engine-bug patterns; swap-free abuse | Trade cancellation per terms; void |
| Automated trading (EAs not allowed at launch) | Order-timing regularity; scripted request patterns; identical timing across accounts | Per D17 |

**Process**

1. Continuous scoring per ACC and per cluster; thresholds open cases.
2. Mandatory scan at every stage pass and before every payout.
3. Analyst review with an evidence pack: trades, correlations, logs, graph links.
4. Decision: clear, warn, profit deduction, void stage, terminate CIF, ban cluster. Void, termination and ban need a second reviewer.
5. Customer notice with rule reference and reason code; appeal path (M24).
6. Precedent log so similar cases receive consistent decisions.

**Controls**

- Written evidence standard per behaviour.
- Model versioning, back-testing and false-positive tracking (M31).
- Treatment of public signals and shared commercial EAs stated explicitly in the terms (D17, D18).

**Edge cases**

- Household or office members trading similarly → link-aware review under the evidence standard.
- One commercial EA used independently by many traders → moot at launch, because EAs are not allowed (D17).
- VPN used for privacy → review signal only, not proof.
- Traders from one course or community using one method → correlation alone is insufficient; timing evidence required.

## M17 Fraud and Abuse Prevention

Commercial and identity fraud is detected through the identity graph, payment signals and staff-action monitoring, and handled in one case system shared with M16 and M20.

**Project configuration**: with crypto-only pay-ins at launch, stolen-card purchases and chargebacks do not arise until cards are added. The live risks are multi-accounting to collect repeat-account discounts, promotion-code abuse, account takeover aimed at payout wallets, and tainted crypto. No wallet-screening (blockchain analytics) provider is named in the project record \[OPEN\].

| Threat | Controls |
| --- | --- |
| Stolen-card purchases | PSP fraud score, 3-D Secure, velocity rules, BIN rules, device/IP risk |
| Friendly fraud (chargebacks) | Evidence capture (M08); restriction scope per D30; blocklist |
| Multi-accounting to evade caps or bans | Identity graph, KYC deduplication, hashed blocklists (M03) |
| Promotion abuse | Per-CIF and per-device code limits; first-purchase codes bound to verified identity |
| Affiliate fraud (self-referral, cookie stuffing) | Affiliate–customer link detection; commission holdback until refund and chargeback windows close (M27) |
| Account takeover | Login anomaly detection, MFA, instrument-change hold, change notifications |
| Synthetic identity, forged documents | Vendor authenticity checks, liveness, manual review (M05) |
| Laundering via fee → payout cycles | AML scenarios (M20) joined with hedging detection (M16) |
| Insider fraud (staff creating or approving fake funded accounts or payouts) | Maker-checker, segregation of duties, staff-action anomaly reports (M25) |

**Case management**: one case per subject (CIF or cluster) with linked alerts, severity, SLA, evidence, decision, reviewer and audit trail.

**Edge cases**

- Legitimate customer flagged → appeal; decision reversible on evidence.
- Relatives paying for each other → third-party payment rule (M06).
- Office or university networks sharing IPs → IP edge weight reduced by network type.

## M18 Funded Account Management

A funded account is provisioned only after Tier 2 verification and a signed funded-trader agreement, and its payable profit is measured against a payout baseline so no profit is paid twice.

**Project configuration \[CONFIRMED\]**: 80% split on every product, 90% from the second scale-up; 1-Step refunds its fee with the 3rd payout; first payout 14 days after funding, then the 15th and the last day of each month; $50 minimum; USDT on TRC-20 to a verified wallet; two-person approval; scaling per CC-4 up to $400,000 per trader. No activation fee is recorded.

**Funded onboarding**

1. Last evaluation stage approved (Lite and Classic: S2; 1-Step: S1) or Instant paid (M10) → Tier 2 KYC request (M05).
2. Contract generated: CIF, ACC, allocation, split, payout terms, RSV, simulated or live status disclosure (D1), governing law.
3. E-signature; signer bound to the T2-verified person; signature audit trail stored.
4. Activation fee collected only if D22 ever sets one; none is recorded (M08).
5. FND ACC provisioned (M09); payout and scaling timers started (M11).

**Allocation control**: total funded allocation and number of funded ACCs capped per CIF and per cluster (D21), checked at funding and at scaling; excess denied, queued or consolidated per D21.

**Payable profit and split**

```latex
P=\max\big(0,\ X_{\text{req}} - B - V\big),\qquad \text{Trader share}=s\cdot P
```

X\_req is closed balance or equity at request (D22, D23); B the payout baseline; V profit removed for violations; s the split tier. Fee refunds are a separate line (D22).

- B never moves on a payout; it changes only when scaling sets a new initial capital.
- Full payout: the balance is reduced by P + V (trader and firm shares plus forfeited violation profit), so a closed-balance account returns to B.
- Partial payout of a trader amount W ≤ s·P: the balance is reduced by the gross amount G = W ÷ s plus V; the remaining P − G stays above B and stays payable.
- After any deduction the daily base drops by the amount deducted (M12); on Instant the trailing floor also needs D42.
- Losses after a payout must be recovered above B before the next payout (high-water-mark principle, I8).

**Payout eligibility (all must hold)**

- ACC in FUNDED\_ACTIVE; minimum days since funding or last payout; minimum trading days in the period (D22).
- Consistency rule satisfied (D19); P ≥ minimum payout.
- Positions and pending orders closed if D23 requires.
- No open integrity, fraud or compliance case.
- KYC valid (documents unexpired, screening clean); payout instrument verified and past cooling-off.
- Residence not restricted (D32).

**Scaling**: criteria (time, profit %, payout count, no violations) per D26 → allocation and/or split increase; risk review; new ACC or in-place change; caps re-checked.

**Edge cases**

- Breach after a payout request but before execution → D24.
- Funded breach with unpaid accrued profit → D25, stated in the contract.
- Partial payout or a retained profit buffer → only where D22 permits; B stays unchanged and the remainder stays payable.
- Funded inactivity → D5.
- Document expiry or residence change → payouts held (M05, D32).
- Account sale or transfer to another person → prohibited; detected through access logs (M14, M16).
- Death or incapacity → D36.

## M19 Payout Processing

A payout executes only after eligibility is recomputed at execution time, every check passes, the authority matrix is satisfied, and ledger and engine balance stay consistent.

**Project configuration \[CONFIRMED\]**: payouts are made on the 15th and the last day of each month (the first one 14 days after funding), only in USDT on TRC-20 through NOWPayments mass payouts to a wallet verified for that trader, with a $50 minimum and two-person approval on every payout. Who bears the network fee is \[OPEN\] (D31).

**Process**

1. Request in the portal: ACC(s), amount ≤ eligible, verified instrument; the system snapshots equity, positions and eligibility.
2. Trading paused or allowed per D23.
3. Automated checks: eligibility (M18), replay audit (M14), surveillance scan (M16), fraud checks (M17), sanctions re-screen of CIF and destination, KYC validity, AML risk score (M20), allocation caps.
4. Routing: auto-approve below threshold with no flags; otherwise a manual queue (risk, then compliance) with SLA.
5. Two-person approval on every payout \[CONFIRMED\]; larger amounts may add tiers; requester, approver and executor are different people.
6. Treasury liquidity check against reserves (M21, D34); batching by method and currency; cutoff and holiday calendars applied.
7. Execution through NOWPayments mass payouts with idempotency key = PO ID; network-fee rule per D31.
8. Confirmation from provider webhook or statement, or on-chain transaction hash.
9. Engine balance deduction and daily-base re-basing (M12, M18); ACC returns to FUNDED\_ACTIVE with baseline B unchanged.
10. Ledger postings; customer notice and payout statement; tax-reporting record (M20).

**Denial or partial approval**: reason codes, evidence, customer notice, appeal (M24); partial approval deducts violating profit.

**Controls**

- Sanctions re-screen at execution, not only at approval (I7).
- Daily payout reconciliation: provider statements vs PO records vs ledger vs engine balances (M21).
- Limits per payout, per period and per CIF; anomaly detection on destinations and timing.
- Crypto treasury under multi-party control (multisig or MPC) with hot-wallet limits.

**Edge cases**

- Engine balance deduction succeeds, payment fails → PO retried or a compensating balance credit; never a double deduction.
- Payment succeeds, engine balance deduction fails → further payouts on the ACC blocked until reconciled.
- Provider rejects or returns funds → liability re-credited, instrument suspended, customer asked to update.
- Duplicate request → rejected by idempotency.
- Sanctions list updated between approval and execution → the execution-time screen governs.
- FX moves between approval and execution → rate rule stated in the payout policy (D31).
- Chargeback on the original fee after a payout → recovery under M08; CIF restricted.
- Stuck crypto transaction → fee-bump or replacement policy; never a second payment without on-chain proof of failure.

## M20 Compliance, Legal and Regulatory

Compliance starts with jurisdiction-specific legal determinations; the engine then enforces the resulting restricted-country list, AML and sanctions programme, data-protection rules, consumer terms and tax duties.

**Project configuration**: the project's legal facts and open questions are in CC-8. Confirmed: a Bangladesh software vendor plus an offshore operating company (not yet formed); Bangladesh and India residents refused; no prize contests; no cards, because Stripe prohibits funded prop trading; G0 counsel before the first paid spend. Open: the operator's jurisdiction and licences, and whether paid challenges count as gambling under Bangladesh law (C8).

**Legal determinations required (counsel; not assumed)**

- Operating entity jurisdiction(s) and licensing analysis for the chosen funding mode (D1): consumer protection, gambling-law characterization risk, investment-services licensing, \[LIVE\] broker and liquidity-provider licensing.
- AML and payment-services registration duties of each entity's jurisdiction.
- Foreign-exchange and capital-account rules in the jurisdictions where owners and operators reside, covering fee receipts and payouts.
- Restricted jurisdictions: sanctions, regulatory, banking and PSP constraints (D32).
- Accessibility: whether the European Accessibility Act (Directive (EU) 2019/882, applying to services from 28 June 2025) covers the portal for EU consumers, and whether its microenterprise exemption applies (M22).
- Design clearance: the portal's name, look and icons checked against Apple's trademarks and trade dress before launch (M22, D44).

**Terms and policies** (versioned; acceptance logged per CIF): terms of service; funded trader agreement; privacy and cookie policies; refund policy; risk disclosure (simulated status, no investment advice); prohibited strategies policy; payout policy; complaints policy. Internal: AML, sanctions and data-retention policies.

**AML/CFT programme**

- Enterprise risk assessment; customer risk rating by country, PEP status, payment method, behaviour and payout volume.
- KYC and EDD rules (M05).
- Monitoring scenarios: many instruments per CIF; third-party payments; rapid fee → pass → payout cycles; linked-account hedge pairs (one fails, one is paid); payouts to high-risk jurisdictions or wallets; structuring; unusual refund requests.
- Alert → investigation → decision → suspicious activity report where required → records; no tipping-off.
- Designated compliance officer/MLRO; staff training; independent audit.

**Sanctions programme**: screening at onboarding, purchase, payout and every list update; freeze procedure; reporting duties per jurisdiction.

**Data protection**

- Lawful-basis map per data category; consent management.
- Data-subject requests (access, rectification, erasure, portability) with retention exceptions; GDPR Art. 17(3)(b) covers only duties under EU or member-state law (M03).
- Retention schedule (D35); impact assessment for KYC and biometrics; cross-border transfer mechanisms; processor agreements.
- Breach notification procedure (e.g., GDPR 72-hour regulator notice where applicable).

**Consumer protection and marketing**

- No misleading earnings claims; any published pass or payout statistics reproducible from the data platform (M26). A “was” or struck-through price must be one genuinely charged: misleading reference prices are unfair commercial practices (EU Unfair Commercial Practices Directive; UK DMCC Act 2024). The EU 30-day prior-price rule (Directive 98/6/EC, Art. 6a) covers goods only, though some member states extend price-indication rules to services; counsel to confirm per market.
- Affiliate content review (M27); complaints register with deadlines (M24).
- Statutory withdrawal rights at checkout, with express-consent capture where the law allows early performance (D29).

**Tax**: indirect tax on fees by customer location; invoicing rules; information reporting and withholding on payouts per entity jurisdiction; transfer pricing if multi-entity.

**Legal operations**: legal holds; regulator and law-enforcement request workflow; records retention.

**Edge cases**

- Erasure request during AML retention → mandated records retained, processing restricted.
- Customer's country becomes restricted → wind-down protocol (D32).
- Regulator inquiry → legal hold on the relevant CIFs and logs.
- Vendor terms (feed, payments, KYC) conflict with target markets → vendor review (M29).

## M21 Finance, Treasury and Accounting

A double-entry ledger records every money movement with its external reference, and daily reconciliations prove that ledger, providers, banks, wallets and engine balances agree.

**Project configuration \[CONFIRMED\]**: fixed and variable costs and the 1.8× reserve rule are in CC-7. At launch the reconciliations are NOWPayments deposits and mass payouts, BTCPay and the operator's wallets on chain, and engine balances against the ledger; card reconciliation starts only with cards. Accounting policies are \[OPEN\] (D33).

**Ledger**

- General ledger plus sub-ledgers: orders, refunds, chargebacks, payouts, affiliate commissions, PSP and payout fees, FX, \[LIVE\] hedging P&L.
- Every entry balanced and linked to its ORD, PAY, PO or source ID (I9).

**Accounting policies (accountant determination, D33)**

- Revenue recognition timing for evaluation fees.
- Classification of payouts; accrual for the 1-Step fee refund due with the 3rd payout.
- Provision for expected payouts on funded accounts, using the M15 liability measure.

| Reconciliation | Compares | Frequency |
| --- | --- | --- |
| PSP | Settlements vs orders and refunds | Daily |
| Bank | Statements vs ledger | Daily |
| Crypto | On-chain balances and transactions vs ledger | Daily |
| Payout providers | Statements vs PO records | Daily |
| Engine | Cached balances vs ledger rebuild | Daily and on demand |
| Affiliates | Commissions vs attributed orders net of refunds and chargebacks | Each commission cycle |

Every break gets an owner, an age, a root cause and an escalation threshold.

**Treasury**

- Cash by currency and entity; liquidity forecast from M15 expected payouts.
- Reserve policy: reserves held as a function of accrued and stressed liability (D34).
- PSP rolling reserves and holds tracked; FX exposure managed; crypto custody split hot/cold under multi-party control.

**Program economics**

- Per program and size: fee revenue, pass rates by stage, funded conversion, payout frequency and size distribution, refund and chargeback rates, acquisition cost, margin.
- Historical or Monte Carlo simulation of new programs and RSV changes — a required input to the M07 publish gate. Baseline: the pricing thread's zero-edge payout model, calibrated to FTMO's published 2024 results, with 2025 as the payout-spike case.

**Controls**: segregation of duties; approval matrix; period close; audit trail; external audit support.

**Edge cases**

- PSP terminates with funds held → alternate PSP (M30); receivable tracked.
- Chargeback surge → reserve top-up; sales review.
- Payout demand exceeds liquidity → incident (M30), reserve policy, communicated timelines.

## M22 Trader Portal

The portal shows each customer every account, rule status and money movement under their CIF, using the same metric definitions the engine enforces.

**Project configuration \[CONFIRMED\]**: the firm's own portal in React with Lightweight Charts drawn from its feed, so the chart shows the prices the engine fills at; “Data provided by Twelve Data” beside every chart and price table; the Lightweight Charts NOTICE text and a link to tradingview.com; login through Keycloak; the restricted-news list published monthly. Added requirement (audit): before each 17:00 New York reset the dashboard shows the next day's daily floor, so a trader carrying a floating loss sees a reset-instant breach coming (M12).

**Functions**

- Access: registration, login, MFA, sessions, profile, consents, data-subject requests.
- Verification: KYC submission and status; document re-upload.
- Payments: pay-in and payout instruments; checkout; invoices and receipts.
- Accounts: every ACC with stage and status. Per-ACC dashboard: balance, equity, daily loss remaining with floor value, max loss remaining with floor value, target progress, trading days, time left, rule status, warnings.
- Trading: order ticket for market, limit, stop and trailing orders (M09); trailing distance and step entered in points; each working trailing order shows its current level and its last move, with the as-of time.
- Trading data: history, statements and exports; the account's RSV rulebook; the news restriction calendar (M13).
- Funded: contract, payout requests with status and history, scaling status.
- Support: tickets, chat, notification centre; affiliate dashboard for affiliates.
- Certificates and leaderboards (MAY): published only with the customer's consent and never with cash or material prizes \[CONFIRMED\].

**Design**

- Style: Apple-inspired, not copied — calm layouts, generous spacing, one accent colour, a clear type hierarchy, light and dark themes, restrained motion — under the firm's own brand, colours, icons and screens. \[CONFIRMED\]
- Nothing of Apple's is used: no Apple names, logos or trademarks in the product or its marketing, and no claim or hint of a link to Apple. No SF Pro, SF Compact or New York fonts and no SF Symbols icons are bundled or served, because Apple licenses them only for mock-ups of apps on Apple platforms (recheck the licence text at build). No screen, icon, sound or image is traced from Apple products or apple.com. \[CONFIRMED\]
- Fonts and icons under open licences only, for example Inter (SIL Open Font License 1.1) and Lucide (ISC); their licence texts ship with the portal, and the ScanCode scan (M28) checks them. \[DEFAULT\]
- One design system — tokens for colour, type, spacing, corner radius and motion — shared by the portal and the back office (M25). \[DEFAULT\]
- Accessibility to WCAG 2.2 level AA: contrast, full keyboard use, screen-reader labels, visible focus and a reduced-motion setting. Whether the European Accessibility Act applies is a counsel question (M20). \[DEFAULT\]
- No manipulative design: no false urgency or fake countdowns, no pre-ticked paid add-ons, cancelling as easy as buying, and cookie refusal as prominent as acceptance (M20). Every trading screen labels the account as simulated (CC-1). \[DEFAULT\]

**Controls**

- Every metric labelled with its as-of time and source; definitions identical to M12 and M14 (I14).
- Server time shown beside local time; each ACC's reset instant displayed.
- Step-up authentication for wallet changes and payouts.

**Edge cases**

- Portal lags the engine in fast markets → as-of label; breach notices come from the engine, not the portal view.
- Customer assumes local midnight is the reset → reset instant shown explicitly per ACC.
- Trader closes the portal with a trailing order working → it keeps trailing in the engine; on return the portal shows its current level and every move since.

## M23 Notifications and Communications

Every lifecycle event maps to a versioned template and channel, and every delivery is logged against the CIF and ACC.

**Project configuration**: transactional email through Amazon SES at $0.10 per 1,000 \[CONFIRMED\]; SMS and push providers are \[OPEN\].

**Event → message map**: purchase and receipt; account ready; stage started; proximity alerts; breach with evidence summary; pass; review outcome; KYC requests and outcomes; contract ready and signed; payout status; document expiry; inactivity and deadline warnings; rule or terms changes; incident notices; marketing (consent-based).

**Channels**: email, in-app, SMS, push; localization; preference centre separating mandatory transactional messages from opt-in marketing.

**Controls**

- Template versioning with approval; delivery log (M14, L8); deduplication; rate limits.
- No passwords, document images or full account numbers in any message.

**Edge cases**

- Critical notice bounces → fallback channel.
- Mass event (many breaches at once) → throttled individual notices plus one incident notice.

## M24 Customer Support and Dispute Resolution

Support works from the CIF 360 view, resolves rule disputes by replaying the account from stored data, and changes balances or states only through admin approval flows (M25).

**Project configuration \[CONFIRMED\]**: FreeScout (self-hosted, unmodified) is the support desk; disputes must be raised within 30 days of the trade; the trader terms make platform prices the only reference for fills and rule checks.

**Ticket model**: TKT linked to CIF and optionally ACC, ORD or PO; category (billing, technical, rules, breach dispute, KYC, payout, access, complaint); priority; SLA; status; assignee; escalation level.

**Tiers**: L1 general; L2 specialists (rules and risk, payments, KYC); L3 engineering; compliance escalation for AML and sanctions using no-tipping-off scripts.

**Identity check**: the contact is verified against CIF factors before any account data is disclosed or changed.

**Breach dispute workflow**

1. Customer submits ACC and timestamp.
2. Evidence pack built automatically: replay result, prices vs reference feed, formula and RSV, trade list, logs (M14).
3. Analyst decision within the authority matrix: uphold; reverse (reinstate with restored state); or goodwill remedy (free retake, discount).
4. Response with rationale; appeal to a second reviewer; final decision and precedent logged.

Payout denials and KYC rejections follow the same pattern.

**Complaints**: formal register, root cause, deadlines where regulated (M20).

**Controls**: approved macros; QA sampling; satisfaction surveys; SLA dashboards; personal data masked; no direct write access to balances or states for support roles.

**Edge cases**

- Abusive or threatening customer → documented warning, channel restriction, legal escalation where needed.
- Public social-media escalation → same workflow; no account data disclosed publicly.
- Chargeback threat → no concession outside policy; the dispute workflow continues.

## M25 Admin Console and Internal Controls

Every staff action runs under role-based access, a mandatory justification and an immutable audit log, with maker-checker on any action that moves money or changes an account's outcome.

**Project configuration \[CONFIRMED\]**: the back office is react-admin with the risk desk; staff log in through Keycloak; every payout needs two-person approval; new funded accounts pause automatically while payout liability exceeds the reserve (M15).

**Roles**: Support L1/L2; Risk analyst; Risk manager; Compliance analyst; MLRO; Finance; Treasury; Program manager; Engineering (break-glass only); Auditor (read-only); Super-admin (restricted).

**Functions**

- CIF 360; ACC actions: enable/disable trading, breach, reinstate, extend, reset, manual pass/fail, RSV rebinding, close positions.
- Balance corrections; program and RSV management; promotions; KYC review; case management; payout approval; refunds.
- Blocklists; restricted-country list; manual news windows; kill switches; reports; bulk operations with dry-run.
- Same design system as the portal (M22). Trailing orders shown with every level move and the price update that caused it.

| Action | Control |
| --- | --- |
| Balance adjustment, reinstatement, manual pass | Maker-checker; reason code; evidence link |
| Every payout (two-person approval, CONFIRMED); refund above threshold | Maker-checker; amount-tiered authority |
| RSV publish; restricted-country list change | Maker-checker; diff review |
| CIF merge or split; blocklist removal | Maker-checker |
| Kill switch | One authorized role in an emergency; post-hoc review within a fixed window |
| Personal-data reveal; view as customer | Logged per access; masked by default |
| Bulk action | Dry-run output reviewed; approval; rollback via compensating events |

**Controls**

- Least privilege; segregation of duties (e.g., KYC approver ≠ payout approver for the same CIF).
- Periodic access recertification; same-day deprovisioning on staff exit.
- Anomaly detection on staff behaviour: unusual reinstatement volume, repeated payouts to one destination, actions on related accounts.
- Staff and related-party trading accounts prohibited or ring-fenced (D40).
- Break-glass access is time-limited and alerted.

**Edge cases**

- Erroneous bulk action → compensating events derived by replay; never deletion.
- Staff member who is also a customer → conflict flag; cannot act on own or related CIFs.

## M26 Reporting, Analytics and Monitoring

Operational, business, risk, compliance and finance reports all draw on one metric catalogue, so portal, admin console and management reports cannot disagree.

**Project configuration \[CONFIRMED\]**: Prometheus, Grafana (internal only) and Uptime Kuma; a quality monitor per price source tracks delay distribution, gap rate, divergence from a second feed (from growth) and uptime. Acceptance test 6 measures the 99th-percentile time from price update to breach decision on the production server; test 7 measures the feed (source timestamps, delay by session, gaps, reconnects) over the 12-day trial and 30 paid days.

| Domain | Contents | Cadence |
| --- | --- | --- |
| System health | Tick-to-decision latency, event lag, error rates, feed connectivity, feed health, scheduler status | Real time |
| Operations | Queues (KYC, payouts, cases, tickets); breaches and passes today; payout volume | Real time |
| Business | Sales by program, size, country and channel; funnel per product (register → purchase → each stage pass → funded → first payout); time to pass; breach-reason mix; payout ratio; cohort profitability; refund and chargeback rates | Daily and weekly |
| Risk | Exposure and liability (M15); stress results; top accounts by liability; surveillance alerts and outcomes; kill-switch use | Real time and daily |
| Compliance | KYC throughput and rejection reasons; sanctions hits; AML alerts and reports (restricted access); data-subject requests; complaints | Weekly and monthly |
| Finance | P\&L; cash; reserves; reconciliation breaks; deferred revenue and provisions | Daily and monthly |

**Data platform**: change-data capture and events → warehouse; data-quality tests; metric catalogue with single definitions; as-of snapshots so reports are reproducible; role-based access.

**Monitoring and alerting**: SLOs per critical service; on-call rotation; runbooks; escalation paths.

**Edge cases**

- Late data → restatement flagged with a version.
- Metric definition change → versioned; historical reports keep the prior definition label.

## M27 Affiliates, Promotions and CRM

Affiliates are verified and paid only on net, settled revenue; promotions are rule-bound and capped; CRM messages respect consent and suppression lists.

**Project configuration**: codes give up to 10% on a first Lite, Classic or 1-Step account and are not valid on Instant; no commission is paid on repeat-account sales; codes and repeat discounts never combine \[CONFIRMED\]. Affiliates resident in, or campaigns aimed at, Bangladesh or India are refused, and referral attribution from those countries is void: if paid challenges are classed as gambling, Cyber Security Ordinance 2025 s.20 covers advertising and assisting, and the Gambling Prevention Act 2026 may reach affiliate marketing (C8). The commission model is \[OPEN\].

**Affiliates**

- Onboarding: KYC or KYB (M05), contract, marketing-content rules; restricted jurisdictions (D32) checked at onboarding and before each commission payout.
- Tracking: links and codes; attribution window and model (first or last click).
- Commission: CPA or revenue share on fees net of refunds and chargebacks; holdback period; clawback.
- Payment through the M19 flow; tax forms; content compliance review (M20).
- Fraud controls per M17.

**Promotions**

- Code types: percentage, fixed amount, free reset, add-on.
- Eligibility: new customers, countries, programs. Limits: global and per CIF/device; stacking rules; expiry; budget cap.
- Creation and usage audited.

**CRM**

- Consent-based lifecycle messaging; segmentation by stage and status; post-breach retake offers (D27); win-back.
- Suppression of banned, restricted-country and opted-out CIFs.

**Edge cases**: affiliate referring their own accounts (M17); leaked codes (usage caps); discount applied after a price change (honoured within quote validity).

## M28 Security and Data Protection

Security controls protect three high-value assets: customer identity data, money-moving functions and engine balance changes.

**Project configuration \[CONFIRMED\]**: Keycloak for traders and staff; SOPS and age for secrets; Caddy for TLS; Trivy, Semgrep CE, ZAP and ScanCode for scans; an external penetration test before launch (C9, gate G4); pgBackRest to Backblaze B2 offsite, with price-containing backups kept 30 days at most; a backup-restore drill at gate G2; production on OVHcloud in Europe running Docker Engine, with Docker Desktop only on the build machine.

- Identity and access: staff MFA and SSO; role-based access (M25); privileged-access management.
- Data: TLS in transit; encryption at rest; field-level encryption for KYC documents, tax IDs and bank details; KMS/HSM key management with rotation.
- Payments: PSP-hosted tokenization to minimize PCI DSS scope; no storage of card numbers or security codes.
- Application: secure development lifecycle, code review, dependency and secret scanning, WAF, rate limiting, bot defence, signed webhooks, mutual TLS on internal links.
- Engine: only the engine writes the ledger, and any balance change without a ledger entry raises an alert (I10); feed and payment API keys are vaulted with SOPS and age.
- Infrastructure: network segmentation; hardened hosts; encrypted, restore-tested backups; high availability for the engine, feed connector and ledger; disaster recovery with defined RPO/RTO (M30).
- Detection: SIEM, intrusion detection, data-loss prevention, vulnerability management, periodic penetration tests.
- Vendors: due diligence and ongoing monitoring of KYC, payment, hosting and data vendors (M29).

**Edge cases**: credential stuffing on the portal (rate limits, MFA); leaked feed or payment API keys (rotation, alerts); insider data exfiltration (DLP, access logging).

## M29 Integrations and External Dependencies

Every external dependency has a defined failure mode, fallback and reconciliation, because each one sits on a money, identity or rule-decision path.

| Dependency | Used by | Fallback on failure |
| --- | --- | --- |
| Twelve Data feed (Business Venture) | M09, M12, M22 | Delay and gap guards freeze symbols; an outage stops new orders |
| Second feed at growth (iTick Professional or TraderMade) | M12, M15 | Not at launch |
| Official calendars (BLS, Federal Reserve, ECB, Bank of England, Bank of Japan, EIA) | M13 | Monthly manual update; the risk desk marks Bank of Japan releases |
| NOWPayments (USDT TRC-20 deposits and mass payouts) | M06, M08, M19 | BTCPay for Bitcoin deposits; payouts queued |
| BTCPay Server, self-hosted (optional) | M06, M08 | NOWPayments |
| Didit (ID check, AML screening, rescreening) | M05, M19, M20 | Manual review queue; funding and payouts held |
| Amazon SES | M23 | Queue and retry |
| OVHcloud VPS-2 and VPS-1 | All | Restore from pgBackRest backups (G2 drill) |
| Backblaze B2 | M28, M30 | Local backups retained |
| Keycloak, FreeScout (self-hosted) | M04, M24, M25 | Restore from backup |
| Prop-specialist card processor (expansion) | M06, M08 | Not at launch |
| Broker or LP bridge (expansion) | M15 | Not at launch |

Not used, by licence: TradingView data, Deriv API, Binance, Coinbase, Gemini, OKX, Bybit, Pyth free tier, CoinMarketCap free plan and AllTick (CC-5). Wallet-screening, SMS and push providers are not chosen \[OPEN\].

**Integration controls**: idempotent calls; retries with backoff; dead-letter queues; circuit breakers; timeouts; webhook signature checks; schema versioning; contract tests; vendor status monitoring; daily reconciliation; minimal data in payloads; an exit plan per critical vendor.

## M30 Incident Management and Business Continuity

Incidents follow one classified runbook process, and firm-caused account damage is remediated by replay, not by discretion.

**Project configuration**: in a feed outage there are no new orders, open positions keep their last price and checks resume on the first fresh update \[CONFIRMED\]; delay and gap guards freeze single symbols \[DEFAULT\]; a vendor change runs both feeds side by side and deletes Twelve Data prices within 30 days of cancelling \[CONFIRMED\].

**Incident classes**: engine or server outage; price-feed error or bad ticks; rule-engine malfunction (false breaches or passes); extreme market event; PSP or payout outage; data breach; DDoS; vendor licence termination; banking or PSP de-risking; regulatory action; key-person unavailability.

**Process**

1. Detect (M26) and classify severity.
2. Assign an incident commander.
3. Contain: kill switches, auto-breach suspension, sales or payout pause (M15, M25).
4. Communicate: status page and templated customer notices (M23).
5. Remediate: bulk replay with corrected data; reinstate accounts whose breach disappears; extend time limits by the outage duration (M11); compensation per policy.
6. Post-incident review: root cause and corrective actions tracked to closure.

**Pre-approved policies**: reinstatement criteria for firm-caused breaches; time extensions; trade cancellation and adjustment rights for erroneous prices (must exist in the terms); communication templates.

**Business continuity**: RPO/RTO per service; tested failover; alternate PSPs and payout rails; server and feed migration plan; orderly wind-down plan (stop sales, honour accrued obligations per terms, retain records).

**Edge cases**: incident during a payout batch (idempotent resume); incident spanning a daily reset or DST change (baselines rebuilt from history); partial data loss (rebuild from the event journal and backups, then replay).

## M31 Governance, Change Management and Model Validation

Rule, model and code changes pass impact testing and approval before release, and existing accounts never change rules silently.

**Project configuration \[CONFIRMED\]**: phases P1–P6 and gates G1–G4, with G0 before P3's first paid spend, are in CC-8. Seven acceptance tests must all pass: deterministic replay; ledger property tests; rule edge cases; crash and restart; delay and gap guards; load; feed measurement.

- Policy ownership and review cycle: risk, AML, sanctions, payout, refund, rules, security and retention policies.
- RSV change: proposal → replay of historical accounts under old and new RSV (pass rate, payout cost, fairness) → compliance and legal review → approval → publication → customer notice → post-release monitoring.
- Software change: unit and integration tests; replay regression on golden datasets; time-boundary tests (resets, DST, holidays, leap day); load tests at news-event peaks; staged rollout; feature flags; rollback.
- Model governance (surveillance, fraud, risk): documentation, validation, drift and false-positive monitoring, human review before adverse action.
- Committees: risk (limits, exposure, reserves), compliance oversight, incident review.
- Assurance: internal and external audit cycles; KPIs and KRIs with escalation thresholds.

## M32 System Invariants

These 15 invariants must hold at all times; automated checks run continuously, and any violation raises a P1 alert.

| ID | Invariant |
| --- | --- |
| I1 | Every ACC belongs to exactly one CIF; after KYC every natural-person CIF maps to exactly one natural person (entity CIFs, if ever allowed, are a separate type). |
| I2 | Every ACC is bound to exactly one RSV at any instant; RSVs are immutable, rebinding (e.g., an add-on) is an effective-dated event, and every rule outcome references the RSV in force. |
| I3 | Every ACC has exactly one current state; every state change has an event with actor and reason. |
| I4 | BREACHED, EXPIRED and VOIDED states are never edited; a reversal is a new approved event. |
| I5 | No ACC is provisioned without a confirmed paid order (including any activation fee) or an approved advancement, reset or scaling event; no funded ACC trades before T2 KYC and the contract clear. |
| I6 | Only accounts in S1\_ACTIVE, S2\_ACTIVE or FUNDED\_ACTIVE — and PAYOUT\_PENDING when D23 allows trading — can place orders. |
| I7 | No payout executes without execution-time eligibility, valid KYC, a clean sanctions screen, a verified owned wallet and two-person approval. |
| I8 | Per order lineage (an ORD and every ACC descended from it), cumulative payouts ≤ cumulative trader share of payable profit plus the fee refund, counted once per ORD. |
| I9 | The ledger balances per entry and per period; every money movement has a ledger entry and an external reference. |
| I10 | Every balance change is a ledger entry written by the engine; the cached balance always equals the ledger rebuild (acceptance test 2). |
| I11 | While its price data is licensed and retained, replaying any ACC reproduces its stored rule outcomes (acceptance test 1). |
| I12 | Log streams are append-only, with unbroken hash chains. |
| I13 | Funded allocation per trader never exceeds $400,000 (CC-4), nor a cluster cap where one is set. |
| I14 | The customer-facing rulebook and portal metrics derive from the enforced RSV. |
| I15 | No two natural-person CIFs share a verified identity document. |

## Appendix A — End-to-End Data Flows

Thirteen flows connect the modules; every step is an event or call recorded in M14.

**F1 Registration → CIF**

1. Portal sign-up → M04 gates (geo, age, bot) → M03 provisional CIF.
2. M05 T0 checks (T1 waits for the first purchase, F2) → "Customer registered" → M23 welcome, M27 CRM.

**F2 Purchase → active account**

1. M08 cart and price → T1 AML screen (M05), fraud and cap checks (M17, M03) → NOWPayments USDT invoice, or BTCPay for Bitcoin (M06).
2. Verified webhook → order Paid → M21 ledger posting.
3. M10 saga → M09 creates the ACC in the engine and posts the initial balance → rule state and timers initialized (M12, M11).
4. Account-ready notice (M23) → ACC visible in the portal (M22).

**F3 Trade or tick → rule outcome**

1. Twelve Data update → feed connector → Valkey Streams (sequence-numbered) → engine: fills, positions, equity.
2. Trailing orders: each price update can move trailing levels in the engine (M09); a trigger fills like a stop order, and inside a news window counts as M13 sets.
3. M12 evaluation against the RSV → none, warning, soft violation, pass or breach.
4. Breach: delay and gap guards pass (M15) → M10 BREACHED → M09 closes positions at the breach price and locks the account → M23 notice → M14 evidence bundle.
5. Derived metrics → portal (M22) and reports (M26).

**F4 Daily reset**

1. M11 scheduler at the reset instant → baselines computed from history at that instant → M12 rule state.
2. Trading-day counter, inactivity and duration checks → events to M10.

**F5 Stage pass**

1. M12 pass conditions met → M10 review state (read-only).
2. M16 scan + M14 replay audit → automatic or manual approval.
3. Next step: S2 for Lite and Classic (M09 provisions a new ACC), or KYC + contract for 1-Step (F6) → M11 timers → M23 notice.

**F6 Funded onboarding**

1. Last evaluation stage approved, or Instant paid → M05 Tier 2 KYC (Didit) → screening → decision.
2. M18 contract generation → e-signature → M09 FND ACC → M11 payout timers.

**F7 Payout**

1. M22 request → M18 eligibility snapshot → checks (M14, M16, M17, M05, M20).
2. Two-person approval (M25) → M21 liquidity check → M19 NOWPayments mass payout on the 15th or the last day of the month (M29).
3. Confirmation → M09 balance deduction and daily-base re-basing (M12) → M21 ledger → M23 notice → tax record (M20).

**F8 Breach → retake**

1. Breach event → M23 notice → M27 retake or reset offer (D27).
2. Reset purchase (F2) → new ACC with parent pointer; old ACC superseded.

**F9 Chargeback**

1. Card PSP webhook, from expansion only (crypto pay-ins cannot be charged back) → M08 links ORD, ACC and CIF → restriction (D30) → evidence pack → representment.
2. Outcome → M21 ledger → M03 status update.

**F10 Sanctions list update**

1. List delta → re-screen of CIFs and payout destinations (M05) → hits → M20 case.
2. Confirmed match → ACCs and payouts frozen (M25) → reporting per jurisdiction.

**F11 Breach dispute**

1. M24 ticket → replay evidence pack (M14) → analyst decision → appeal.
2. Reversal → M25 maker-checker reinstatement → M10 new state event → M23 notice.

**F12 News calendar**

1. Official schedules → M13 monthly list and versioning → windows → portal (M22) and pre-trade blocking in the engine (M09).
2. Violations → M12 consequence (D15).

**F13 Reconciliation**

1. Daily jobs (NOWPayments, BTCPay and on-chain wallets, engine balances against the ledger, affiliates; card PSP from expansion) → breaks → M21 finance cases → resolution.

## Appendix B — Policy Decision Register

Forty-four operator decisions set the engine's behaviour. The project record confirms 14, partly confirms 9, sets 2 defaults and leaves 18 open; chargeback scope does not apply at launch. Change a status in its dropdown as decisions land.

| ID | Decision | Options | Status | Project value | Modules |
| --- | --- | --- | --- | --- | --- |
| D1 | Funding mode | Simulated · live · hybrid (simulated with selective mirroring) | Confirmed | Hybrid; all accounts simulated at launch; A-book for proven funded traders deferred | M09, M15, M18, M20, M21 |
| D2 | Stage account handling | New ACC per stage · same account reset | Open | None recorded | M02, M09, M10 |
| D3 | Stage clock start | Purchase · first trade | Open | None recorded | M11 |
| D4 | Max duration per stage | Unlimited · N calendar days · N trading days | Confirmed | No time limit | M10, M11 |
| D5 | Inactivity limit | None · N days; definition of activity | Open | None recorded | M11, M18 |
| D6 | Trading-day definition | Trade opened · trade closed · any fill; minimum profit or volume per day | Open | Count confirmed (4 per evaluation phase); definition not recorded | M11, M12 |
| D7 | Daily reset instant | Server midnight · fixed time in a named zone | Confirmed | 17:00 America/New\_York | M11, M12 |
| D8 | Daily loss base and amount | SOD balance · SOD equity · higher of both; % of IB · % of base | Confirmed | Higher of balance and equity at reset; % of initial capital (3%; Classic 4%) | M12 |
| D9 | Max loss type | Static · intraday trailing · end-of-day trailing; lock level | Confirmed | Static: Lite 6%, Classic 8%, 1-Step 6%; Instant 5% intraday trailing, locked at the starting balance | M12 |
| D10 | Profit target basis | Closed balance · equity; positions closed at pass: yes · no | Open | None recorded | M10, M12 |
| D11 | Pass and breach in the same instant | Breach precedence · timestamp precedence | Open | None recorded | M10 |
| D12 | Spread, swaps, commissions, adjustments in breach equity | Included · excluded (excluded = value at the feed price) | Partly confirmed | Equity always at synthetic bid/ask; swaps and commissions undefined (D41) | M09, M12 |
| D13 | Bad-price handling | Validate before breach · breach, then review and reverse; second feed | Confirmed | One feed; platform prices the only reference; delay and gap guards (default); second-feed divergence halt from growth | M12, M15, M30 |
| D14 | Breach close-out | Auto-close positions · disable trading only | Default | Close every position at the breach price; lock the account | M09, M12 |
| D15 | News restriction | Stages, impact levels, window, actions covered, consequence (breach · profit removal), holding allowed | Confirmed | ±2 minutes, every phase, open and close blocked on affected symbols, crypto exempt; pending entries held to the window's end; stop-loss, take-profit and trailing-stop exits execute, with profit excluded and losses kept (default) | M12, M13 |
| D16 | Weekend/holiday holding | Allowed · auto-close at cutoff · violation | Default | Evaluations may hold; funded may not hold through a break over 2 hours (C15) | M11, M12 |
| D17 | EA policy | Allowed · own EAs only · prohibited; third-party EAs | Confirmed | Not allowed at launch | M12, M16 |
| D18 | Copy trading between own accounts | Allowed within caps · prohibited | Confirmed | Not allowed at launch | M16 |
| D19 | Consistency rule | None · profit-concentration cap · lot consistency · single-trade cap; window; attribution of partial closes | Partly confirmed | Best day ≤ 50% of profit on 1-Step and Instant; none on Lite and Classic; window and attribution not recorded | M12, M18 |
| D20 | Minimum hold time, order rate, max risk per trade | Values and consequences | Open | Lot caps per order and position (default, set in P3); the rest not recorded | M12, M16 |
| D21 | Allocation and account caps | Per trader · per cluster; excess denied · queued · consolidated | Partly confirmed | $400,000 per trader; account-count and cluster caps not recorded | M03, M15, M18 |
| D22 | Funded economics | Split tiers; fee refund; activation fee; payout frequency; first-payout delay; min/max payout; payout basis | Partly confirmed | 80% split, 90% from the 2nd scale-up; 1-Step refund with the 3rd payout; first payout after 14 days, then the 15th and last day; $50 minimum; no activation fee; maximum payout, basis and the 90% add-on (C16) not recorded | M18, M19 |
| D23 | Trading during payout processing | Paused · allowed; positions must be closed: yes · no | Open | None recorded | M18, M19 |
| D24 | Breach between payout request and execution | Void payout · honour profit accrued at request | Open | None recorded | M18, M19 |
| D25 | Accrued profit on funded breach | Forfeited · paid | Open | None recorded | M18 |
| D26 | Scaling plan | Criteria; new ACC · in-place change | Partly confirmed | CC-4 criteria; new ACC or in-place not recorded | M11, M18 |
| D27 | Reset and retake products | Eligibility, price, stages covered | Open | Conflicting records (CC-2) | M07, M10, M27 |
| D28 | KYC timing; failure after the last evaluation stage | Tier triggers; refund · no refund | Partly confirmed | AML at first purchase; full ID before any funded account; refund on failure not recorded | M05, M10 |
| D29 | Refund policy | Windows, conditions, statutory-rights handling | Open | None recorded | M08, M20 |
| D30 | Chargeback consequence scope | ACC · CIF · identity cluster | Not at launch | Crypto pay-ins cannot be charged back; decide before cards | M08, M17 |
| D31 | Payment and payout methods | Methods; chains and tokens; fee bearer; FX rule; wallet verification | Confirmed | USDT on TRC-20 via NOWPayments (C6), Bitcoin via BTCPay optional; no cards, local rails or bank wire; fee bearer and wallet-verification method not recorded | M06, M19 |
| D32 | Restricted jurisdictions | List; wind-down protocol on change | Confirmed | Bangladesh and India; other countries and wind-down not recorded | M04, M20, M27 |
| D33 | Accounting policies | Revenue recognition; payout provisioning | Open | None recorded | M21 |
| D34 | Reserves | Reserve ratio; liquidity limits | Confirmed | 1.8× monthly net sales; new funded accounts pause while liability exceeds it | M15, M21 |
| D35 | Retention | Period per data category | Partly confirmed | Twelve Data prices deleted within 30 days of cancelling; price backups kept 30 days at most; other categories not recorded | M14, M20 |
| D36 | Death or incapacity | Estate payout policy | Open | None recorded | M03, M18 |
| D37 | Unscheduled news windows | Prospective only · retroactive | Open | Bank of Japan window default (opens when marked published, runs 2 minutes); unscheduled events not recorded | M13 |
| D38 | Equity source of truth | Engine · platform; reconciliation rule | Confirmed | The firm's own engine | M09, M12 |
| D39 | Liability hedging | None · mirror selected accounts · aggregate hedge | Confirmed | None at launch; A-book routing deferred to expansion | M09, M15 |
| D40 | Staff and related-party trading | Prohibited · ring-fenced | Open | None recorded | M14, M25 |
| D41 | Swaps and commissions on simulated accounts | None · per-symbol swaps · commission per lot · both | Open | None recorded | M07, M09, M12 |
| D42 | Trailing floor at payout (Instant) | Cap the withdrawal to keep equity a buffer above the floor · restart trailing from the post-payout balance | Open | None recorded | M12, M18 |
| D43 | Trailing orders | Kinds; distance and step; price followed; news windows; frozen symbol or closed market | Partly confirmed | Trailing stop and trailing entry, the trader's choice; distance and step set by the trader in points; buys follow the ask, sells the bid; news windows as M13 (trailing stops exit like stop-losses; trailing entries held); frozen or closed market: no move or trigger until the next valid price (default) | M09, M13, M14, M22 |
| D44 | Interface design | Style; font and icon licensing; accessibility level; shared design system | Partly confirmed | Apple-inspired, not copied: no Apple names, fonts or icons; open-licence fonts and icons, WCAG 2.2 AA and one design system for portal and back office (default) | M20, M22, M25 |

## Appendix C — Audit Disposition (28 Oct 2026)

Of 27 findings in the 28 Oct 2026 audit, 16 were applied, 3 applied in part with corrections, 4 accepted without change, 3 made moot by the confirmed configuration and 1 rejected.

| Finding (audit §) | Verdict | Action and location |
| --- | --- | --- |
| §1.1 Counts of modules, invariants, flows and decisions | Accepted | Decisions now number 42 (D41 and D42 added). |
| §1.2 “Unrendered placeholders” in M01 and M10 | Rejected | Both are embedded diagrams that render in the doc; text exports drop them. M01 now also names its layers in text. |
| §1.3 M10 caption “9 states, 2 terminal outcomes” against 15–16 states | Applied | Caption and note now say the diagram groups six terminal states; the table is complete. |
| §1.4 M04, M26, M27, M30 missing from M01; affiliate, promotion and incident entities missing from M02 | Applied | M01 ownership table and M02 entity table extended. |
| §2.1 Double debit on payouts; partial payout deducts all profit | Partly applied | The outline never raised B, but “new baseline” was ambiguous: B is now fixed on payouts (M10, M18, M19). Partial payouts deduct G = W ÷ s plus V (M18). |
| §2.2 Trailing floor locked at IB breaches on a full payout | Applied | Real for Instant: D42 added (cap the withdrawal or restart the floor). Beyond the audit: payouts also re-base the daily floor (M12). |
| §2.3 Target formula hard-codes closed balance against D10 | Applied | X\_t per D10 (M12). |
| §2.4 Breach at the daily reset instant | Accepted | A property of the confirmed rule (higher of balance and equity at reset), not only of a balance-only base; documented in M12, with a pre-reset warning in M22. |
| §2.5 Consistency formula ignores V, partial closes and single-trade concentration | Applied | V subtracted; attribution and a single-trade cap added as D19 options. |
| §2.6.1 Fee refund R added unconditionally | Applied | R counts only once its condition is met (1-Step: the 3rd payout) (M15). |
| §2.6.2 V omitted from liability | Applied | V subtracted (M15); Plan v4's simpler formula kept for the reserve test. |
| §2.6.3 Live-mirroring downside not modelled | Applied | Firm result h·X − s·max(0, X) added for expansion (M15, D39). |
| §3.1 I6 conflicts with PAYOUT\_PENDING when trading is allowed | Applied | I6 now lists PAYOUT\_PENDING when D23 allows trading. |
| §3.2 I10 raises false alerts on platform-posted swaps and adjustments | Moot | The confirmed platform is the firm's own engine, which alone writes the ledger; I10 restated around the ledger. |
| §3.3 I1 and I15 conflict with corporate customers | Applied | Scoped to natural-person CIFs; entity CIFs are a separate type. |
| §3.4 I8 counted per ACC lets refunds repeat on scaled ACCs | Applied | I8 counts per order lineage, with the refund once per ORD. |
| §3.5 Activation-fee guard missing | Applied | Guard added to M10 and I5; no activation fee is recorded. |
| §3.6 KYC hard-wired to the S2 pass breaks 1-Step and Instant | Applied | T2 before any funded account; per-product paths in M05, M10 and Appendix A; the three-stage conflict is flagged as F-2. |
| §3.7 T1 timing contradicts itself | Applied | T1 at first purchase, as Plan v4 sets (M04, M05, F1, F2). |
| §4.1 FATF R.11 | Accepted | No change needed. |
| §4.2 GDPR Art. 17(3)(b) limited to EU and member-state law | Applied | M03, M20 and Section 0 corrected. |
| §4.3 MT5 deal-reason fields and the trade-copier caveat | Moot | MT5 references removed (own engine). The audit's extra enum values and copier claim were not checked and are not used. |
| §4.4 2026 DST windows | Accepted | No change needed. |
| §5.1 No formula for excluding rollover spread | Moot | Equity is always valued at synthetic bid/ask; with a symmetric spread the mid equals the feed price (M12). |
| §5.2 Tick retention against data licensing | Partly applied | Retention now follows the Twelve Data licence (M14). The audit's Deriv “24 hours” and “cl. 2.3” do not match the project record (cl. 2.1 storage, cl. 1.2.1 sublicensing), and Deriv is not used; crypto pay-ins have no chargeback window. |
| §5.3 Affiliates not gated by restricted jurisdictions | Applied | Gating added in M27. Wording corrected to the project record: CSO 2025 s.20 covers advertising if challenges count as gambling, and the reach of the Gambling Prevention Act 2026 is counsel question C8, not settled liability. |
| §5.4 Reference-pricing compliance | Partly applied | Misleading reference prices covered in M20 (UCPD; DMCC Act 2024). Corrected: the EU 30-day prior-price rule applies to goods, not services. |

The audit's remediation matrix (§6) maps onto these rows; its trailing-floor fix appears as one option of D42 rather than being adopted, because that choice is yours.

Sources checked on 28 Oct 2026: [EuroCommerce, Price Indication Directive recommendations (Commission guidance on scope)](https://www.eurocommerce.eu/app/uploads/2022/10/2022.10.14-EuroCommerce-Recommendations-Price-Indication-Directive.pdf) · [Max Planck Encyclopedia, Price Indication](https://max-eup2012.mpipriv.de/index.php/Price_Indication). Every other value comes from the project record named in Section 0.
