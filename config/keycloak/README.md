# Keycloak realm (development)

`propfirm-realm.json` is imported when Keycloak starts with an empty
database (`docker compose --profile platform up`). It defines:

- roles `trader` (every self-registered user), `staff`, `admin`;
- clients `portal` and `backoffice` (browser login with PKCE) and
  `dev-cli` (password grant, for tests and curl only);
- every access token carries the audience `propfirm-api`, which the API
  requires;
- two test users: `trader1@example.com` / `dev-trader-password` and
  `staff1@example.com` / `dev-staff-password`.

Development only. The production realm drops `dev-cli` and the test users,
turns on e-mail verification (needs SMTP, Amazon SES) and requires
multi-factor authentication for sensitive actions (outline M04).
