"""Event journal and replay (P1 step 6, acceptance tests 1 and 4).

- `inputs`: everything that can change engine state, and `apply`.
- `codec`: canonical JSON, so equal values hash equally everywhere.
- `setup`: the configuration a journal is bound to.
- `machine`: records, the hash chain, live apply and verified replay.
- `stream`: inputs over a Valkey Stream (consumer group, redelivery).
- `postgres`: the journal, ledger and price store in PostgreSQL.
- `runner`: start-up rebuild and the read-journal-acknowledge loop.
"""
