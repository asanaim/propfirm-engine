"""Processes that run the engine as a platform (P2).

- `config`: settings from environment variables.
- `setup`: the engine setup each environment runs.
- `engine`: the engine process (the only writer of the journal).
- `live`: the engine's current state, published to Valkey for the API.
- `gateway`: submit an input and wait for its journal record.
- `projector`: per-account history tables built from the journal.
- `feeder`, `scheduler`: synthetic prices (development only), clock ticks
  and checkpoints.

Channels never change state themselves: every change is an input on the
engine's stream, decided by the engine and recorded in the journal. What
the API reads (live state, history) is derived and can be rebuilt.
"""
