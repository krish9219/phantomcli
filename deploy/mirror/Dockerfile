# Phantom plugin mirror — production container.
#
# Build:
#     docker build -f deploy/mirror/Dockerfile -t phantom-mirror:1.0.0 .
# Run:
#     docker run -d --name phantom-mirror \
#         -p 127.0.0.1:8801:8801 \
#         -v /srv/phantom-mirror:/data \
#         phantom-mirror:1.0.0
#
# The container expects a writable /data directory containing:
#     /data/index.json     — registered plugin index (auto-created)
#     /data/plugins/...    — published bundles
# Operator runs `phantom plugin publish <dir> --store /data` from the
# host (or via `docker exec`) to add bundles.

FROM python:3.13-slim AS base

LABEL org.opencontainers.image.title="phantom-mirror"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.source="https://github.com/aravindlabs/phantomcli"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 10001 mirror
WORKDIR /app

COPY pyproject.toml ./
COPY phantom ./phantom

RUN pip install --no-cache-dir \
        "fastapi>=0.110" \
        "uvicorn[standard]>=0.27" \
        "cryptography>=42" \
        "pynacl>=1.5"

# Install phantom in editable form so we get the mirror server module.
RUN pip install --no-cache-dir -e .

RUN mkdir -p /data && chown -R mirror:mirror /data /app
USER mirror

VOLUME ["/data"]
EXPOSE 8801

ENV PHANTOM_MIRROR_DATA=/data \
    PHANTOM_MIRROR_HOST=0.0.0.0 \
    PHANTOM_MIRROR_PORT=8801

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8801/healthz', timeout=2).status == 200 else 1)"

CMD ["python", "-m", "phantom.plugins.mirror.serve_cli"]
