from __future__ import annotations

import json
import re
from datetime import datetime

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import Settings
from .db import DB
from .persona import SASS_LEVELS, build_system_prompt
from .providers import ChatRequest, Registry
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
    vault = Vault(settings.vault_path, settings.vault_folder, settings.vault_write)
    registry = Registry(settings.load_provider_config(), settings.default_provider)
    stt = Transcriber(settings.whisper_model, settings.data_dir / "whisper")
    app.state.registry, app.state.vault, app.state.db = registry, vault, db

    @app.exception_handler(VaultError)
    async def vault_error(_request, exc: VaultError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # ---- status -------------------------------------------------------------
    @app.get("/api/status")
    def status():
        return {
            "version": __version__,
            "user_name": settings.user_name,
            "providers": [p.describe() for p in registry.providers.values()],
            "default_provider": registry.default_id(),
            "sass_levels": SASS_LEVELS,
            "vault": {"available": vault.available, "writable": vault.writable,
                      "folder": vault.folder, "notes": vault.note_count()},
            "voice": {"available": stt.available, "model": settings.whisper_model},
        }

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
        path = vault.write(f"{vault.folder}/Chats/{day} {safe}.md", "\n".join(lines), overwrite=True)
        return {"path": path}

    # ---- chat ---------------------------------------------------------------
    @app.post("/api/chat")
    async def chat(body: ChatBody):
        text = body.message.strip()
        if not text:
            raise HTTPException(400, "Message is empty")
        provider = registry.get(body.provider)
        if provider is None:
            raise HTTPException(400, "No AI provider is available. Add credentials to .env and restart.")
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
            system=build_system_prompt(body.sass, settings.user_name, vault, provider.tool_style),
            messages=history, model=model, vault=vault)

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
        if not stt.available:
            raise HTTPException(503, "Voice input is not installed in this image (build with INSTALL_WHISPER=true)")
        data = await audio.read()
        if len(data) < 800:
            return {"text": ""}
        try:
            return {"text": await run_in_threadpool(stt.transcribe, data)}
        except Exception as exc:
            raise HTTPException(500, f"Could not transcribe audio: {exc}") from exc

    # ---- vault --------------------------------------------------------------
    @app.get("/api/vault/search")
    def vault_search(q: str = ""):
        return vault.search(q, limit=20)

    @app.get("/api/vault/note")
    def vault_note(path: str):
        return {"path": path, "content": vault.read(path)}

    @app.get("/api/todos")
    def todos():
        return vault.list_todos()

    @app.post("/api/todos")
    def add_todo(body: TodoBody):
        vault.add_todo(body.text)
        return vault.list_todos()

    @app.post("/api/todos/toggle")
    def toggle_todo(body: TodoToggle):
        vault.set_todo(body.line, body.done)
        return vault.list_todos()

    @app.post("/api/capture")
    def capture(body: TodoBody):
        return {"path": vault.capture(body.text)}

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
