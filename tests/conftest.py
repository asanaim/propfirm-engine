import asyncio
import os
import uuid
from collections.abc import Iterator

import asyncpg
import pytest
import valkey


def _require_services() -> bool:
    return os.environ.get("PROPFIRM_REQUIRE_SERVICES") == "1"


@pytest.fixture
def valkey_client() -> Iterator[valkey.Valkey]:
    """A Valkey connection from VALKEY_URL.

    Skipped when VALKEY_URL is unset, unless PROPFIRM_REQUIRE_SERVICES=1 (CI),
    where a missing or unreachable service fails the run instead.
    """
    url = os.environ.get("VALKEY_URL")
    if not url:
        if _require_services():
            pytest.fail("VALKEY_URL is not set but PROPFIRM_REQUIRE_SERVICES=1")
        pytest.skip("VALKEY_URL not set; run through docker compose to include this test")
    client = valkey.Valkey.from_url(url)
    client.ping()
    yield client
    client.close()  # type: ignore[no-untyped-call]


@pytest.fixture
def stream_key(valkey_client: valkey.Valkey) -> Iterator[str]:
    key = f"test:prices:{uuid.uuid4().hex}"
    yield key
    valkey_client.delete(key)


def _service_url(name: str) -> str:
    url = os.environ.get(name)
    if not url:
        if _require_services():
            pytest.fail(f"{name} is not set but PROPFIRM_REQUIRE_SERVICES=1")
        pytest.skip(f"{name} not set; run through docker compose to include this test")
    return url


@pytest.fixture
def database_url() -> str:
    """PostgreSQL from DATABASE_URL (skip and fail rules as for Valkey)."""
    return _service_url("DATABASE_URL")


@pytest.fixture
def valkey_url() -> str:
    return _service_url("VALKEY_URL")


@pytest.fixture
def pg_schema(database_url: str) -> Iterator[str]:
    """A fresh schema name; the schema is dropped afterwards."""
    schema = f"t_{uuid.uuid4().hex[:16]}"
    yield schema

    async def drop() -> None:
        conn = await asyncpg.connect(database_url)
        try:
            await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await conn.close()

    asyncio.run(drop())
