# The API image. Started with no arguments it runs `fraudlens.platform.demo`: build
# whatever is missing (dataset, models, database contents), then serve.
# Multi-arch: python:3.12-slim publishes linux/amd64 and linux/arm64 (Apple Silicon).
FROM python:3.12-slim

# LightGBM needs the OpenMP runtime. util-linux provides runuser for the entrypoint.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 util-linux \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Dependencies first, so editing the source does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN uv sync --frozen --no-dev

# Generated data and models live in volumes; entrypoint ensures they are writable.
RUN useradd --create-home --uid 10001 fraudlens \
    && mkdir data artifacts \
    && chown fraudlens:fraudlens data artifacts

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

# Run the entrypoint as root so it can chown mounted volumes, then drop to fraudlens.
USER root
EXPOSE 8010
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "fraudlens.platform.demo", "--host", "0.0.0.0"]
