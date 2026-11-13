# Two images from one file (P2 step 7a):
#   runtime (the default, last stage): the services. No development tools, no
#     tests, no uv; runs as the unprivileged user `pipridge` (uid 10001).
#   test: the test suite (`docker compose run --rm --build test`).
# pinned by digest; Debian security fixes published since the image was built
# are installed on top (the upgrade below), so a fix does not wait for a new
# digest. Grype in CI checks the result.
FROM python:3.15.2-slim@sha256:e97b88e416c3c01eb427b21fd8cd3ebf006e6ee38afcfcc3cae1c54ee2ab0a75 AS base
RUN apt-get update \
    && apt-get -y --no-install-recommends upgrade \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

# The virtual environment lives outside /app so a bind-mounted
# checkout (docker compose) never shadows it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
# every file pyproject.toml names (readme, license-files): the build of the
# project itself fails without them (tests/test_dockerfile.py)
COPY pyproject.toml uv.lock README.md LICENSE ./


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
# the build shown to admins (rule AC23): `git rev-parse --short HEAD`, passed
# by compose from PIPRIDGE_REVISION; last, so a new revision reuses every layer
ARG PIPRIDGE_REVISION=unknown
ENV PIPRIDGE_REVISION=$PIPRIDGE_REVISION
USER 10001:10001
CMD ["/opt/venv/bin/python", "-m", "propfirm.service.engine"]
