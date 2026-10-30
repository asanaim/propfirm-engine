"""Service settings from environment variables.

Secrets (the database password inside DATABASE_URL) come from the
environment, which in production is filled from the SOPS-encrypted file;
they are never written to logs or to the repository.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from propfirm.policy import DEFAULT_POLICY
from propfirm.rules.calendar import DEFAULT_NEWS_DIR

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
    news_dir: Path = DEFAULT_NEWS_DIR  # restricted-events calendar files (M13)
    customer_schema: str = "customers"  # CIF, orders, accounts (personal data)
    settings_schema: str = "settings"  # admin setting changes
    oidc_issuer: str = ""  # e.g. http://localhost:8080/realms/propfirm (API only)
    oidc_jwks_url: str = ""  # where the API fetches the realm's signing keys
    oidc_audience: str = "propfirm-api"
    sandbox: bool = False  # sandbox payments and screening; never in production
    country_header: str | None = None  # header a trusted proxy sets with the IP country
    didit_api_key: str | None = None  # Tier 2 identity checks (C7); secret
    didit_workflow_id: str | None = None
    didit_webhook_secret: str | None = None  # secret
    public_url: str | None = None  # the portal's address, for the provider's callback
    smtp_host: str | None = None  # security emails; without it, the sandbox mailer
    smtp_port: int = 587
    smtp_tls: str = "starttls"  # or "implicit" (port 465)
    smtp_username: str | None = None
    smtp_password: str | None = field(default=None, repr=False)  # secret
    mail_from: str | None = None  # the From address; required with SMTP_HOST
    price_source: str = "synthetic"  # "synthetic" or "twelve_data": named beside prices
    batch: int = 200  # inputs per transaction, at most
    block_ms: int = 1_000  # how long one stream read waits for input
    publish_ms: int = 250  # live state is published at most this often

    def __post_init__(self) -> None:
        for name in ("database_url", "valkey_url"):
            if not getattr(self, name):
                raise ConfigError(f"{name} is required")
        schemas = ("schema", "read_schema", "customer_schema", "settings_schema")
        for name in schemas:
            if not _NAME.fullmatch(getattr(self, name)):
                raise ConfigError(f"{name} must match {_NAME.pattern}")
        if len({getattr(self, name) for name in schemas}) != len(schemas):
            raise ConfigError(f"{', '.join(schemas)} must differ")
        for name in ("stream", "group", "consumer"):
            if not _KEY.fullmatch(getattr(self, name)):
                raise ConfigError(f"{name} must match {_KEY.pattern}")
        didit = (self.didit_api_key, self.didit_workflow_id, self.didit_webhook_secret)
        if any(didit) and not all(didit):
            raise ConfigError(
                "DIDIT_API_KEY, DIDIT_WORKFLOW_ID and DIDIT_WEBHOOK_SECRET go together"
            )
        if self.price_source not in ("synthetic", "twelve_data"):
            raise ConfigError("PROPFIRM_PRICE_SOURCE must be synthetic or twelve_data")
        if self.smtp_host and not self.mail_from:
            raise ConfigError("MAIL_FROM is required with SMTP_HOST")
        if self.smtp_tls not in ("starttls", "implicit"):
            raise ConfigError("SMTP_TLS must be starttls or implicit")
        if bool(self.smtp_username) != bool(self.smtp_password):
            raise ConfigError("SMTP_USERNAME and SMTP_PASSWORD go together")
        for name in ("batch", "block_ms", "publish_ms", "smtp_port"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ConfigError(f"{name} must be a positive integer")

    @property
    def didit(self) -> bool:
        """Whether Didit is configured."""
        return self.didit_api_key is not None

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
        news = env.get("PROPFIRM_NEWS_DIR")
        sandbox = env.get("PROPFIRM_SANDBOX", "0")
        if sandbox not in ("0", "1"):
            raise ConfigError("PROPFIRM_SANDBOX must be 0 or 1")
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
            news_dir=Path(news) if news else DEFAULT_NEWS_DIR,
            customer_schema=text("PROPFIRM_CUSTOMER_SCHEMA", "customers"),
            settings_schema=text("PROPFIRM_SETTINGS_SCHEMA", "settings"),
            oidc_issuer=text("OIDC_ISSUER", ""),
            oidc_jwks_url=text("OIDC_JWKS_URL", ""),
            oidc_audience=text("OIDC_AUDIENCE", "propfirm-api"),
            sandbox=sandbox == "1",
            country_header=env.get("PROPFIRM_COUNTRY_HEADER") or None,
            didit_api_key=env.get("DIDIT_API_KEY") or None,
            didit_workflow_id=env.get("DIDIT_WORKFLOW_ID") or None,
            didit_webhook_secret=env.get("DIDIT_WEBHOOK_SECRET") or None,
            public_url=env.get("PROPFIRM_PUBLIC_URL") or None,
            smtp_host=env.get("SMTP_HOST") or None,
            smtp_port=number("SMTP_PORT", 587),
            smtp_tls=text("SMTP_TLS", "starttls"),
            smtp_username=env.get("SMTP_USERNAME") or None,
            smtp_password=env.get("SMTP_PASSWORD") or None,
            mail_from=env.get("MAIL_FROM") or None,
            price_source=text("PROPFIRM_PRICE_SOURCE", "synthetic"),
            batch=number("PROPFIRM_BATCH", 200),
            block_ms=number("PROPFIRM_BLOCK_MS", 1_000),
            publish_ms=number("PROPFIRM_PUBLISH_MS", 250),
        )
