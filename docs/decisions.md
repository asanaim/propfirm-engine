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
