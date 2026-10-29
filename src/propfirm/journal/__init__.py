"""Event journal and replay (P1 step 6, acceptance tests 1 and 4).

- `inputs`: everything that can change engine state, and `apply`.
- `codec`: canonical JSON, so equal values hash equally everywhere.
- `setup`: the configuration a journal is bound to.
- `machine`: records, the hash chain, keyed price digests, checkpoints,
  live apply, verified replay and resume from a snapshot.
- `stream`: inputs over a Valkey Stream (consumer group, redelivery).
- `postgres`: the journal, ledger, price store, price keys and snapshots in
  PostgreSQL.
- `anchor`: copies of the head hash kept outside the database.
- `runner`: start-up rebuild and the read-journal-acknowledge loop.
"""
