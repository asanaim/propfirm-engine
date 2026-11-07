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

## Gaps against AC00 found in the code

These decisions are still made by one person and must become proposal
and approval: identity (KYC) accept or reject (`onboarding.decide`),
funded review approve or deny (`progression.decide`), case block
confirmation (`case.confirm`), and the risk desk's news windows
(`news.desk`; cancelling already needs an admin).

## Not started

AC02 payment and withdrawal methods as attachable plugins with owner
approval (today the providers are fixed in code and environment). AC05
settings hub, AC06 Ctrl+K search, AC07 website content manager, AC08
figures bound to product data, AC09 evidence for claims, AC10 versioned
policy pages with re-acceptance, AC11 SEO and generated sitemap, AC12
editable notification templates (notifications exist; templates are
code), AC13 method plugins, AC14 write-only secrets in the console, AC17
maintenance mode, AC18 language manager, AC19 to AC22 console lists and
reports (partly present: not checked), AC23 system pages, AC24 price feed
failover, AC25 subscriber consent, AC26 social login, AC27 soft delete,
AC29 list pattern, AC30 brand tokens.

AC28 icons: the back office uses Material icons (`@mui/icons-material`,
Apache 2.0), one distinct icon per menu item with its label; the portal
uses Lucide. Moving the back office to Lucide is a design choice for the
owners, not started.
