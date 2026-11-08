# Keycloak realm (development)

`propfirm-realm.json` is imported when Keycloak starts with an empty
database (`docker compose --profile platform up`). It defines:

- roles `trader` (every self-registered user), `staff` (employees) and
  `admin`;
- clients `portal` and `backoffice` (browser login with PKCE) and
  `dev-cli` (password grant, for tests and curl only). A portal token
  carries only `trader`; a back-office token carries only `staff` and
  `admin`; so each app gets its own view of the API (see
  `src/propfirm/api/app.py`). A `dev-cli` token carries every role the
  user has;
- every access token carries the audience `propfirm-api`, which the API
  requires;
- browser login (`browser-with-amr`, the default browser flow with two
  settings): every token says how the user signed in (`amr`: `pwd`, and
  `otp` when the authenticator code was entered) and when (`auth_time`).
  The API reads both for wallet changes and payout requests (step-up). An
  authenticator app is optional at login; once set up (the portal's
  Security page, Keycloak action `CONFIGURE_TOTP`) it is asked for at every
  login. The `amr` references last as long as the SSO session (36,000 s);
- four test users: `trader1@example.com` / `dev-trader-password`,
  `trader2@example.com` / `dev-trader2-password` with an authenticator
  (TOTP secret `dev-totp-secret-trader2`, raw bytes, SHA-1, 6 digits, 30 s),
  `staff1@example.com` / `dev-staff-password` (employee) and
  `admin1@example.com` / `dev-admin-password` (admin), both with an
  authenticator (TOTP secrets `dev-totp-secret-staff1` and
  `dev-totp-secret-admin1`): staff and admins always sign in with a second
  factor [decided 6 Nov 2026];
- `dev-cli` signs in two ways. The password grant records `amr` (`pwd`, and
  `otp` when the code is sent as `totp=`; a user with an authenticator must
  send it) but Keycloak gives it no `auth_time`, so the API's step-up
  (approvals, wallet changes, payouts) refuses it. For those, sign in as a
  browser does (code flow with PKCE, `auth_time` included):
  `python scripts/dev_token.py admin1@example.com dev-admin-password dev-totp-secret-admin1`
  prints an access token.

The realm is imported only into an empty Keycloak database. To load a
changed realm file locally, remove the Keycloak volume (users and sessions
are lost; the journal is not):

```
docker compose --profile platform stop keycloak
docker compose --profile platform rm -f keycloak
docker volume rm engine_keycloak   # name: docker volume ls
docker compose --profile platform up -d
```

Development only. The production realm drops `dev-cli` and the test users,
turns on e-mail verification (needs SMTP, Amazon SES) and requires
multi-factor authentication for sensitive actions (outline M04).

## Sign-in pages (pipridge themes, 8 Nov 2026)

`themes/` holds two login themes built on Keycloak's own `keycloak.v2`
pages: `pipridge` (traders, the realm's login theme) and `pipridge-staff`
(the `backoffice` client's login theme: the same look, labelled "Back
office", without the self-registration link). Both draw the pipridge ridge
terrain behind a glass card (`js/terrain.js`, WebGL; one still frame for
reduced motion, CSS ridges without WebGL) and use self-hosted Inter (SIL
OFL, `fonts/OFL.txt`). Compose mounts the folder at `/opt/keycloak/themes`.
A new realm gets the themes from the realm file; for an existing one the
one-shot `keycloak-theme` service sets them on every
`docker compose --profile platform up` (it logs "pipridge sign-in themes
set" and exits). Development mode does not cache themes, so an edited CSS
file shows on the next page load.
