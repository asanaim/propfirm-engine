# Two images from one file (P2 step 7a):
#   runtime (the default, last stage): the services. No development tools, no
#     tests, no uv; runs as the unprivileged user `pipridge` (uid 10001).
#   test: the test suite (`docker compose run --rm --build test`).
# pinned by digest: rebuild with a newer digest to take Debian security fixes
FROM python:3.14.10-slim@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

# The virtual environment lives outside /app so a bind-mounted
# checkout (docker compose) never shadows it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./


FROM base AS test
RUN uv sync --frozen --no-install-project
COPY src ./src
COPY tests ./tests
COPY config ./config
COPY scripts ./scripts
COPY security ./security
COPY .secrets.baseline ./
RUN uv sync --frozen
CMD ["uv", "run", "--frozen", "pytest"]


FROM base AS runtime
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY config ./config
RUN uv sync --frozen --no-dev \
    && rm /usr/local/bin/uv \
    && groupadd --system --gid 10001 pipridge \
    && useradd --system --uid 10001 --gid pipridge --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin pipridge \
    && install -d -o pipridge -g pipridge -m 0750 /var/lib/propfirm
USER 10001:10001
CMD ["/opt/venv/bin/python", "-m", "propfirm.service.engine"]
