# The four mock sources (1 · Sources) in one small image, for docker compose (S17).
#
# Not the product: the product image (Dockerfile) never contains sources/, and
# src/ekb never imports it. Compose runs this image four times, one mock each:
#   python -m sources.mock_docs   --host 0.0.0.0   (the docs tool, port 8081)
#   python -m sources.mock_hr_api --host 0.0.0.0   (the HR record API, port 8082)
#   python -m sources.mock_idp    --host 0.0.0.0   (the identity provider, port 8083)
#   python -m sources.mock_hr_db  init --path /data/hr.sqlite   (the HR database, once)
#
# Build: docker build -f deploy/docker/sources.Dockerfile -t ekb-sources:local .
# What enters the build is listed in sources.Dockerfile.dockerignore.

ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim-trixie

# CVE-2026-85091 is fixed in Debian 13's deb13u1 revision (as in the product image).
RUN apt-get update \
 && apt-get install -y --only-upgrade --no-install-recommends zlib1g \
 && rm -rf /var/lib/apt/lists/*

COPY deploy/docker/sources-requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app \
 && mkdir -p /app /data \
 && chown app:app /app /data

WORKDIR /app
COPY --chown=app:app sources ./sources

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

LABEL org.opencontainers.image.title="ekb-sources" \
      org.opencontainers.image.description="Mock department systems for the ekb POC (synthetic data)"

ENTRYPOINT ["python", "-m"]
CMD ["sources.mock_docs", "--host", "0.0.0.0"]
