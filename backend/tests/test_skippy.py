import json
import os
import stat
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from skippy.config import Settings, expand_env
from skippy.main import create_app
from skippy.providers.openai_compat import OpenAICompatProvider
from skippy.vault import Vault, VaultError

FAKE_CLAUDE = Path(__file__).parent / "fake_claude.py"


def sse(*chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


@pytest.fixture
def env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "Projects").mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    (vault / "Projects" / "Billing API.md").write_text("# Billing API\nTalks to the ledger service over gRPC.\n")
    (vault / ".obsidian" / "secret.md").write_text("ledger")
    FAKE_CLAUDE.chmod(FAKE_CLAUDE.stat().st_mode | stat.S_IEXEC)
    cfg = tmp_path / "skippy.yaml"
    cfg.write_text(f"""
providers:
  - {{id: claude-code, kind: claude_code, binary: "{FAKE_CLAUDE}"}}
  - id: portkey
    kind: openai_compat
    base_url: ${{PK_URL:-https://gw.example/v1}}
    headers: {{x-portkey-api-key: "${{PK_KEY}}", x-portkey-virtual-key: "${{PK_VK}}"}}
    models: ${{PK_MODELS}}
""")
    monkeypatch.setenv("PK_KEY", "pk-test")
    monkeypatch.setenv("PK_MODELS", "gpt-test, other")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    settings = Settings(data_dir=tmp_path / "data", vault_path=vault, vault_folder="Skippy", vault_write=True,
                        config_path=cfg, static_dir=tmp_path / "static", whisper_model="base.en",
                        user_name="Dane", default_provider="portkey")
    app = create_app(settings)
    return TestClient(app), app, vault


def read_stream(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line]


def test_expand_env(monkeypatch):
    monkeypatch.setenv("A", "1")
    assert expand_env({"x": ["${A}", "${MISSING:-d}", "${MISSING}"]}) == {"x": ["1", "d", ""]}


def test_vault_paths_and_search(env):
    _, app, root = env
    vault: Vault = app.state.vault
    assert [h["path"] for h in vault.search("ledger grpc")] == ["Projects/Billing API.md"]
    for bad in ["../outside", "/etc/passwd", ".obsidian/secret"]:
        with pytest.raises(VaultError):
            vault.read(bad)
    vault.write("Skippy/New", "hi")
    with pytest.raises(VaultError):
        vault.write("Skippy/New.md", "again")
    assert (root / "Skippy" / "New.md").read_text() == "hi"


def test_todos_and_capture(env):
    client, _, root = env
    assert client.post("/api/todos", json={"text": "Ship  Skippy"}).json() == [
        {"line": 2, "done": False, "text": "Ship Skippy"}]
    done = client.post("/api/todos/toggle", json={"line": 2, "done": True}).json()
    assert done[0]["done"] and done[0]["text"] == "Ship Skippy"
    assert "- [x] Ship Skippy ✅" in (root / "Skippy" / "Todos.md").read_text()
    assert client.post("/api/todos/toggle", json={"line": 0, "done": True}).status_code == 400
    path = client.post("/api/capture", json={"text": "idea"}).json()["path"]
    assert path.startswith("Skippy/Daily/") and "idea" in (root / path).read_text()


def test_status(env):
    client, _, _ = env
    status = client.get("/api/status").json()
    assert status["default_provider"] == "portkey"
    portkey = next(p for p in status["providers"] if p["id"] == "portkey")
    assert portkey["available"] and portkey["models"] == ["gpt-test", "other"]
    assert status["vault"]["notes"] == 1


def test_portkey_chat_with_tool_round(env):
    client, app, root = env
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append((dict(request.headers), body))
        if len(seen) == 1:
            return sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "todo_add", "arguments": '{"text":'}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ' "Call Bob"}'}}]}}]})
        return sse({"choices": [{"delta": {"content": "Added "}}]}, {"choices": [{"delta": {"content": "it."}}]})

    OpenAICompatProvider.transport = httpx.MockTransport(handler)
    try:
        events = read_stream(client.post("/api/chat", json={"message": "add a todo to call Bob", "sass": 2}))
    finally:
        OpenAICompatProvider.transport = None

    kinds = [e["type"] for e in events]
    assert kinds == ["start", "tool", "delta", "delta", "done"]
    headers, first = seen[0]
    assert headers["x-portkey-api-key"] == "pk-test" and "x-portkey-virtual-key" not in headers
    assert "authorization" not in headers
    assert first["model"] == "gpt-test" and first["messages"][0]["role"] == "system"
    assert seen[1][1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "Added"}
    assert "- [ ] Call Bob" in (root / "Skippy" / "Todos.md").read_text()

    cid = events[0]["conversation_id"]
    saved = client.get(f"/api/conversations/{cid}").json()
    assert [m["content"] for m in saved["messages"]] == ["add a todo to call Bob", "Added it."]
    path = client.post(f"/api/conversations/{cid}/save").json()["path"]
    assert "Added it." in (root / path).read_text()


def test_upstream_error_is_reported(env):
    client, _, _ = env
    OpenAICompatProvider.transport = httpx.MockTransport(lambda r: httpx.Response(401, text="bad key"))
    try:
        events = read_stream(client.post("/api/chat", json={"message": "hi"}))
    finally:
        OpenAICompatProvider.transport = None
    assert events[1]["type"] == "error" and "401" in events[1]["message"]


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shebang")
def test_claude_code_provider(env):
    client, _, _ = env
    events = read_stream(client.post("/api/chat", json={"message": "hello there", "provider": "claude-code"}))
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text == "You said: hello there | tools=Read,Glob,Grep,Edit,Write"
    assert {"type": "tool", "name": "Read", "detail": "Skippy/Todos.md"} in events
    assert events[-1] == {"type": "done"}
