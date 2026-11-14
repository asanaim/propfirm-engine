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
