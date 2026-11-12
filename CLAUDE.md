# Skippy

Personal AI assistant: FastAPI backend (`backend/skippy`) + React/Vite UI (`frontend/`) in one
Docker container, with an Obsidian vault mounted at `/vault`. See README.md for features and roadmap.
Repo: https://github.com/Zethex/skippy_agent

## Local dev on this Windows PC

- No Python or Node on the host: build, test and run everything through Docker.
- Docker Desktop must be running. If it crashes on start with "initializing Inference manager ...
  dockerInference", move `%LOCALAPPDATA%\Docker\run` aside and relaunch it (stale socket files).
- Providers, vault choice and name are configured in the app (Settings) and stored in
  `/data/settings.json` in the `skippy-data` volume; `.env` only has `SKIPPY_VAULTS_DIR`
  (`C:/Users/Zethe/Documents`, mounted at `/vaults`) and `TZ`. The real vault is `Bipolarbear`
  in there; Skippy writes to whichever vault is selected unless editing is turned off.
- To start from a clean first run: `docker compose down -v` (also wipes chat history).
- Git has `core.autocrlf=true` globally; `.gitattributes` forces LF because CRLF breaks scripts in
  Linux containers (e.g. the `tests/fake_claude.py` shebang).

- The image is Wolfi-based; base images are build args (`SKIPPY_BASE_IMAGE`, `SKIPPY_BUILD_IMAGE`
  in `.env`) so the work PC can use a corporate mirror. Don't add CA-cert handling: the base
  image carries its certs, and `SSL_CERT_FILE` / `NODE_USE_SYSTEM_CA` make Python, httpx and npm
  use them. Python lives in a venv at `/opt/venv`.

## Commands

```bash
# run / rebuild after code changes (UI at http://localhost:8642)
docker compose up -d --build
docker compose logs -f skippy

# backend tests, inside the built Wolfi image (build it first)
MSYS_NO_PATHCONV=1 docker run --rm --user root --entrypoint sh -v "$(pwd -W)/backend:/src:ro" skippy:local \
  -c "cp -r /src /b && cd /b && pip install -q pytest && PYTHONPATH=. pytest -q tests"

# frontend type-check/build happens in the Dockerfile's `web` stage
docker compose build
```

## Notes

- Settings: `backend/skippy/settings_store.py` (storage, secret masking, vault discovery) and
  `frontend/src/Settings.tsx`. Saving calls `apply_settings()` in `main.py`, which rebuilds the
  vault, provider registry and transcriber live; endpoints must read `app.state.*`, never capture them.
- Secrets never go to the browser (`<field>_set` flags instead). In a PUT, a missing/null secret
  keeps the saved value, `""` clears it.
- Provider kinds in settings: `claude_code` (Claude CLI + subscription token), `portkey`,
  `openai_compat`; `runtime_provider()` maps portkey onto the OpenAI-compatible class with
  `x-portkey-*` headers. `config/skippy.yaml` is only read to seed a first run.
- New provider kinds go in `backend/skippy/providers/` (implement `availability`, `check`, `stream`).
- Persona/attitude dial lives in `backend/skippy/persona.py`; keep code, todos and vault writes plain.
