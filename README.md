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
  - `openai_compat`: any OpenAI-style endpoint. Portkey is preconfigured; OpenAI, Ollama and LiteLLM are a few lines in `config/skippy.yaml`.
- **Voice input**: press the mic, speak, press again. Audio is transcribed inside the container by Whisper (faster-whisper on CPU) and dropped into the message box for you to check before sending.
- **Obsidian second brain**: Skippy can search, read and (unless you turn it off) write notes in the mounted vault. The side panel has the todo list, quick capture into a daily note, and vault search. "Save to vault" files a whole conversation.

## Run it

```bash
cp .env.home.example .env      # or .env.work.example on the work PC
# edit .env: vault path and credentials
docker compose up -d --build
```

Open <http://localhost:8642>.

### Home PC (Claude subscription)

1. On the host, run `claude setup-token` and sign in. It prints a long-lived token.
2. Put it in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`.

Don't set `ANTHROPIC_API_KEY` as well unless you want API billing; the CLI prefers it over the subscription token.

### Work PC (Portkey)

Fill in `PORTKEY_BASE_URL`, `PORTKEY_API_KEY`, `PORTKEY_MODELS`, and whichever of
`PORTKEY_VIRTUAL_KEY` / `PORTKEY_CONFIG` / `PORTKEY_PROVIDER` your gateway uses. Set
`INSTALL_CLAUDE_CODE=false` so the image doesn't try to download the Claude CLI.

Behind a corporate proxy: set `HTTPS_PROXY`, and if the gateway uses an internal CA, drop the
PEM in `config/` and point `SKIPPY_CA_BUNDLE` at it. The Whisper model is fetched from
huggingface.co on first use; if that is blocked, set `INSTALL_WHISPER=false` or copy a model
into the `skippy-data` volume under `whisper/`.

## How Skippy uses the vault

The vault is bind-mounted at `/vault`. Skippy keeps its own files in one folder
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
**keep the vault in git or under backup**. `SKIPPY_VAULT_WRITE=false` makes it read-only.

There is no login. The port is bound to `127.0.0.1` on purpose; don't expose it.

## Layout

```
backend/skippy/
  main.py            HTTP API and static file serving
  persona.py         system prompt and the attitude dial
  vault.py           vault access, todos, capture, function-calling tools
  stt.py             Whisper transcription
  db.py              conversation history (SQLite in /data)
  providers/         claude_code.py, openai_compat.py; add a new kind here
frontend/src/        React + Vite UI
config/skippy.yaml   provider list
```

## Development

```bash
# backend
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
SKIPPY_DATA_DIR=./.data SKIPPY_VAULT_PATH=/path/to/vault SKIPPY_CONFIG=../config/skippy.yaml \
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
