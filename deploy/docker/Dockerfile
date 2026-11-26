# One image for every Python service (connector, worker, api); the command
# picks the role: `ekb connect`, `ekb worker`, `ekb api`.
#
# Build:  docker build -f deploy/docker/Dockerfile -t ekb:local .
# Extras: the real infrastructure's clients are in by default (S17); a smaller image:
#         docker build --build-arg EXTRAS="" ...

ARG PYTHON_VERSION=3.13

# ---------------------------------------------------------------- builder
FROM python:${PYTHON_VERSION}-slim-trixie AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
# Kafka, OpenSearch, and DynamoDB + S3 clients (S17): the services run on them.
ARG EXTRAS="--extra kafka --extra search --extra aws"

# 1) Dependencies only: this layer is cached until uv.lock changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev ${EXTRAS}

# 2) The project itself.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable ${EXTRAS}

# ---------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

# CVE-2026-85091 is fixed in Debian 13's deb13u1 revision.
RUN apt-get update \
 && apt-get install -y --only-upgrade --no-install-recommends zlib1g \
 && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' zlib1g)" ge '1:1.3.dfsg+really1.3.1-1+deb13u1' \
 && rm -rf /var/lib/apt/lists/*

# uv installs all service dependencies in the builder. The service needs
# neither system pip's vendored libraries nor its bootstrap wheel.
RUN python -m pip uninstall -y pip \
 && python -c "import ensurepip, pathlib, shutil; shutil.rmtree(pathlib.Path(ensurepip.__file__).parent)"

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app \
 && mkdir -p /app/.cache \
 && chown app:app /app/.cache

WORKDIR /app
# .cache/ holds the model cache, the cost log and the test tokens; compose mounts a
# volume here, which starts owned by the app user because this folder is.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app config ./config

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EKB_CONFIG_DIR=/app/config

USER app

LABEL org.opencontainers.image.title="ekb" \
      org.opencontainers.image.description="Enterprise knowledge base for AI agents"

ENTRYPOINT ["ekb"]
CMD ["--help"]
