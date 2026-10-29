"""Service settings from environment variables.

Secrets (the database password inside DATABASE_URL) come from the
environment, which in production is filled from the SOPS-encrypted file;
they are never written to logs or to the repository.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from propfirm.policy import DEFAULT_POLICY

_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_KEY = re.compile(r"[A-Za-z0-9:_.-]{1,200}")


class ConfigError(ValueError):
    """A setting is missing or invalid."""


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    database_url: str
    valkey_url: str
    schema: str = "engine"  # journal schema
    read_schema: str = "engine_read"  # projections (rebuildable)
    stream: str = "engine:inputs"
    group: str = "engine"
    consumer: str = "engine-1"
    anchor_path: Path | None = None
    policy_path: Path = DEFAULT_POLICY
    batch: int = 200  # inputs per transaction, at most
    block_ms: int = 1_000  # how long one stream read waits for input
    publish_ms: int = 250  # live state is published at most this often

    def __post_init__(self) -> None:
        for name in ("database_url", "valkey_url"):
            if not getattr(self, name):
                raise ConfigError(f"{name} is required")
        for name in ("schema", "read_schema"):
            if not _NAME.fullmatch(getattr(self, name)):
                raise ConfigError(f"{name} must match {_NAME.pattern}")
        if self.schema == self.read_schema:
            raise ConfigError("schema and read_schema must differ")
        for name in ("stream", "group", "consumer"):
            if not _KEY.fullmatch(getattr(self, name)):
                raise ConfigError(f"{name} must match {_KEY.pattern}")
        for name in ("batch", "block_ms", "publish_ms"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ConfigError(f"{name} must be a positive integer")

    @property
    def live_prefix(self) -> str:
        """Valkey key prefix of the published live state."""
        return f"live:{self.schema}"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ServiceConfig:
        def text(name: str, default: str) -> str:
            return env.get(name, default)

        def number(name: str, default: int) -> int:
            raw = env.get(name)
            if raw is None:
                return default
            if not re.fullmatch(r"[0-9]+", raw):
                raise ConfigError(f"{name} must be a whole number, got {raw!r}")
            return int(raw)

        anchor = env.get("PROPFIRM_ANCHOR_PATH")
        policy = env.get("PROPFIRM_POLICY")
        return cls(
            database_url=text("DATABASE_URL", ""),
            valkey_url=text("VALKEY_URL", ""),
            schema=text("PROPFIRM_SCHEMA", "engine"),
            read_schema=text("PROPFIRM_READ_SCHEMA", "engine_read"),
            stream=text("PROPFIRM_STREAM", "engine:inputs"),
            group=text("PROPFIRM_GROUP", "engine"),
            consumer=text("PROPFIRM_CONSUMER", "engine-1"),
            anchor_path=Path(anchor) if anchor else None,
            policy_path=Path(policy) if policy else DEFAULT_POLICY,
            batch=number("PROPFIRM_BATCH", 200),
            block_ms=number("PROPFIRM_BLOCK_MS", 1_000),
            publish_ms=number("PROPFIRM_PUBLISH_MS", 250),
        )
