# Skippy

A personal AI assistant that runs in one local Docker container: a web UI you can type or
talk to, any mix of AI providers behind it, and your Obsidian vault as its long-term memory.

Named for Skippy the Magnificent from Craig Alanson's *Expeditionary Force*. The attitude is a
dial in the top bar (Straight, Dry, Skippy, Magnificent), so the beer can only shows up when
you want it.

## What works today (v0.1)

- **Chat** with streaming replies, saved conversation history, Markdown and code rendering.
- **Providers**, switchable per message:
  - `claude_code`: the Claude Code CLI in headless mode, so a Claude Pro/Max subscription works with no API key.
  - `openai_compat`: any OpenAI-style endpoint. Portkey, OpenAI, Ollama and LiteLLM are presets in Settings.
- **Voice input**: press the mic, speak, press again. Audio is transcribed inside the container by Whisper (faster-whisper on CPU) and dropped into the message box for you to check before sending.
- **Obsidian second brain**: Skippy can search, read and (unless you turn it off) write notes in the mounted vault. The side panel has the todo list, quick capture into a daily note, and vault search. "Save to vault" files a whole conversation.

## Run it

```bash
cp .env.home.example .env      # or .env.work.example on the work PC
# edit .env: set SKIPPY_VAULTS_DIR to the folder that holds your Obsidian vault(s)
docker compose up -d --build
```

Open <http://localhost:8642>. On first start Skippy opens **Settings**, where you set up
everything else: no more editing `.env` for credentials.

- **AI providers**: add Claude (subscription), Portkey, OpenAI, Ollama or any OpenAI-compatible
  gateway from presets, paste keys, press *Test connection* (it also lists the gateway's models so you
  can add them with a click), pick a default, and switch providers on or off.
- **Obsidian vault**: Skippy lists the vaults it finds under `SKIPPY_VAULTS_DIR`, lets you browse to
  any folder or create a new vault, and has a switch for whether it may edit notes.
- **You & voice**: your name and the Whisper model.

Changes apply immediately, without restarting the container. Settings live in
`/data/settings.json` inside the `skippy-data` volume (keys included, readable only inside the
container). They are never sent back to the browser: the UI only shows whether a key is saved.
On the very first start, anything in `.env` and `config/skippy.yaml` (the v0.1 way of
configuring) is imported; after that the Settings screen is the source of truth.

`.env` now only holds what Docker needs before Skippy starts: `SKIPPY_VAULTS_DIR`, `TZ`, the
port, build switches (`INSTALL_CLAUDE_CODE`, `INSTALL_WHISPER`) and proxy settings.

### Home PC (Claude subscription)

In Settings, add **Claude subscription**. To get the token, run this once and sign in:

```bash
docker exec -it skippy claude setup-token
```

Paste the token into the provider and save. Don't also set `ANTHROPIC_API_KEY` unless you
want API billing; the CLI prefers it over the subscription token.

### Work PC (Portkey)

Use `.env.work.example` (it sets `INSTALL_CLAUDE_CODE=false`), then add **Portkey gateway** in
Settings: gateway URL, API key, and under *Routing options* whichever of virtual key, config ID
or provider your gateway uses. *Test connection* fetches the model list.

Behind a corporate proxy: set `HTTPS_PROXY`, and if the gateway uses an internal CA, drop the
PEM in `config/` and point `SKIPPY_CA_BUNDLE` at it. The Whisper model is fetched from
huggingface.co on first use; if that is blocked, set `INSTALL_WHISPER=false` or copy a model
into the `skippy-data` volume under `whisper/`.

## How Skippy uses the vault

`SKIPPY_VAULTS_DIR` is bind-mounted at `/vaults` and the vault you pick in Settings is a folder
inside it. Skippy can only see inside that folder, so point it at the parent of your vaults
(e.g. Documents), not your whole drive. Skippy keeps its own files in one folder
(`SKIPPY_VAULT_FOLDER`, default `Skippy/`) and treats the rest as reference material:

| Path | What it is |
| --- | --- |
| `Skippy/Todos.md` | The todo list. Plain `- [ ]` checkboxes, so it works with Obsidian and the Tasks plugin. |
| `Skippy/Daily/YYYY-MM-DD.md` | Quick captures, time-stamped. |
| `Skippy/Chats/` | Conversations saved from the UI. |

With the Claude provider, the CLI runs with the vault as its working directory and only its
file tools enabled (Read, Glob, Grep, plus Edit and Write when writing is on). With
OpenAI-style providers the same abilities are offered as function-calling tools. The system
prompt tells Skippy to never delete notes, to make minimal edits outside its own folder, and
to draw architecture as Mermaid. That is an instruction to a model, not a guarantee, so
**keep the vault in git or under backup**. Turning off *Let Skippy edit notes* in Settings makes it read-only.

There is no login. The port is bound to `127.0.0.1` on purpose; don't expose it.

## Layout

```
backend/skippy/
  main.py            HTTP API and static file serving
  settings_store.py  settings saved from the UI, vault discovery
  persona.py         system prompt and the attitude dial
  vault.py           vault access, todos, capture, function-calling tools
  stt.py             Whisper transcription
  db.py              conversation history (SQLite in /data)
  providers/         claude_code.py, openai_compat.py; add a new kind here
frontend/src/        React + Vite UI (Settings.tsx is the settings screen)
config/skippy.yaml   first-run provider list (imported once into settings)
```

## Development

```bash
# backend
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
SKIPPY_DATA_DIR=./.data SKIPPY_VAULTS_ROOT=/path/to/vaults SKIPPY_CONFIG=../config/skippy.yaml \
  uvicorn skippy.asgi:app --reload --port 8642
PYTHONPATH=. pytest tests

# frontend (proxies /api to :8642)
cd frontend && npm install && npm run dev
```

## Roadmap

1. **Day organiser**: a Today view built from todos, daily notes and calendar; morning plan and end-of-day wrap-up written to the daily note.
2. **Jira**: read assigned issues and sprints, link them to todos and architecture notes, draft updates.
3. **Repos**: mount a workspace folder so Skippy can read code across repositories and keep one architecture note per service in the vault.
4. **Vault recall**: embeddings-based search alongside keyword search; whole-vault task scan.
5. **Voice**: hands-free mode with auto-send, then spoken replies.
