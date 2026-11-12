# Both stages are built on Wolfi. Override the base images to use your own mirror, e.g. a
# corporate registry whose images already carry the right CA certificates:
#   docker compose build --build-arg BASE_IMAGE=registry.corp.example/chainguard/wolfi-base:latest
# or set SKIPPY_BASE_IMAGE / SKIPPY_BUILD_IMAGE in .env. Any Wolfi-based image with apk works.
ARG BUILD_IMAGE=cgr.dev/chainguard/wolfi-base:latest
ARG BASE_IMAGE=cgr.dev/chainguard/wolfi-base:latest

# ---- web UI ------------------------------------------------------------------
FROM ${BUILD_IMAGE} AS web
ARG NODE_VERSION=22
RUN apk add --no-cache nodejs-${NODE_VERSION} npm
# Trust the image's own certificate store when npm downloads packages.
ENV NODE_USE_SYSTEM_CA=1
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- runtime -----------------------------------------------------------------
FROM ${BASE_IMAGE}

ARG PYTHON_VERSION=3.12
# Home PC: both true. Work PC: INSTALL_CLAUDE_CODE=false (everything goes through Portkey).
ARG INSTALL_CLAUDE_CODE=true
ARG INSTALL_WHISPER=true

RUN apk add --no-cache python-${PYTHON_VERSION} bash curl tzdata ripgrep

# Python, httpx and Hugging Face downloads use the image's system certificate store instead of
# their bundled one, so certificates baked into the base image are trusted everywhere.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY backend/requirements.txt backend/requirements-voice.txt ./
RUN python${PYTHON_VERSION} -m venv /opt/venv \
 && pip install -r requirements.txt \
 && if [ "$INSTALL_WHISPER" = "true" ]; then pip install -r requirements-voice.txt; fi

RUN adduser -D -u 1000 -h /home/skippy skippy && mkdir -p /data /vaults && chown skippy:skippy /data /vaults
USER skippy
ENV HOME=/home/skippy PATH=/home/skippy/.local/bin:/opt/venv/bin:$PATH

# The Claude Code CLI lets Skippy use a Claude Pro/Max subscription instead of an API key.
RUN if [ "$INSTALL_CLAUDE_CODE" = "true" ]; then curl -fsSL https://claude.ai/install.sh | bash; fi

COPY --chown=skippy:skippy backend/skippy ./skippy
COPY --chown=skippy:skippy config ./config
COPY --from=web --chown=skippy:skippy /web/dist ./static

EXPOSE 8642
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -fsS http://localhost:8642/api/status > /dev/null || exit 1
CMD ["uvicorn", "skippy.asgi:app", "--host", "0.0.0.0", "--port", "8642"]
