from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import Settings
from .db import DB
from .persona import SASS_LEVELS, build_system_prompt
from .providers import ChatRequest, Registry
from .settings_store import (WHISPER_MODELS, SettingsError, SettingsStore, clean_rel, create_vault, find_vaults,
                             list_folders, normalise_provider, runtime_provider)
from .stt import Transcriber
from .vault import Vault, VaultError

HISTORY_LIMIT = 40  # messages replayed to the model per turn


class ChatBody(BaseModel):
    message: str
    conversation_id: str | None = None
    provider: str | None = None
    model: str | None = None
    sass: int = 1


class TodoBody(BaseModel):
    text: str


class TodoToggle(BaseModel):
    line: int
    done: bool


class TitleBody(BaseModel):
    title: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Skippy", version=__version__)
    db = DB(settings.data_dir / "skippy.db")
    store = SettingsStore(settings.data_dir / "settings.json", settings.seed)
    vaults_root = settings.vaults_dir
    app.state.db, app.state.store, app.state.stt = db, store, None

    def apply_settings() -> None:
        """(Re)build the vault, providers and voice from the stored settings. Runs on every save,
        so changes in the Settings screen take effect without restarting the container."""
        cfg = store.data
        rel = cfg["vault"]["path"]
        path = (vaults_root if rel == "." else vaults_root / rel) if rel else vaults_root / ".no-vault-selected"
        app.state.vault = Vault(path, cfg["vault"]["folder"], cfg["vault"]["writable"])
        app.state.vault_rel = rel
        app.state.registry = Registry([runtime_provider(p) for p in cfg["providers"] if p["enabled"]],
                                      cfg["default_provider"])
        app.state.user_name = cfg["profile"]["user_name"]
        whisper = cfg["profile"]["whisper_model"]
        if app.state.stt is None or app.state.stt.model_name != whisper:
            app.state.stt = Transcriber(whisper, settings.data_dir / "whisper")

    apply_settings()

    @app.exception_handler(VaultError)
    async def vault_error(_request, exc: VaultError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(SettingsError)
    async def settings_error(_request, exc: SettingsError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # ---- status -------------------------------------------------------------
    @app.get("/api/status")
    def status():
        vault = app.state.vault
        return {
            "version": __version__,
            "user_name": app.state.user_name,
            "providers": [p.describe() for p in app.state.registry.providers.values()],
            "default_provider": app.state.registry.default_id(),
            "sass_levels": SASS_LEVELS,
            "vault": {"available": vault.available, "writable": vault.writable, "folder": vault.folder,
                      "notes": vault.note_count(), "name": vault.root.name if vault.available else "",
                      "path": app.state.vault_rel},
            "voice": {"available": app.state.stt.available, "model": app.state.stt.model_name},
        }

    # ---- settings -----------------------------------------------------------
    @app.get("/api/settings")
    def get_settings():
        return {**store.public(), "vaults_root": os.environ.get("SKIPPY_VAULTS_HOST") or str(vaults_root),
                "vaults_root_available": vaults_root.is_dir(),
                "claude_cli": bool(shutil.which("claude")), "whisper_models": list(WHISPER_MODELS)}

    @app.put("/api/settings")
    def put_settings(body: dict = Body(...)):
        rel = (body.get("vault") or {}).get("path")
        if rel:
            rel = clean_rel(rel)
            target = vaults_root if rel == "." else vaults_root / rel
            if not target.is_dir():
                raise SettingsError(f"There's no folder called {rel} in the mounted folder")
        store.update(body)
        apply_settings()
        return get_settings()

    @app.post("/api/settings/test")
    async def test_provider(body: dict = Body(...)):
        """Try a provider as currently typed in the form; blank secrets fall back to the saved ones."""
        merged = store.merge_secrets(body, store.provider(str(body.get("id") or "")))
        cfg = normalise_provider(merged)
        if not cfg:
            raise SettingsError("Unknown provider type")
        provider = Registry([runtime_provider(cfg)]).providers[cfg["id"]]
        return await provider.check()

    @app.get("/api/settings/vaults")
    def list_vaults():
        return find_vaults(vaults_root)

    @app.get("/api/settings/folders")
    def browse(path: str = "."):
        return list_folders(vaults_root, path)

    @app.post("/api/settings/vaults")
    def new_vault(body: dict = Body(...)):
        return {"path": create_vault(vaults_root, str(body.get("parent") or "."), str(body.get("name") or ""))}

    # ---- conversations ------------------------------------------------------
    @app.get("/api/conversations")
    def conversations():
        return db.list_conversations()

    @app.get("/api/conversations/{cid}")
    def conversation(cid: str):
        conv = db.get_conversation(cid)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        return {**conv, "messages": db.messages(cid)}

    @app.patch("/api/conversations/{cid}")
    def rename(cid: str, body: TitleBody):
        db.rename_conversation(cid, body.title.strip()[:120] or "Untitled")
        return {"ok": True}

    @app.delete("/api/conversations/{cid}")
    def delete(cid: str):
        db.delete_conversation(cid)
        return {"ok": True}

    @app.post("/api/conversations/{cid}/save")
    def save_to_vault(cid: str):
        conv = db.get_conversation(cid)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        lines = [f"# {conv['title']}", ""]
        for m in db.messages(cid):
            lines += [f"## {'Me' if m['role'] == 'user' else 'Skippy'}", "", m["content"], ""]
        safe = re.sub(r'[\\/:*?"<>|#^\[\]]', " ", conv["title"]).strip()[:80] or "Chat"
        day = datetime.fromtimestamp(conv["created_at"]).strftime("%Y-%m-%d")
        path = app.state.vault.write(f"{app.state.vault.folder}/Chats/{day} {safe}.md", "\n".join(lines), overwrite=True)
        return {"path": path}

    # ---- chat ---------------------------------------------------------------
    @app.post("/api/chat")
    async def chat(body: ChatBody):
        text = body.message.strip()
        if not text:
            raise HTTPException(400, "Message is empty")
        provider = app.state.registry.get(body.provider)
        if provider is None:
            raise HTTPException(400, "No AI provider is available. Add one in Settings.")
        ok, reason = provider.availability()
        if not ok:
            raise HTTPException(400, f"{provider.label} is not available: {reason}")

        conv = db.get_conversation(body.conversation_id) if body.conversation_id else None
        if conv is None:
            title = " ".join(text.split())
            conv = db.create_conversation(title[:60] + ("…" if len(title) > 60 else ""))
        db.add_message(conv["id"], "user", text)
        history = [{"role": m["role"], "content": m["content"]} for m in db.messages(conv["id"])[-HISTORY_LIMIT:]]
        model = body.model if body.model in provider.models else (provider.models[0] if provider.models else None)
        request = ChatRequest(
            system=build_system_prompt(body.sass, app.state.user_name, app.state.vault, provider.tool_style),
            messages=history, model=model, vault=app.state.vault)

        async def events():
            def line(obj: dict) -> bytes:
                return (json.dumps(obj) + "\n").encode("utf-8")

            reply: list[str] = []
            yield line({"type": "start", "conversation_id": conv["id"], "title": conv["title"],
                        "provider": provider.id, "model": model})
            try:
                async for event in provider.stream(request):
                    if event["type"] == "delta":
                        reply.append(event["text"])
                    yield line(event)
            except Exception as exc:  # surface provider bugs to the UI instead of a dead stream
                yield line({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                if "".join(reply).strip():
                    db.add_message(conv["id"], "assistant", "".join(reply), provider.id, model or "")
            yield line({"type": "done"})

        return StreamingResponse(events(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- voice --------------------------------------------------------------
    @app.post("/api/transcribe")
    async def transcribe(audio: UploadFile = File(...)):
        if not app.state.stt.available:
            raise HTTPException(503, "Voice input is not installed in this image (build with INSTALL_WHISPER=true)")
        data = await audio.read()
        if len(data) < 800:
            return {"text": ""}
        try:
            return {"text": await run_in_threadpool(app.state.stt.transcribe, data)}
        except Exception as exc:
            raise HTTPException(500, f"Could not transcribe audio: {exc}") from exc

    # ---- vault --------------------------------------------------------------
    @app.get("/api/vault/search")
    def vault_search(q: str = ""):
        return app.state.vault.search(q, limit=20)

    @app.get("/api/vault/note")
    def vault_note(path: str):
        return {"path": path, "content": app.state.vault.read(path)}

    @app.get("/api/todos")
    def todos():
        return app.state.vault.list_todos()

    @app.post("/api/todos")
    def add_todo(body: TodoBody):
        app.state.vault.add_todo(body.text)
        return app.state.vault.list_todos()

    @app.post("/api/todos/toggle")
    def toggle_todo(body: TodoToggle):
        app.state.vault.set_todo(body.line, body.done)
        return app.state.vault.list_todos()

    @app.post("/api/capture")
    def capture(body: TodoBody):
        return {"path": app.state.vault.capture(body.text)}

    # ---- web UI -------------------------------------------------------------
    index = settings.static_dir / "index.html"
    if (settings.static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=settings.static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = settings.static_dir / path
        if path and candidate.is_file() and settings.static_dir.resolve() in candidate.resolve().parents:
            return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({"detail": "Web UI not built. Run `npm run build` in frontend/."}, status_code=404)

    return app
