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

AC07 and AC08, the website's text with figures from the products. In
place for the home page sections hero, challenges and "How it works"
(fixed fields, and up to six steps): an admin or owner proposes new text
in the back office "Website" editor, which previews it filled; a second
admin or owner approves (AC00). `GET /site` (public) gives each approved
section with its figures filled; a section never edited keeps the site's
own words. A figure is written as a token (`{max_split}`, `{min_price}`,
`{max_allocation}`, `{first_target}` and others, listed in the editor) and
filled from the products and policy in force when read, so the site and
the products cannot disagree; text that types an amount or a percentage
is refused. Settings migration 9. More sections (FAQ, about, payouts,
footer, contact) follow the same shape when the portal gains those pages.

AC02, AC13 and AC14, payment and withdrawal methods and their secrets.
In place: a method (payment or withdrawal; provider adapter, enabled,
minimum, maximum, fixed and percent charge, instructions, fields the
trader fills) is attached, changed, enabled or disabled through a
proposal: an admin or owner proposes, an owner who is not the proposer
approves. Back office "Payment methods" screen. `GET /payment-methods`
(public) lists the enabled ones. Once a payment method is attached, a
purchase must name an enabled one whose provider is the checkout's, and
the list price must be within its limits; the portal shop offers the
choice. Charges round up to a whole cent. Secrets are sealed with
AES-256-GCM (key `PROPFIRM_SECRETS_KEY`, outside the database), bound to
their method, shown only as "set", and replaced by an owner-approved
proposal. Settings migration 10. Not yet: withdrawal methods applied to
payout requests (limits and charge at request time) and real provider
adapters (only `sandbox` and `manual` exist; NOWPayments and a card
processor need the operator's accounts).

News impact (decided 8 Nov 2026). In place: the risk desk (one person,
as adding a news window) marks each release high, medium or low impact
(`POST /staff/news/events/{id}/impact`, kept append-only); a release not
marked is high. The public calendar shows the impact in words and that
the release is restricted; marking changes no restriction. Every release
on the calendar is a restricted one: medium and low releases from outside
FTMO's list would need a licensed calendar feed (not chosen).

AC05 settings hub and AC06 Ctrl+K search (8 Nov 2026). In place: a System
settings page (`/settings`, for `settings.view`) with one card per area and
its own search box. Open areas link to their screen (payment methods,
withdrawal methods, security, website, policy pages); General opens a read
only page of the deployment's basics (site name, base currency, trading
day, record time, versions), each with why it is fixed; brand, notification templates, KYC form, SEO, languages,
extensions and maintenance say which rule builds them. The Security page
lists the locked switches from `GET /admin/security`, read only. Ctrl+K
(Cmd+K on a Mac), or the Search button in the top bar, opens a search from
any screen over pages and settings the login may open, and opens a record
from its full reference (PO, CAS, ONB, PRG and 8 digits; CIF opens that
customer's cases). Menu, hub and search read one catalogue
(`backoffice/src/places.ts`). Not yet: searching users, tickets and
transactions by name or amount; it needs the trader lists (AC19), tickets
(AC21) and a transaction log (AC20), none of which exist yet.

AC17 maintenance mode and registration closed page (8 Nov 2026). In place:
the site mode (`maintenance`, `registration_open`), changed by a proposal a
second admin or owner approves (`PUT /admin/site-mode`; settings migration
11), and two website sections, `maintenance` and `registration_closed`,
each with a title, a message and an optional image (a path on the site;
the content security policy loads images only from the site). While
registration is closed, `POST /me` is refused (409 `registration_closed`).
Under maintenance, registration and `POST /orders` are refused (409
`maintenance`), but sign in, trading and payouts carry on, so no trader is
kept from managing an open position. `GET /site/status` is public. The
portal shows the maintenance page to signed out visitors, the closed page
instead of the registration form, and a banner on every page during
maintenance. Not yet: uploading images from the console (a file must be
placed on the site first).

AC12 notification templates (9 Nov 2026). In place: a template per event
(subject and body with variables in braces) and one email wrapper around
every body (`{body}` once), each a setting a second admin or owner
approves (settings migration 12; `GET /admin/notices`, `PUT
/admin/notices/{event}`, `PUT /admin/notice-wrapper/email`). Until wording
is approved, the built-in wording is sent, word for word what was sent
before. The engine refuses a template that could not be sent: unknown
variables, stray braces, a missing required variable (the payout wallet
email must name the new wallet and the time), control characters, over
length. Values are never read as variables. Back office: Notification
templates screen with live preview on sample values. Events today: the
payout wallet security email (the only email the firm sends). Not yet: SMS
and push (no such channel exists yet), and the per-customer notification
history screen (now on AC19's trader file: subjects and delivery state; the outbox already keeps every
email and each send attempt).

AC19 trader lists and file (12 Nov 2026). In place: a Traders screen for
staff who read customer records (an apprentice sees names, emails and
countries masked and searches by CIF only). Tabs by status, each with its
count: all, trading, funded, no account, identity pending, identity
declined, open case, blocked; a search by CIF, name or email; the list
shows accounts, identity status, total paid out and registration time.
One trader's file shows the registration, accounts, purchases, identity
checks, cases, payouts, emails sent (subjects and the latest delivery
state, not bodies) and registration events (`GET /staff/traders`, `GET
/staff/traders/{cif}`). Read only; segments are computed from the records,
never stored. Not covered, because nothing records them here: email and
mobile unverified (Keycloak verifies email at sign up; no phone number is
collected), login history (kept by Keycloak), support tickets (AC21) and
the transaction log (AC20).

AC20 reports (12 Nov 2026). In place: a Transactions and emails screen.
The transaction log lists every purchase (money in) and payout (money
out), newest first, filtered by type, status, trader, remark (product,
discount code, failure) and UTC dates; totals count money once settled (a
purchase paid, a payout paid). CSV export of the filtered log (at most
10,000 rows; money from integer cents, times in UTC; cells that a
spreadsheet would run as a formula are quoted) for staff with full view
only; an apprentice sees the log but does not export. The email log lists
every email sent to traders with its latest delivery state, subjects only
(`GET /staff/reports/transactions`, `GET
/staff/reports/transactions/export`, `GET /staff/reports/emails`).
Proposal and approval history are the Setting proposals, Setting changes
and Security log screens. Not covered: login history (kept by Keycloak;
reading its events is a later step), refunds and chargebacks (no such
records yet).

AC23 system pages (12 Nov 2026). In place: a System information card on
the settings hub, admins and owners only (`GET /admin/system`): the
application version and build (`PIPRIDGE_REVISION`, passed by compose),
Python, PostgreSQL and Valkey versions with whether each answers, when the
API started, and the engine journal's head. Not built, deliberately:
"cache clearing" (no cache holds anything staff could need to drop:
settings, policies and website text are read fresh each time) and
"updates" from the console (code changes arrive by a reviewed pull
request and a redeploy; an update button would let one admin change
running code, against AC00).

AC15 identity checks (10 Nov 2026), decided: provider only, as FTMO and
FundedNext do. Identity checks run at the provider (Didit: photo ID and a
selfie); there is no staff-designed upload form, so no document images are
held by the firm. Staff edit the words traders see while a check is
required, pending or declined: the `kyc` website section (five fields,
approved by a second person; figures refused as in AC08). The attempts
count stays in code. KYC stays on: a funded account cannot open without an
approved check. The settings hub's KYC card opens the Website screen.

AC26 sign in with Google (10 Nov 2026), ready for the firm's keys: set
GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in `.env` (the redirect URI to
register is in `.env.example`) and the start-up setup job
(`config/keycloak/setup.sh`) turns on "Sign in with Google" on the trader
sign-in page. A Google sign-in is followed by the authenticator code (flow
`otp-after-social-sign-in`, recorded as `otp` in the token's amr), so the
second factor is never skipped. Staff pages hide the button. Without keys
the button is off. Apple needs the Apple Developer Program and a Keycloak
extension: not yet.

AC09 statistics (10 Nov 2026), ready for real figures: a `stats` website
section of up to four figures (such as "$500M+", "140+", "4.8/5"), each
with the evidence that backs it (the record and where it is kept). The
evidence is required, seen by staff and approvers, never sent to the
public site. No figures, no strip.

## Exceptions to AC00 decided by the owners (8 Nov 2026)

Decisions that stay with one person: confirming a customer block on a case
(`case.confirm`), adding a restricted news window and marking a release's
impact (`news.desk`). Both
only add restrictions; cancelling a news window still needs an admin.

## Not started

AC09 evidence for claims, AC11 SEO and generated sitemap, AC18 language
manager, AC21 and AC22 (tickets, extensions), AC24 price feed
failover, AC25 subscriber consent, AC26 social login, AC27 soft delete,
AC29 list pattern, AC30 brand tokens.

