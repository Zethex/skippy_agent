# ---- web UI ------------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- runtime -----------------------------------------------------------------
FROM python:3.12-slim

# Home PC: both true. Work PC: INSTALL_CLAUDE_CODE=false (everything goes through Portkey).
ARG INSTALL_CLAUDE_CODE=true
ARG INSTALL_WHISPER=true

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tzdata ripgrep \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements-voice.txt ./
RUN pip install -r requirements.txt \
 && if [ "$INSTALL_WHISPER" = "true" ]; then pip install -r requirements-voice.txt; fi

RUN useradd --create-home --uid 1000 skippy && mkdir -p /data /vaults && chown skippy:skippy /data /vaults
USER skippy
ENV HOME=/home/skippy PATH=/home/skippy/.local/bin:$PATH

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
