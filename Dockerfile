FROM python:3.14.10-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

# The virtual environment lives outside /app so a bind-mounted
# checkout (docker compose) never shadows it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

COPY src ./src
COPY tests ./tests
COPY config ./config
RUN uv sync --frozen

CMD ["uv", "run", "--frozen", "pytest"]
