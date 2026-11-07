# Back office: admin console rules and their status

Source: the owners' review of the PropLab V2.0 admin (7 and 8 November
2026). Only concepts are adopted, rebuilt as our own; no code, layout,
text or icon mapping is copied. The full text of each rule is in the Prop
Firm project document `claude/backoffice-spec-admin-console.md` (version 3).
This file records what the engine does today for each rule.

Status words: **in place** (done and tested), **partial** (some of it),
**not started**, **not checked** (not yet compared with the code).

## Done in this change (branch p2/two-person-rule)

AC00, two person rule. In place for settings: every settings change is a
proposal until a second person, never its maker, approves it. The policy
file can no longer switch this off (`staff.settings_need_second_admin =
false` is refused). Rejecting needs the same authority as approving.

AC01, owners decide products. In place: a stage-limits change is proposed
by an owner (T5) and approved by the other owner. Admins can neither
propose nor approve it. With Keycloak roles only (no staff directory) there
are no owners, so no product change can be made: the directory must hold
the two owners (`STAFF_OWNERS`).

Stand-in approver (decided 8 Nov 2026). In place: one owner proposes an
admin (T4, never an owner or staff) as the owners' stand-in, for at most
`staff.stand_in_max_days` (30); the other owner approves the appointment.
While appointed, the stand-in may approve a product change in place of an
unavailable owner; never one of their own proposals and never a stand-in
change. Ending it early is the same two-owner decision (a null subject).
API: `GET` and `PUT /admin/owners/stand-in`. Database: settings migration
6 widens the kind check (run against PostgreSQL by the integration tests,
which need Docker).

## Already in the engine before this change

AC03, rule versioning: in place for product rule sets (an account keeps
the version it was bought on; a stage-limits change makes a new version).
Trading conditions carry `trading.config_version`. Advance notice to
traders before a trading-condition change: not started.

AC04, audit trail: in place (hash-chained, anchored, shipped to a SIEM).

AC16, locked security switches: partial. MFA for staff logins and
step-up for approvals are in the policy; locking them on in production
(refusing false, as AC00 now does) is not started.

Payouts, voids and profit removals: two people already (pass and
authorise, 31 Oct 2026). Staff grants and screening: two people already.

AC00 for reviews. In place: an identity (KYC) review and a funded review
are decided by two people. The first approve or deny is a proposal; a
second person, never the proposer, carries it out with the same decision
or rejects the proposal, and the review stays open. Customers migration 9.

AC28 icons. In place: the back office uses Lucide (`lucide-react`, ISC),
one distinct icon for every menu item and resource, at one size and line
weight, always beside its label; Material icons are removed.

AC16, locked security switches. In place: the policy file refuses to
turn off a trader's MFA for payouts, staff login MFA, approval step-up,
pre-employment screening and the two-person rule, or to let staff trade.
`GET /admin/security` lists each switch, its value and what it does. The
web server sends HSTS; the realm keeps a 12-character password policy,
brute-force lockout and SSL for external requests (tested). KYC before
money out holds by design: a funded account opens only after an approved
identity review.

AC10, versioned policy pages. In place: a new version of a page (terms,
privacy, trade policy or any other) is proposed by one owner and approved
by the other (no stand-in); every version is kept and dated
(`GET /policies`, `GET /policies/{slug}?version=n`). A trader must accept
the version in force of every page before buying (`/me/policies/pending`,
`POST /me/policies/{slug}/accept`; `POST /orders` answers 409
`policies_pending`); each acceptance is recorded per trader per version.
The portal shows the pages (plain text, never HTML), links them in the
footer and asks for acceptance on every page until given; the back office
has a "Policy pages" editor. Settings migration 7.

## Exceptions to AC00 decided by the owners (8 Nov 2026)

Two decisions stay with one person: confirming a customer block on a case
(`case.confirm`) and adding a restricted news window (`news.desk`). Both
only add restrictions; cancelling a news window still needs an admin.

## Not started

AC02 payment and withdrawal methods as attachable plugins with owner
approval (today the providers are fixed in code and environment). AC05
settings hub, AC06 Ctrl+K search, AC07 website content manager, AC08
figures bound to product data, AC09 evidence for claims, AC11 SEO and generated sitemap, AC12
editable notification templates (notifications exist; templates are
code), AC13 method plugins, AC14 write-only secrets in the console, AC17
maintenance mode, AC18 language manager, AC19 to AC22 console lists and
reports (partly present: not checked), AC23 system pages, AC24 price feed
failover, AC25 subscriber consent, AC26 social login, AC27 soft delete,
AC29 list pattern, AC30 brand tokens.

