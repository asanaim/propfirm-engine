"""The API process, wired to PostgreSQL and Valkey from the environment.

    uvicorn propfirm.api.main:app --host 127.0.0.1 --port 8000

Bind to localhost only until login and account ownership exist (step 2).
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import valkey.asyncio as avalkey
from fastapi import FastAPI

from propfirm.api.app import Deps, create_app
from propfirm.journal.stream import InputStreamWriter
from propfirm.policy import load_policy
from propfirm.service.config import ServiceConfig
from propfirm.service.gateway import Gateway
from propfirm.service.live import LiveReader
from propfirm.service.projector import HistoryReader


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = ServiceConfig.from_env(os.environ)
    client = avalkey.Valkey.from_url(config.valkey_url)
    pool = await asyncpg.create_pool(config.database_url, min_size=1, max_size=10)
    try:
        deps = Deps(
            live=LiveReader(client, config.live_prefix),
            history=HistoryReader(pool, config.read_schema),
            gateway=Gateway(InputStreamWriter(client, config.stream), pool, config.schema),
            policy=load_policy(config.policy_path),
        )
        app.state.deps = deps
        yield
    finally:
        await pool.close()
        await client.aclose()


app = create_app(lifespan=lifespan)
