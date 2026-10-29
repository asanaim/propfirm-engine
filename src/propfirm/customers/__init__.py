"""Customers: the CIF (Customer Information Form), orders and accounts.

Personal data lives here, in its own PostgreSQL schema, never in the
engine journal: the journal is append-only and hash-chained, so it could
not erase personal data on request. The engine only ever sees ACC ids.

- `store`: CIF records, events, orders, accounts, discount codes, screenings.
- `pricing`: list price, repeat-account and code discounts (policy file).
- `providers`: payment and screening interfaces, with sandbox stand-ins.
- `checkout`: registration, ordering, payment and account provisioning.
"""
