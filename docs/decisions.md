# Owner decisions

Dated records of the decisions the owners took on the strategic review
(Prop Firm project document `claude/pipridge_strategic_review_v3.md`, 12 Nov
2026). Each record says what was decided and where it stands in the code.
A later decision supersedes an earlier one by a new record that names it;
records are never edited away.

## 12 Nov 2026

Q1, model. Approved: pipridge is completed as a platform and sold or
licensed to a foreign operator. The seller does not operate a firm, host
the platform or take trader money.

K1, payments. Card and crypto payment rails are both built as adapters the
operator switches on. Status: not built (work package WP6).

K2, catalogue. Lite (two step), 1 Step and Instant are sold, with a free
trial; Classic is off. Status: not built (product switches, free trial).

K3, daily loss. The trading day resets at 17:00 New York, from the higher
of balance and equity. Status: in place (unchanged).

K4, repeat discount. None. Status: not built (removal).

K5, instruments. No indices. Status: in place (none are listed).

K6, swaps. Charged per symbol per night. Status: not built (work package
WP3).

K7, Instant floor. Once the trailing floor reaches the starting balance it
stays there; a payout does not restart it. Status: in place from Instant
rule set version 2 (`instant-funded-v2`, `floor_restarts_after_payout =
false`): a payout may not take the account to or below the floor, and the
payable profit is capped to leave it above. Accounts opened on version 1
keep the restart they were sold with.

Q3 to Q7 (legal opinion, first buyer, named buyer, feed budget, IP
signatory): in progress with the owner.

Also decided, for the work order: fix review defects D1, D2, D3, D4, D9 and
D10. D4 with an allocation cap of 400,000 dollars of funded capital per
trader and one active Instant account per trader, and owners or admins
switching each account size (10K, 25K, 50K, 100K, 200K, 400K) on or off.

## 13 Nov 2026

Source: build plan and checklist (Prop Firm project, 13 Nov 2026), section
1, every row approved by the owner; product line version 1 of the same
date. Status on each record is as of `main` at pull request 86.

Status updates to 12 Nov records (the records above stand):

- K2: Classic off and per-size switches are in place (#82). Free trial: not
  built (step 2).
- K4: no repeat discount is in place (#82). A retake discount after a
  breach is decided below (D-13.6); it is a different rule.

D-13.1, profit split. Lite and 1 Step: 80% at funding; 85% after 3 paid
payouts with no confirmed case; 90% from the first scaling step. Instant
fixed at 80%. Website and contract read the figures from the product.
Status: not built (step 2).

D-13.2, scaling. One step when all hold: 10% net profit since the last
step, at least 3 paid payouts, at least 60 days since the last step, risk
class A or B. Each step adds 20% of the starting balance, up to 400,000
dollars of funded capital per trader. A daily job proposes the step; a
second person approves. Status: values in the policy file; job not built
(step 2).

D-13.3, bookkeeping. An operator ledger for income, expense, profit and
loss, beside the trader ledger in the same double-entry engine; no new
dependency. Status: not built (step 3).

D-13.4, Classic. Off at launch; stays a switch. Reopened only if measured
payouts stay at or below 0.65 of FTMO's 2024 level for six months. Status:
off (#82).

D-13.5, two step. Lite is the two step product and the core of the line;
only Classic is off. Status: in place.

D-13.6, retake after a breach. 10% off the list price, one retake at a
time; the figure is an owner setting. Supersedes nothing (K4 still holds:
no discount for a second or third account bought while one is active).
Status: not built (step 2).

D-13.7, reserve. At each sale a fixed share of the fee goes to a separate
reserve account (base model: Lite 72%, 1 Step 66%, Instant 27%). Payouts
are paid from it. Surplus above 1.3 times the computed need returns
monthly. Replaces the rule of 1.8 times monthly sales. Status: not built
(step 3).

D-13.8, short holds. Not an automatic ban. Funded accounts: the share of
profit from trades closed by the trader in under 60 seconds is measured;
above 50% the account moves to class C and the payout is held for review;
two people decide a void. The 60 seconds and the 50% are settings.
Status: not built (steps 1 and 5).

D-13.9, news rule. Follows FTMO in full, funded accounts only: resting
entry orders are cancelled when a window opens; a new order is refused;
any fill inside the window, a stop loss or take profit included, closes
the account and voids the trade. A position opened more than 2 minutes
before the release may be held through it. Swing add-on accounts are
exempt. Supersedes the news exit rule of pull request 6 (exits stand,
profit excluded), which was never merged. Status: refusal of new orders
in place; the rest not built (step 1).

D-13.10, prices. Bid and ask enter the price type now. The Twelve Data
trial measures real delay against a second clock; a second source is
chosen on the trial data. No vendor is named before the trial. Status:
quote age limit and sanity filter in place (#81); bid and ask not built
(step 1); trial waits for a key (step 8).

D-13.11, live execution. Not built. A venue interface and the class A
list are built so a licensed buyer can add it; live execution needs the
buyer's licence and capital. Status: not built (step 9).

D-13.12, platforms. cTrader, DXtrade, Match Trader and MT5 stay options.
A venue interface is built in step 9; no adapter until a buyer commits.

D-13.13, support. Tickets are built into the back office (rule AC21); live
chat goes through the extensions slot (AC22), the operator's choice.
Status: not built (step 6).

D-13.14, tests. A nightly full run in CI with real services, a coverage
figure and a browser test that opens every back office screen; pytest,
Vitest and Playwright only. Status: not built (step 7).

D-13.15, ownership. A proprietary licence notice naming the owner and an
ownership record listing every contributor and AI tool. Status: step 0.

D-13.16, sale to a foreign buyer. The build proceeds; whether the owner
may sell stays with his lawyer. Status: legal work in progress.

D-13.17, product line version 1. Free trial (Lite stage 1 rules, 14 days,
one per identity, no funded account); Lite 10K to 200K at 55, 109, 215,
389 and 779 dollars, 6% then 6% target, 6% static loss, 3% daily; 1 Step
10K to 200K at 95, 199, 299, 499 and 999 dollars, 10% target, 10% loss
trailing at end of day and locked at the starting balance, 3% daily, fee
refunded with the third payout; Instant 5K, 10K and 25K at 109, 199 and
479 dollars, 5% trailing loss locked at the starting balance, no restart,
80% split, one active per trader. 5K evaluations removed. Discount codes
at most 10%, none on Lite 10K. Every value is an owner setting and a
change applies to new purchases only. Status: not entered (step 2).

D-13.18, add-ons. Each a switch with a price in percent of the fee and a
rule change: 90% split from the first payout (Lite, 1 Step; 20%); Swing,
weekend and news holding at leverage 1:30 (Lite, 1 Step; 15%); swap free
for the first 5 nights of a position (all products; 10%). A weekly payout
add-on was proposed and is rejected. Status: not built (step 2).

D-13.19, swaps. Costly side only, never credited; nightly at 17:00 New
York; charge = position value times annual rate / 360. Forex: rate
difference minus 1.0%, floored at zero; gold and silver long: USD rate
plus 1.0%, short zero; oil 4.0% long, zero short; triple on Wednesday;
crypto 15% both sides every night. Eight central bank rates are owner
settings. Markup, oil and crypto rates to be checked against one broker's
published table (step 8). Status: not built (step 1).

D-13.20, 1 Step loss floor. FTMO's version: the 10% end-of-day trailing
floor keeps rising with the highest end-of-day balance and is not capped at
the starting balance (`max_loss_kind = "trailing_eod"`,
`floor_locks_at_start = false`). Supersedes the lock in D-13.17 for 1 Step;
Instant keeps its lock (K7). Status: the fields exist (#92); the product
change is entered in step 2.
Replaced by D-14.10 (14 Nov 2026): the floor stops at the starting balance.

D-13.21, trading hours (review D6). Taken under the owner's instruction to
choose the most sustainable option where no decision exists: New York time;
forex Sunday 17:00 to Friday 17:00; metals and oil Sunday 18:00 to Friday
17:00 with a daily break 17:00 to 18:00; crypto every minute; closed 24 Dec
17:00 to 25 Dec 17:00 and 31 Dec 17:00 to 1 Jan 17:00 for forex, metals
and oil. Outside hours: no new order, close or stop change; pending orders
and stops wait; prices are not applied. Every value is a policy setting
(`[trading.hours]`), to be checked against the price source's hours in the
feed trial (step 8). Status: built (step 1).

D-13.22, closed markets and holidays (amends D-13.21, owner question of
13 Nov 2026 on market practice). On a closed market a pending order cannot
be cancelled either, as MetaTrader at FTMO and FundedNext refuses every
request then. Holidays: metals and oil follow the CME Globex holiday
schedule (halts, early closes, full closures) for the rest of 2026 and for
2027; forex closes for Christmas and New Year's Day only; crypto never
closes. Two 2026 early-close times are assumed from 2027's pattern and
marked [A] in the policy file. Status: built (step 1).
Status update, 13 Nov 2026: product line version 1 (D-13.17, D-13.20) is
entered in the policy file: Lite and 1-Step from 10K at the new prices,
1-Step on FTMO's end-of-day trailing loss without a lock. Still to build
in step 2: the free trial, the split ladder, the add-ons, the retake price
and the Lite 10K code block.

D-13.23, market break rule (build plan step 1, line 5). Taken under the
owner's instruction to follow FTMO and choose the most sustainable option
where no decision exists. Funded accounts (Lite, Classic, 1-Step, Instant)
are made flat before every closed period of 2 hours or more on a market
(the weekend, a holiday halt): 5 minutes before the close their positions on
that market are closed at the last price and their pending orders cancelled,
and in those 5 minutes no new order opens there (`market_break`). The daily
1 hour break of metals and oil does not count. Evaluations hold over breaks,
as FTMO. The Swing add-on (step 2) will lift the rule per account.
Settings: `[trading.hours] flat_before_break_minutes = 5`, `long_break_minutes
= 120`; per stage `flat_before_breaks`. Versions: trading-v8; lite-funded-v3,
classic-funded-v2, one-step-funded-v3, instant-funded-v3 (accounts on the
earlier versions keep them and are not bound). Owner decision, 13 Nov
2026: an automatic close, not a breach. The email first added for it
(#101) is withdrawn by D-14.1: the closing shows in the trader's history
and the rules page explains it. Status: built (step 1).

D-14.1, automatic emails (owner decision, 14 Nov 2026). The firm emails a
trader automatically only on serious matters, not on every event:
- a rule violation or breach that ends or restricts an account;
- a ban, a void or an account closed by staff;
- payments: a purchase paid, a refund or chargeback, each payout stage
  (approved, sent, denied or held);
- a change of payout method or wallet, and other security changes
  (already sent for the wallet).
Routine events send no email: a market-break closing, a trade, a stop, a
pass of an evaluation stage, a trading-day count. Each serious email is
built with the feature that raises it, from a notification template
(rule AC12). Supersedes the email of D-13.23; amends the step 6 line
"notification events" to this list.

D-14.2, news rule as FTMO (build plan step 1, line 6; section 1 news row,
decided 13 Nov 2026). On funded accounts (`news_breaches`, new versions
lite-funded-v4, classic-funded-v3, one-step-funded-v4, instant-funded-v4):
- when a restricted window opens on a symbol, the account's pending and
  trailing entries on that symbol are cancelled (`news_window`); a window
  the engine steps over entirely cancels nothing, since nothing can fill in
  it;
- orders and closes stay refused inside the window, as before;
- a stop loss, take profit or trailing stop that fills inside the window
  breaches the account (`BreachReason.NEWS`): its other positions close and
  it is locked. The trade is not paid out because a breached account is
  not. The integrity case is still raised, so staff see it.
Affected symbols follow the instrument map (CURRENT_MAP, ftmo-1). The
Swing add-on (step 2) will lift the rule per account. Evaluations and the
earlier funded versions keep the earlier behaviour. The trader's email on
the breach follows D-14.1 with the breach emails.

D-14.1 amended (owner, 14 Nov 2026): an evaluation stage passed is emailed
too (template `stage_passed`), with the next step: the Stage 2 account
after a short review, or the identity check and the trader agreement for
a funded account. Written to the outbox in the same transaction as the
progression record, so a pass is never missed or emailed twice.

D-14.3, short holds (build plan step 1, line 8; section 1, 13 Nov 2026).
Not an automatic ban. Per funded account and payout period (since the
last payout, or since funding): the share of profit from trades the
trader closed within 60 seconds of opening. A stop loss or take profit
never counts as short; losses do not offset profit; a partial close is
its own trade. Above 50 percent, a payout request raises one integrity
case for the period (source `short_hold`) and is held while it is open;
staff decide, a void needs two people. The measure is shown on the staff
payout page. Settings `[payouts] short_hold_seconds = 60`,
`short_hold_share_percent = 50` (0: off). If the trade history cannot be
read the request is not held, since staff approve every payout anyway.
The class C step of section 1 follows with the trader classes (step 5).

D-14.4, bad-price corrections (build plan step 1, line 7; review D9, second
half). One person flags an applied price (symbol and price number) with the
price it should have been, and a reason; the system lists each account a
fill at that price touched and the amount that reverses it; a second
person approves (never the proposer). The engine then corrects each
balance (CorrectBalance, signed, once per account and correction) and
reinstates each account the bad price breached (ReinstateAccount: active
and unlocked; only when above its floors once corrected). Positions the
bad price closed stay closed. A correction for a price of today counts in
today's result; for an earlier day it moves the day's base, and a
reinstated account starts today from its current balance.
Amount: a fill's result is scaled linearly to the good price, fills moving
with the mid (spread kept); exact for US dollar quotes, conversion taken as
unchanged for cross pairs; rounded half to even to the cent. Fills that
cannot be scaled (closed at the open price; a position opened at the bad
price still open) are listed for staff to handle by hand.
Not covered: a trailing loss limit's high-water mark raised by a bad
price is not lowered (a reinstated trailing account keeps it); flag such
cases by hand until the trader classes and risk desk exist (step 5).

D-14.5, trader classes, first use (owner: "take the most profitable,
sustainable decision", 14 Nov 2026). An account flagged for short holds
(D-14.3) becomes class C from then on, recorded with its reason. A class
C or D account has every payout reviewed: each payout period raises an
integrity case (source `risk_class`) even when its short-hold share is
below the threshold, so a repeat pattern cannot slip through a quiet
period. Staff set the class back (case.work, with a reason; every change
is kept). Lot caps and the daily classing of step 5 build on this record.
Why: a review costs minutes; an unreviewed payout to a short-hold trader
is paid from the reserve. Classing is not a breach, so a fair scalper loses
nothing but time.

D-14.4 amended (14 Nov 2026), gaps closed:
- A negative correction (a windfall taken back) lowers a trailing loss
  limit's high-water mark by the same amount, never below the starting
  balance. A positive one (a loss given back) leaves it: the floor never
  rises from a correction.
- Price corrections show an account's fills with the price number each
  used ("Find the price number"), so staff need not read the journal.

D-14.6, profit split ladder (build plan step 2, line 3; section 1 decided
13 Nov 2026). Lite, Classic and 1-Step: 80 % at funding; 85 % from the
payout after 3 paid payouts on the order's accounts while no case of the
trader has been confirmed (closed as actioned); 90 % from the first
scaling step (`[scaling] split_after_step = 1`, was 2). Instant stays at
80 %. A rung is never lost by a later case once paid, but the 85 % rung
is not reached while a confirmed case stands. Settings per product:
`split_after_payouts`, `split_percent_after_payouts` (both or neither,
the second above the first split). The contract (template v3) and the
shop state the ladder from the same settings. The scaling rung applies
once the scaling job (step 2, line 4) records steps.

D-14.7, standing rule (owner, 14 Nov 2026). Where a decision is left open,
take the most cost-effective one: lowest cost, profitable for the firm and
sustainable. Record the choice and its reason here.

D-14.5 confirmed (owner, 14 Nov 2026): class C means staff review of every
payout.

D-14.4, trailing loss limit after a correction, confirmed under D-14.7
(owner, 14 Nov 2026). A windfall taken back lowers the high-water mark by
the same amount, never below the start; a loss given back leaves it.
Reason: keeping a floor raised by the firm's own bad price would breach
traders for profit they never had; each such breach is a dispute, a likely
chargeback and a public complaint, which cost more than the payout risk.
The floor never rises from a correction, so the firm's exposure is never
increased by one.

D-14.4 amended (14 Nov 2026): staff no longer look up a price number.
Price corrections lists suspect prices (a move of 0.2 % or more and
straight back) of the last 3 days with the fills each caused; "Use" fills
the form with the price between its neighbours (rounded down) as the good
price. The account search stays as a second way.

D-14.8, scaling job (build plan step 2, line 4; section 1, 13 Nov 2026).
Funded Lite, Classic and 1-Step accounts; Instant is not scaled. One step
when all hold: 60 days since funding or the last step (`review_days`;
section 1 says 60 days, the policy file had 4 months: the approved row
wins), net profit of at least 10 % of the starting balance in the period
(balance now plus payout debits, less the period's opening balance), at
least 3 payouts paid in the period, class A or B (unclassed counts as B),
active and unlocked, and room under the $400,000 trader cap. A step adds
20 % of the account's first starting balance, rounded down to the cent, to
the balance and to the starting balance the loss limits use; the day's base
and the high-water mark move with it. A check runs hourly and proposes; one
member of staff with the progression decision right approves (D-14.7: the
conditions are mechanical, so one reviewer is enough; the engine refuses a
step twice). The split ladder's 90 % rung reads the steps applied. The
contract (v3) states the period in days.

D-14.9, retake price and the Lite 10K code block (build plan step 2;
D-13.6, D-13.17; details taken under D-14.7). A breached account gives one
new account of the same product and size at 10 % off the list price
(`retake_discount_percent`, an owner setting), rounded down to the cent.
Same size only: a cheap account breached on purpose must not buy a discount
on a large one. One retake per breach, and one at a time: no retake price
while another retake order is unpaid in its hold or its account still holds
capital; a failed or lapsed retake order frees the breach. None on Instant
(0 %: its fee must cover a no-skill withdrawal, as with codes). A retake
never combines with a code (codes are for first accounts) and takes the
place of a repeat discount (none on sale, K4). Discount codes are refused
on the sizes in `code_free_sizes_cents`: Lite 10K. The trader sees the
offer in the shop (`GET /me/retakes`). No minimum price per product is set:
no decision asks for one, and with codes at most 10 % and none on Instant
the existing limits already hold the fee above cost.

D-14.10, 1 Step loss floor (owner, 14 Nov 2026; replaces D-13.20). The 10 %
end-of-day trailing floor stops rising at the starting balance
(`floor_locks_at_start = true`), and on the funded stage a payout does not
lower it again (`floor_restarts_after_payout = false`), as on Instant (K7).
New versions `one-step-s1-v4` and `one-step-funded-v5`; accounts sold on
`one-step-s1-v3` and `one-step-funded-v4` keep those rules. Why (the
owner's standing rule: most cost effective, profitable, sustainable): the
product line and its margin were modelled without a restart, while the
code restarted the floor. A simulation of 14 Nov (40,000 traders, typical
losing trader, $100,000 account; an estimate, not a measurement) gives a
payout cost per order of about $163 with the lock and no restart, $150
with no lock and no restart, and $379 with a restart. No lock saves a
little more but leaves the trader less room after every payout. FTMO's own
1-Step has no lock; this is a deliberate difference.

D-14.11, CI minutes (owner, 15 Nov 2026: continue without a paid plan;
D-14.7). The repository is private, so GitHub Actions minutes are billed:
2,000 a month on the free plan, and 1 to 14 November used them all (about
12 minutes a run, run twice per change). CI now runs once per pull request
and not again on the merge to main; the engine (`ci`: test, security),
`portal` and `backoffice` workflows each run only when their own files
change; a newer push cancels the older run; the security job alone also
runs on Mondays, for advisories published since the last change. Expected
use: about 600 minutes a month. Risk accepted: a pull request merged
without updating to the latest main is not retested after the merge. While
minutes are exhausted, every check is run locally before each pull request
and the results are written in its description; the runtime image scan
(Grype) waits for the next run on GitHub. The Actions budget stays at $0.

D-14.12, add-ons built (build plan step 2; D-13.18; details taken under
D-14.7, 15 Nov 2026).
- What: 90 % split from the first payout (Lite, 1-Step; +20 %); Swing:
  no news rule and no market break rule, leverage at most 1:30, an
  instrument's own lower leverage kept (Lite, 1-Step; +15 %); swap free on
  each position's first 5 nights (every product; +10 %). Prices, products,
  the split, the leverage and the nights are policy settings (`[addons]`).
- Price: each add-on is its share of the list price, rounded down to the
  cent. A discount (code, retake, repeat) comes off the list price only,
  never off an add-on: the add-on prices the extra risk, which a discount
  does not reduce.
- Rules: an account with add-ons runs on a derived rule set, the stage's
  id followed by the add-on ids in a fixed order
  (`lite-funded-v4-swing-swapfree`). Every rule outcome names them, and they
  go with the account to each next stage and to funded. Every combination
  is published with each stage, so an admin's change of limits carries them
  too.
- Swap free counts each night of a position, Wednesday's 3 included, and
  charges only the nights past the free ones; other accounts are not
  counted.
- Switches: each add-on is switched on or off with the products and sizes
  (one proposal, a second admin or owner approves); off stops new sales,
  and accounts keep theirs. A screen that sends no add-on switches leaves
  them as they are.
- Contract template v4 names the add-ons bought; with the split add-on it
  states 90 % and no ladder. Template v3's header named itself v2 (a typo);
  v3 is left as issued.
- Not built: the free trial, coupons screen and referral ledger (later
  lines of step 2).

D-14.13, free trial built (build plan step 2; D-13.17, K2; details taken
under D-14.7, 15 Nov 2026).
- What: Lite stage 1 rules on Lite 10K, 14 days, free, one per customer;
  practice only: it never progresses, is never funded, pays nothing and
  ranks nowhere (the leaderboard ranks funded accounts only). Product,
  size, days and the switch are policy settings (`[trial]`).
- Size: 10K, the smallest Lite. Accounts are simulated, so a larger size
  costs the firm nothing, but the trial is meant to sell the 10K first
  purchase, and a 10K trial matches it exactly.
- Rules: its own rule set, the stage's id with `-trial`
  (`lite-s1-v3-trial`), published with the stage, so a trial can never
  be mistaken for a paid account and every outcome names it.
- One per customer: enforced by the database (a unique index on the
  customer's trial orders, a failed one excepted), so two requests at once
  cannot open two. No sanctions screening for a trial: no money moves;
  screening stays at the first paid purchase.
- Recorded as an order at the size's list price with a 100 % `trial`
  discount, price 0, so reports show what was given away.
- Not a first account: a welcome code and the "first account" rules still
  apply to the first paid purchase; a breached trial gives no retake.
- End: an hourly job sends `ExpireAccount` once 14 days have passed since
  opening (looking back 30 days further); the engine closes the positions
  at the current prices and locks the account, status `expired` ("Trial
  ended"). Not a breach, so no email (D-14.1).

D-14.14, coupons screen (build plan step 2; D-13.17; details taken under
D-14.7, 19 Nov 2026). Finance > Coupons lists every discount code with
its state (valid, off, ended, used up), uses, paid uses and the discount
given on paid orders. Staff who may change settings (admins, owners)
create a code and switch it on or off, each with a reason; every change is
kept (who, when, why). A code may have an end date and a use limit; a use
is an order with the code that has not failed, so an unpaid order holds
its use until it fails. A code is capped at the highest code discount any
product allows (10 %); checkout still applies each product's own cap
(none on Instant or Lite 10K) and the first-account rule. One approver,
not two: a code cannot exceed the cap the owners set, so the exposure is
bounded by policy. Accepted: two orders placed at the same instant can
both take a code's last use.

D-14.15, referral ledger (build plan step 2; the commission model was open,
D41 and M27; taken under D-14.7, 19 Nov 2026).
- Every trader has a referral link (`/?ref=CODE`, 8 letters and digits).
  A new customer's code is taken once, at registration, and never
  changes; a refused code does not stop the registration.
- Commission: 10 % of what the referred customer paid for their first
  paid account (after any discount, with add-ons), rounded down; none on
  a free trial and none on repeat accounts (CONFIRMED). One accrual per
  referred customer, available 30 days after payment (holdback: refunds
  and fraud checks). Why: a share of a first purchase costs nothing
  unless a sale happened, and a first purchase is what a referral brings.
- Refused (C8): referrers and referred customers resident in Bangladesh
  or India; a trader's own code; a code of a blocked customer.
- The ledger is append-only; a clawback (refund, self-referral, fraud) is
  a new negative entry linked to the order, with who and why. Staff:
  Finance > Referrals.
- Not built: paying commission out (with the affiliate programme, after
  KYC or KYB, M27); self-referral detection by device or payment wallet
  (M17).

D-14.16, operator ledger (build plan step 3, lines 1 and 2; section 1
"bookkeeping" row approved 13 Nov; details taken under D-14.7, 19 Nov
2026).
- The firm's own books: double entry in US cents, append-only, in the
  customer database beside the trader ledger. The engine's trader ledger
  is unchanged and only the engine writes it.
- Chart of accounts: cash; payouts payable, commissions payable, other
  payables; owner's equity; fee income, discounts and fee refunds per
  product (the last two as contra income); trader payouts, affiliate
  commissions, processor fees and operating expenses by category
  (hosting, market data, software, identity checks, legal, marketing,
  staff, other).
- Entries from the records, each with a fixed id so a sync posts once:
  a paid order (cash in the price; income at list price plus add-ons;
  the discount as contra income; a free trial posts nothing); a payout
  as an expense when the engine debits it and as cash when it is paid
  (so the accrual and cash views of step 3, line 3, both read the book);
  a referral commission and its clawback. The sync runs at most once a
  minute.
- Operating expenses: one member of staff with the settings right
  proposes, a different one approves; approval posts the entry at once,
  against cash (paid) or payables (owed). Processor fees are entered this
  way until the payment adapters report them (step 4).
- Not yet: the income statement, liability desk, reserve, reconciliation,
  refunds and chargebacks (step 3, lines 3 to 7).

D-14.17, income statement (build plan step 3, line 3; details taken
under D-14.7, 19 Nov 2026). Read from the operator ledger (D-14.16), by
calendar month in UTC and by product, with every product together as
"all". Months are UTC: the books are kept in UTC like every time in the
engine, and a month in New York time would move each month's edge by
hours with the clocks; the trading day's 17:00 New York reset is not an
accounting period.
- Accrual view, when earned or owed: fees (list price plus add-ons),
  less discounts and fee refunds, is net revenue; less trader payouts
  (when the engine debits them) and affiliate commissions (when
  accrued), is contribution per product; less operating expenses (of no
  product), is profit.
- Cash view, when paid: received from orders, payouts paid, commissions
  paid, expenses paid, net cash. Commissions are not paid out yet
  (D-14.15), so that line stays 0 until they are.
- The product of an entry comes from its source record (the order, or
  the payout's order), so the ledger stores no product column.
- CSV of every product in the range, amounts in dollars with two
  decimals. Up to 120 months per request.

D-14.18, liability desk (build plan step 3, line 4; details taken under
D-14.7, 19 Nov 2026). What the firm owes or may owe traders, read on
demand, nothing stored:
- Certain: payouts approved, taken from the account or sent and not yet
  paid; plus commissions payable from the operator ledger.
- Open: payouts requested and not yet decided.
- Expected: for each active funded account, what it would receive if it
  asked now, by the payout rules' own amount (share, fee refund,
  maximum), with the eligibility checks (days, wallet, identity)
  ignored, because a claim not yet due is still a claim; less any payout
  of the account already counted above and still in its balance, so no
  claim is counted twice.
- The largest single claim and the 20 largest, by account. An account
  whose claim cannot be read is listed, not silently left out.
- Who: the settings view right (admins, owners), as the books. Payout
  staff do not see the firm's totals.
- Reading every funded account on each request is acceptable at launch;
  a cached figure comes with the reserve (line 5) if it grows slow.

D-14.20, payout reserve and coverage K (build plan step 3, line 5; D-13.7
built; details taken under D-14.7, 20 Nov 2026; formula from strategic
review v3, D3).
- Sweep: at each paid sale the product's share of the price (Lite 72%,
  1 Step 66%, Instant 27%; `[reserve]` in the policy file) moves from
  cash to the reserve account in the operator ledger, rounded up to the
  cent. A free trial sweeps nothing. The share is read when the books
  sync (at most a minute after the sale) and the amount is recorded in
  the entry. Classic is off sale and has no share; it needs one before
  it opens (the policy check refuses a product on sale without one).
- Draw: a paid payout is drawn from the reserve in full. A reserve drawn
  below zero is shown as a shortfall owed to it, not hidden.
- Requirement R* = owed on closed profit (the liability desk's certain,
  open and expected tiers) + 0.5 x the traders' share of open profit +
  1.5 x expected payouts on active evaluations (price x sweep / 1.25) +
  the most one customer could claim now. K = reserve / R*. The weights
  and thresholds are policy values.
- States: normal at K >= 1.3, amber from 1.1, red from 1.0, breach below.
  K is shown rounded down to two decimals; R* is rounded up to the cent.
- Shown on the liability desk to the settings view right, with what
  could return to operating cash above 1.3 x R*.
- Simplifications, each on the safe side or flagged: one customer stands
  in for an identity cluster until clusters exist (step 5); no cohort
  decay is applied to evaluations, which overstates the requirement; the
  reserve is a ledger account, and moving the money to a separate bank
  account is an operating task.
- Not yet: the automatic response of each state (amber: no promotion
  below list, scaling frozen, Instant capped; red: new funded accounts
  queue, owners alerted, repricing proposal; breach: published delay),
  owners' capital top-ups, and the monthly release of the surplus. They
  follow as their own pull request.
D-14.19, staff and trader sign-ins kept apart (owner, 19 Nov 2026: the
back office accepted a trader's login and the portal a staff login;
"follow the standard and safe protocol").
- Separate entry points stay: the trader portal and the back office are
  two apps with two Keycloak clients and, in production, two addresses.
  One login page that sends each user to the right app by their ID was
  considered and not taken: it puts the staff sign-in on the public
  site, where phishing and password guessing aim, and shares one session
  between the two. Workforce and customer sign-ins are kept apart as
  standard practice.
- The API binds each token to the app it was issued to (OIDC `azp`):
  portal tokens reach only trader endpoints, back-office tokens only
  staff endpoints; anything else is refused with `wrong_app`. Clients
  per app are settings (`OIDC_PORTAL_CLIENTS`, `OIDC_OFFICE_CLIENTS`);
  `dev-cli` is listed for development and absent from the production
  realm.
- The back office always asks for the password and second factor
  (`prompt=login`), so a Keycloak session opened in the portal is never
  reused there; a login that is not staff (`not_staff`) is signed out at
  once and told to use the trader portal. The portal tells a staff login
  that staff do not hold accounts there (the existing ban, 6 Nov 2026).
- Not done here, and recommended before launch: a separate Keycloak
  realm for staff, so the two user lists and sessions share nothing.
  It needs a running Keycloak to build and test, which this workspace
  lacks; the binding above already closes the hole at the API.

D-14.21, reconciliation (build plan step 3, line 6; details taken under
D-14.7, 20 Nov 2026). A processor's statement matched line by line to the
firm's records.
- Input: a CSV uploaded by staff with the settings change right, for one
  provider and one period, saying whether it covers money in (sales),
  money out (payouts) or both. Columns: date (ISO 8601 with a zone),
  reference, direction, amount, currency, optional fee. Amounts are
  dollars with at most two decimals, never rounded. One neutral format,
  not each processor's own: no processor is contracted yet (step 4), and
  its export columns would be guesses. A converter per processor comes
  with its adapter.
- Refused whole, with the line numbers, when any line is bad (outside the
  period, a direction not covered, a third decimal, no time zone). The
  same file twice for one provider is refused.
- Each line gets one status: matched, amount differs, not settled,
  unmatched, repeated. Money in matches an order by its provider and
  provider reference; money out a payout by its provider reference.
  Missing: paid orders of the provider and paid payouts in the period
  that the statement leaves out.
- Stored append-only (`recon_runs`, `recon_lines`, migration 22); to
  match again, upload again. Read by the settings view right.
- Amounts are compared in cents as written, whatever the currency; a USDT
  line is compared at par with the dollar record [A]. Fees are shown per
  statement; posting them as processor fees stays a staff expense entry
  (D-14.16) until the adapters report fees.

D-14.22, refunds and chargebacks (build plan step 3, line 7; details
taken under D-14.7, 20 Nov 2026; FTMO practice: a chargeback ends the
account).
- Refund: proposed by one member of staff with the settings change right
  (an amount and a reason), approved or rejected by a different one.
  Approved and proposed refunds plus open and lost chargebacks never
  exceed the price paid; no refund while a chargeback on the order is
  open. A refund in full ends the order's account if active (the engine
  closes its positions and locks it; not a breach) and takes back any
  referral commission. A part refund (goodwill, an add-on) leaves the
  account alone.
- Chargeback: recorded by staff until the payment adapters report
  disputes (step 4), with the processor's case id, amount, fee and the
  day the money was taken. At once, without a second person, because a
  restriction never waits: a high-severity fraud alert on the customer's
  case (which holds payouts, funded reviews and contracts while open),
  the order's account ended, the referral commission taken back, and
  new purchases refused while it is open or lost. A different member of
  staff records the outcome, won or lost; won allows purchases again.
  The case is decided on its own four-eyes path; an ended account is not
  reopened automatically.
- Books: `refund:<RFD>` (refunds, contra income, against cash);
  `chargeback:<CBK>` (chargebacks, contra income, and the processor fee
  as an operating cost, against cash); `chargeback-won:<CBK>` (the amount
  back; the fee is not). Both lower net revenue; the cash view has
  refunds paid and chargebacks lines.
- A referral commission no longer accrues on an order refunded or
  charged back before it accrued.
- Not changed: the reserve keeps the sweep of a refunded order (the safe
  side; the monthly release returns any surplus).

D-14.23, reserve states acted on (completes build plan step 3, line 5;
details taken under D-14.7, 20 Nov 2026; responses from strategic
review v3, D3).
- Watch: the API loop computes coverage K at most once a minute and
  records each change of state (append-only `reserve_states`), alerting
  the settings change audience (urgent in red and breach); a return to
  normal closes the alert. Requests read the last recorded state, never
  the desk. With the reserve off the state is normal.
- Each state adds its limits to the ones before:
  - amber: discount codes refused at checkout (`promotions_paused`);
    scaling neither proposed nor approved; Instant sales capped per UTC
    day at the trailing 30 day daily average, rounded up, at least one.
    Retake and repeat prices are product rules and stay.
  - red: Instant sales stopped (`sold_out_today`): a paid Instant order
    would open a funded account at once, so it is not sold rather than
    sold and queued; passed traders' funded accounts queue after
    signing and open by themselves when the state improves. The alert
    names the product with the highest share of its 90-day sales paid
    out in the same days, for repricing.
  - breach: a payout delay notice on the trader site (`/site/status`,
    the Payouts page), with the date it began. Payouts continue in
    request order, as they already are.
- Top-ups (owners' capital into the reserve: reserve against owner's
  equity) and releases (reserve back to cash, at most what lies above
  1.3 x R*, checked again at the decision) are proposed by one member of
  staff with the settings change right and decided by another; the
  books post them.
- Not built: class C payouts reviewed by hand in amber (trader classes
  come in step 5); a generated repricing proposal (the alert names the
  product; the price change goes through the settings proposals as now).

D-14.24, payment rails (build plan step 4, lines 1 and 3; details taken
under D-14.7, 20 Nov 2026; vendors from the functional outline: USDT on
TRC-20 through NOWPayments, no cards until a prop-specialist processor
at expansion).
- Interface: a rail creates an invoice on the provider's hosted page (no
  card or wallet data reaches pipridge) and reads the provider's signed
  notices as one event: paid, refunded, disputed, or nothing. Each
  payment method names its rail and holds its secrets (AC13, AC14), so
  the method is the switch in the back office. An order records its
  method as its provider; webhooks arrive at
  `/webhooks/payments/<method>`.
- Paid opens the account once, however often the notice comes, and only
  for the order's exact price; another amount opens nothing and alerts
  finance. A dispute is recorded as a chargeback at once (D-14.22). A
  refund reported by the provider alerts finance to record it; refunds
  are not sent through the rails (NOWPayments has no refund call).
- NOWPayments: invoices in USD, paid in `usdttrc20` only (the 1%
  single-currency fee, vendor-stated, not the 1.5% multi-currency one);
  notices checked by HMAC-SHA512 over the key-sorted JSON, as JavaScript
  writes it (checked against Node), compared in constant time; only
  `finished` is money received. Its sandbox API is used when
  PROPFIRM_SANDBOX=1. Not yet proven against the vendor's sandbox: that
  needs the operator's account (C6) and network access this workspace
  does not have.
- Manual (bank transfer or any payment checked by hand): the order shows
  the method's instructions and its own number as the reference; one
  member of staff records the receipt (exact price, bank reference), a
  different one confirms it, and only then is the order paid. Off until
  an owner attaches such a method; the launch configuration has no bank
  wire.
- Production purchases need a method once the sandbox is off.
- Not built: the card adapter (no processor chosen; cards come at
  expansion); payout rails without a fixed network and withdrawal
  limits and charges (step 4, lines 4 and 5).

D-14.25, payout rails and withdrawal methods (build plan step 4, lines 4
and 5; with the open points of D-14.24 settled by the universal rule,
owner's instruction of 20 Nov 2026: "take the most suitable decision").
- Payout interface: a payout order carries the withdrawal method, a
  destination as the method needs it (for USDT a network and an
  address), the amount that reaches the trader and the currency. No
  network is fixed by the interface; the only destination stored today
  is a USDT TRC-20 wallet, as the functional outline confirms. Another
  network needs its own wallet type first.
- Withdrawal methods at request time (rule AC13): once any is on, a
  payout must name one; the trader's amount must be inside its limits;
  its charge (fixed plus percent, rounded up to the cent) is taken from
  the amount and recorded on the payout. With none on: the sandbox in
  development; in production the request is refused until an owner
  attaches one.
- Rails at launch: `manual`. After two approvals and the engine's debit,
  finance sends the net amount from the firm's wallet or the
  NOWPayments dashboard and records the transaction; the payout is paid
  at the next round. Automatic NOWPayments payouts are not built: their
  payout call needs a second-factor code per batch (a person's act) and
  an API key able to move funds, which a server should not hold at
  launch volumes. Revisit when payouts exceed what one person sends in
  a day.
- Books: cash out is what was sent; the charge, kept by the firm, lowers
  the payout cost; the reserve is drawn by what was sent.
- Settled points of D-14.24: no card processor at launch (the outline's
  choice; Stripe-class processors refuse prop firms, and a high-risk
  processor's fees and rolling reserve cost more than crypto at launch
  volumes); crypto through NOWPayments once the operator opens the
  account (owner action, C6); bank transfer stays off.

D-14.26, payout networks as gates (owner, 20 Nov 2026: "create some more
gates for next addition", on the flag that payouts could only go to USDT
on TRC-20).
- Built, each with its own address check: TRC20 (Base58Check), ERC20,
  BEP20 and POLYGON (0x and 40 hex digits; a mixed-case address must
  carry its EIP-55 checksum, computed with Keccak-256 written here and
  checked against published vectors). All USDT.
- The gate is a policy switch, `[payouts] networks`, changed by a
  settings proposal: only TRC20 is open, as the functional outline
  confirms. Open a network only once finance can send on it.
- A trader picks the network with the address; the wallet keeps it, the
  payout order carries it, and the transfer page shows it. A network
  closed later takes no new wallets; wallets on it are still paid.
- Another network (Solana, a second coin) is code: its address check and
  its name in `customers.networks`, then the switch.

D-14.27, identity clusters (build plan step 5, line 1; thresholds from
strategic review v3, D4, as starting values; details taken under D-14.7,
20 Nov 2026).
- Links between two customers, each with its evidence, looked for at
  most hourly by the API loop:
  - identity: the duplicate identity alert raised at funding (same date
    of birth and name as another verified customer);
  - wallet: the same payout address on the same network (EVM addresses
    compared without case); a new link raises a medium fraud alert on
    both customers, which opens a case and holds their payouts until
    staff decide;
  - trades: at least 5 positions opened within 2 seconds of one another
    on the same symbol, either side, in the last 30 days.
- A cluster is the connected set; its id is its lowest CIF, so the same
  links always give the same clusters. Links are derived and
  rebuildable, so a link's evidence and last sighting are updated.
- Used now: the reserve's largest claim of one trader (D-14.20) sums a
  cluster. Next: the allocation cap and the Instant limit (D2 of the
  review), and the detectors (line 3) read clusters.
- Not yet: device and payment instrument links. The platform keeps no
  device fingerprint, and hosted payment pages give it no card or payer
  wallet. Each comes with its source.
- Read by staff with case work rights (Identity clusters page).

D-14.28, daily trader classes (build plan step 5, line 2; thresholds from
strategic review v3, D5, as starting values in `[classes]`; decided by the
owner's rule of 20 Nov 2026, "take the most suitable decision").
- Built on D-14.5: one record, `account_class_changes`; no second table.
  The daily job (API loop, once a day) classes every active funded
  account and records a change only when the class or its reasons
  change, set by "system", the reason "daily: <reasons>; <measures>".
- A, proven: 60 trading days, 200 trades, t statistic of daily results
  above 2, profit factor above 1.3, 3 payouts paid, under 20% of profit
  from short holds, no case ever actioned. Tested exactly in fractions.
- C, watch: over 50% of profit from holds under 60 seconds; a chargeback
  not won; a trades link in an identity cluster (D-14.27).
- D, restricted: a case on the customer actioned in the last 90 days.
- B: everyone else, and every account with no record. D wins over C, C
  over A.
- A person's decision stands: a class set by staff or by the payout
  check is changed by the job only to a worse class on a fact newer than
  it (a chargeback, a cluster link, an actioned case). Short holds carry
  no date, so they never override staff. The job lifts only classes it
  set itself.
- In force now for C and D: every payout reviewed in a case and no
  scaling (D-14.5, already built); no Instant purchase (403
  `instant_not_offered`; an evaluation is still sold).
- Not yet, flagged:
  - lot caps halved for C and close only for D need an engine input
    journaled with the risk policy version (line 6);
  - A's lighter payout review and first place for scaling widen the
    baseline; they need a second person's approval (D10 of the review);
  - markout and news window measures need the second price source and
    the release calendar join.
- Read by staff with case work rights (Trader classes page).

D-14.29, behaviour detectors, first: hedging across accounts (build plan
step 5, line 3; thresholds from strategic review v3, D6, as starting
values in `[detectors]`; decided by the owner's rule of 20 Nov 2026).
- The detectors run in the API loop at most hourly. Each finding raises
  one high fraud alert per account (keyed, never repeated). The alert
  opens a case on the customer, so their payouts are held until staff
  decide. Voiding needs two people (the case rules).
- Hedging across accounts: two positions on one symbol, on opposite sides,
  on two accounts of one customer or of customers in one identity
  cluster (D-14.27), opened within 120 seconds of each other, and open
  together for at least half the shorter position's holding time. Three
  such pairs on one pair of accounts in 30 days make a finding. A
  position still open counts as held until now. The review's "overlapping
  half the holding time" is read as half the shorter hold. That is the
  strict reading: a quick hedge inside a long position still counts.
- Order of the remaining detectors, by expected loss, with what each
  waits for:
  - stale price: the second price source (step 8);
  - copy cluster: already covered by the trades link (D-14.27) and class
    C (D-14.28);
  - account sharing: device and sign in records, which the platform does
    not keep yet;
  - gap trading, consistency gaming, request rate and payment risk: next,
    from records the platform already has.

D-14.30, gap trading and consistency gaming detectors (build plan step 5,
line 3; thresholds from strategic review v3, D6, as starting values in
`[detectors]`; decided by the owner's rule of 20 Nov 2026).
- Gap trading: a profitable closed position counts if either holds:
  - it was opened at most 2 hours before a long market break (the market
    break rule's, 120 minutes or more) and closed after the market
    reopened;
  - it was opened within 5 minutes of a listed release affecting its
    symbol (either side, inclusive; the news rule's symbol map), at a
    volume above twice the account's median volume over 30 days.
  Each such position raises one low integrity alert. Staff may remove the
  profit (two people). Positions at a loss are not flagged: they cost the
  firm nothing. Releases are read from the news calendars, so a release
  added by hand on the news desk is not seen yet.
- Consistency gaming: one trade (same account, symbol and side) closed
  before the 17:00 New York boundary and opened again within 60 seconds
  after it. Three in 30 days raise one medium integrity alert a month per
  account.
- The payout's consistency check merges the two trading days of every
  re-entry, whatever the count. A re-entry within a minute is one trade
  split by the clock, so merging is fair to an honest trader and closes
  the gain to a gaming one. This applies only to rule sets that have the
  consistency rule, and only while the detector is on.
- Remaining detectors: request rate and payment risk.

D-14.31, request rate, and payment risk closed for now (build plan step 5,
line 3, last part; review v3, D6; decided by the owner's rule of 20 Nov
2026).
- Request rate:
  - every order request counts: placing an order, a trailing entry, a
    cancel, a close, stops and a trailing stop;
  - the count is per account per trading day (17:00 New York);
  - the API refuses request 2,001 and every one after it that day with
    429 `request_rate`; the portal shows the message;
  - the first refusal of a day is recorded (`rate_days`, migration 29);
  - the second throttled day within 30 days raises a medium integrity
    alert, which opens a case.
- The count is kept in memory in the API process. That is free and right
  for the one API process at launch. With more than one process, the
  limit applies per process: move the count to Valkey before scaling out.
- Payment risk, part by part:
  - chargebacks: already handled by D-14.22. A chargeback ends the
    account, holds purchases, claws back commission and opens a case;
  - declared country against IP country, and declared country against the
    identity document: already gated at registration and KYC (country
    gating);
  - card country and one card on more than 2 identities: these need card
    data, and there is no card processor at launch (D-14.25). They wait
    for the card adapter, which must supply the card's country and a card
    fingerprint;
  - the same payout wallet on two identities: already a cluster link
    (D-14.27).
- Line 3 is done, except stale price (needs the second price source, step
  8) and account sharing (needs device and sign in records).

D-14.32, exposure desk (build plan step 5, line 4; strategic review v3,
D4, starting values in `[exposure]`; decided by the owner's rule of 20 Nov
2026).
- Every 5 minutes the API loop revalues each active funded account with
  open positions, exactly as the engine values them: P&L in the quote
  currency converted to USD, with the conversion rate moved too when the
  scenario moves its symbol. Equity is floored where the account's loss
  limit would close it.
- The firm's added liability in a scenario is
  sum s x [(E^k - I - V)+ - (E - I - V)+], using the payout rules' own
  split, baseline and violations. The worst case is rounded up to the
  cent.
- Scenarios:
  - each held symbol at plus and minus 1 and 3 standard deviations of
    the last 60 daily returns (from the projected daily bars; rounded up
    to a basis point, exactly);
  - with under 20 days of history, plus and minus the asset class stress
    move (forex 1.5 %, metal 3 %, oil 6 %, crypto 10 %);
  - blocs: USD 1.5 % with gold 3 % the same way as CUR/USD; crypto 10 %;
    oil 6 %; each both ways.
- Limits, on the free reserve (reserve less certain liability): one
  symbol 25 %, a bloc 50 %.
  - At 80 %: the risk desk is alerted (settings change audience).
  - At 100 %: lot caps halved for classes B and C on that symbol and the
    side that wins.
  - At 150 %: lot caps quartered for all classes.
  - Restored after 24 hours continuously under 60 %.
  - Open positions are never touched. Class D is already close only.
- With the reserve off, scenarios are shown and nothing is limited.
- Records: `exposure_moves` (migration 30, append-only) holds every
  raise, under, over and restore, with the settings version it was
  decided under. The cuts in force are a replay of it. Settings gain
  `version()`.
- Not yet: the engine does not apply the cut levels. That needs a lot cap
  input journaled with its policy version (step 5, line 6). Until then
  the levels are shown to staff on the Exposure page, and staff can lower
  caps by a settings change.
- Simplification: a symbol's scenario moves only that symbol, plus the
  conversion rate it is itself the route for. Correlation between symbols
  is captured only through the blocs.

D-14.33, cohort alarm (build plan step 5, line 5; strategic review v3, D3
and D6; starting values in `[cohorts]`; decided by the owner's rule of 20
Nov 2026).
- Zero edge bounds per product:
  - Lite 25 %, 1 Step 37.5 % and Instant 36.8 %, from the pricing study;
  - Classic 27.35 %: 8/18 x 8/13 for its two static loss stages. Its
    daily loss limit is ignored, so this is an upper bound and alarms
    less often.
- Cohorts, one result per order:
  - each product's orders bought in a calendar month (UTC);
  - each affiliate's referred orders of a product, all time.
- Results:
  - a challenge passes when its last evaluation stage passes. This is
    counted at the pass, not at funding, which waits on a review;
  - it fails when an evaluation account is breached or expires;
  - Instant succeeds when its trailing floor locks at the starting
    balance before any breach;
  - running and voided orders are not results.
- Alarm: at least 10 results, a rate above the bound, and an exact one
  sided binomial tail of 1 % or less, computed in integers. Checked every
  6 hours.
- On an alarm:
  - every account of the cohort fails the new payout check "cohort";
  - for an affiliate, commission stops accruing, and the orders left out
    accrue once cleared;
  - a high fraud alert opens a case on the affiliate, where two people
    may end the partnership;
  - the risk desk is told.
- Staff with case rights clear an alarm with a reason (Cohort alarm
  page). Alarms and clears are append-only (migration 31).
- A cleared cohort alarms again only after 10 more results.
- Instant lock is read from the daily reset and breach events. A lock
  inside the day counts from the next 17:00 New York reset.

D-14.33a, cohort alarm flags resolved (owner, 28 Nov 2026: "solve all the
flag").
- The Classic bound is exact, not loose. With zero edge, equity is a
  martingale stopped at the target or the loss limit, so a stage passes
  with probability at most L / (T + L) under any strategy (optional
  stopping). A daily loss limit only adds more ways to stop. Classic has
  no time limit, so a trader can keep daily risk small and approach the
  static figure as closely as they like. The supremum is therefore
  8/18 x 8/13 = 32/117 = 27.35 %. The same argument fits the other
  bounds.
- An Instant lock inside the day now counts at once. For a running
  Instant account with no lock in its events, the live view's floor is
  read; a lock is never undone.
- Clearing an alarm needs two people. One asks with a reason; a second,
  different person confirms (409 `same_person` otherwise). Requests are
  append-only (migration 32). Payouts stay held until the confirmation.
D-14.34, automatic risk limits journaled and replayed (build plan step 5,
line 6; decided by the owner's rule of 20 Nov 2026).
- One engine input, `SetRiskLimits`, carries the whole set: each account
  not in class B with its class, the percent of lot caps each class keeps
  (C 50 %, D 0 %, meaning close only), and every exposure cut (symbol,
  side, percent, classes). It also carries the settings version it was
  decided under. Each set replaces the last. The engine records
  `RiskLimitsSet`, and the set is part of its state snapshot, so replay
  and restart give the same refusals.
- The engine applies the set to new exposure only: market, pending and
  trailing entries at placement.
  - The lot caps are scaled to the smallest percent that applies (a class
    percent, or a cut listing the account's class), rounded down to whole
    hundredths of a lot. Two reasons do not compound.
  - Refusals: `risk_limit` (within the normal caps, above the cut) and
    `close_only` (percent 0).
  - Closes, stop changes and open positions are never limited.
  - A working order placed before a cut fills as placed, because the
    normal caps are rechecked at the trigger but the cut is not.
- The API loop builds the set every minute from the classes in force
  (D-14.5, D-14.28, staff or system) and the exposure cuts in force
  (D-14.32: halve leaves 50 % for B and C, quarter 25 % for all). It
  sends the set only when it changed, and once after start.
- This resolves the flags of D-14.28 (lot caps for C, close only for D)
  and D-14.32 (cuts not applied). The portal explains both refusals.
- Step 5 is complete except stale price (needs the second price source,
  step 8) and account sharing (needs device and sign in records).

D-14.35, account sharing detector (build plan step 5, line 3; strategic
review v3, D6; decided by the owner's rule of 20 Nov 2026 and the request
of 28 Nov 2026, "complete all of step 5").
- Sign ins: a trader's sign in is one authentication of their login (the
  token's `auth_time`) on one device. The API records it the first time
  it sees it (migration 33, append-only):
  - the device: a 32 character hash of the portal's random id (header
    `X-Device-Id`, kept in local storage), or of the user agent when the
    browser keeps no storage;
  - the country (`PROPFIRM_COUNTRY_HEADER`) and the rough place
    (`PROPFIRM_LATITUDE_HEADER`, `PROPFIRM_LONGITUDE_HEADER`, rounded to
    0.1 degree, about 11 km) set by the proxy. On Cloudflare these are
    `cf-ipcountry`, `cf-iplatitude` and `cf-iplongitude`, from the free
    "Add visitor location headers" managed transform;
  - never the IP address. Recording never fails a request.
- Rules over the last 7 days, checked at each new sign in:
  - more than 3 devices, or more than 3 countries: a medium fraud alert;
  - two sign ins over 500 km apart within an hour (great circle): a high
    fraud alert.
- One alert per rule per 7 days. The alert opens a case, which holds
  payouts.
- Each alert also records a step up: the customer's next payout request
  or wallet change needs a second factor entered after it (401
  `reauth_required` with max_age=0, which the portal turns into a fresh
  sign in).
- Staff with case rights read a customer's sign ins and last step up:
  `GET /staff/customers/{cif}/sign-ins`.
- Without the location headers the travel rule cannot fire; the device
  and country rules still work.

D-14.36, stale price detector and the reference price source (build plan
step 5, line 3; strategic review v3, D6; decided by the owner's rule of 20
Nov 2026 and the request of 28 Nov 2026, "complete all of step 5").
- Reference source: OANDA's v20 price stream (bid and ask). The practice
  server is free with a demo account, which is the lowest cost
  available; a paid vendor can replace it by writing to the same window.
  - It covers forex, metals and oil. Crypto is not on OANDA's global
    practice server, so crypto fills are not checked yet.
  - The connector (`python -m propfirm.service.reference`, compose
    profile `reference`) keeps each symbol's quotes for 10 minutes in
    Valkey and trims the rest.
  - These prices never reach the engine or price anything. Rounding widens
    them: bid down, ask up.
- Check: every minute the API loop takes the fills of the last minutes
  that the trader timed (market opens and client closes; stops, limits
  and forced closes are left out). Each is compared with the last
  reference quote at or before it, at most 2 seconds older.
  - A fill better for the trader than the quote's same side by more than
    one reference spread is stale.
  - It is kept as evidence with the quote (`stale_fills`, migration 34;
    never updated; deleted only by the reference purge).
- Action: 5 stale fills on an account in 30 days raise a high fraud alert
  (a case holds payouts) and set class C as the system, so lot caps are
  halved in the engine (D-14.34) and the daily job does not lift it. Two
  people decide a void or a profit removal on the case.
- A fill outside the 10 minute window cannot be checked, for example
  when the API was down. It is logged, not guessed.
- Purge, within 30 days of cancelling:
  `python -m propfirm.service.reference --purge --confirm "purge reference prices"`
  deletes the window and the stale fill evidence.
- Owner action, flagged:
  - create an OANDA practice account and set `OANDA_TOKEN` and
    `OANDA_ACCOUNT`; without them the check finds no quotes and does
    nothing;
  - OANDA's API licence is written for the licensee's own trading. Use as
    an internal reference that is never shown or redistributed should be
    confirmed with OANDA, or replaced by a paid data licence, before paid
    launch.
