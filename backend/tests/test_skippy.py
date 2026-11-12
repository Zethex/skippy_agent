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


def test_settings_seeded_and_secrets_hidden(env, tmp_path):
    client, _, _ = env
    s = client.get("/api/settings").json()
    assert s["vault"]["path"] == "vault"
    portkey = next(p for p in s["providers"] if p["id"] == "portkey")
    assert portkey["kind"] == "portkey" and portkey["api_key_set"] and "api_key" not in portkey
    assert "pk-test" not in json.dumps(s)
    stored = json.loads((tmp_path / "data" / "settings.json").read_text())
    assert next(p for p in stored["providers"] if p["id"] == "portkey")["api_key"] == "pk-test"


def test_settings_update_applies_live_and_keeps_secrets(env, tmp_path):
    client, app, _ = env
    s = client.get("/api/settings").json()
    for p in s["providers"]:
        if p["id"] == "portkey":
            p["models"] = ["new-model"]
            p["virtual_key"] = "vk-1"          # new secret
        else:
            p["enabled"] = False
    s["profile"]["user_name"] = "Zethe"
    client.put("/api/settings", json=s)
    status = client.get("/api/status").json()
    assert status["user_name"] == "Zethe" and [p["id"] for p in status["providers"]] == ["portkey"]
    assert status["providers"][0]["models"] == ["new-model"]
    stored = json.loads((tmp_path / "data" / "settings.json").read_text())
    pk = next(p for p in stored["providers"] if p["id"] == "portkey")
    assert pk["api_key"] == "pk-test" and pk["virtual_key"] == "vk-1"   # untouched secret kept
    assert app.state.registry.get("portkey").headers["x-portkey-virtual-key"] == "vk-1"

    s = client.get("/api/settings").json()
    next(p for p in s["providers"] if p["id"] == "portkey")["api_key"] = ""   # explicit clear
    client.put("/api/settings", json=s)
    assert not client.get("/api/status").json()["providers"][0]["available"]


def test_vault_picker(env, tmp_path):
    client, _, _ = env
    assert client.get("/api/settings/vaults").json() == [{"path": "vault", "name": "vault"}]
    created = client.post("/api/settings/vaults", json={"name": "Work Brain"}).json()["path"]
    assert created == "Work Brain" and (tmp_path / "Work Brain" / ".obsidian").is_dir()
    assert {v["path"] for v in client.get("/api/settings/vaults").json()} == {"vault", "Work Brain"}
    assert client.put("/api/settings", json={"vault": {"path": "Work Brain", "writable": False}}).status_code == 200
    status = client.get("/api/status").json()
    assert status["vault"]["name"] == "Work Brain" and not status["vault"]["writable"]
    assert client.post("/api/todos", json={"text": "x"}).status_code == 400
    assert client.put("/api/settings", json={"vault": {"path": "../etc"}}).status_code == 400
    assert client.put("/api/settings", json={"vault": {"path": "nope"}}).status_code == 400
    assert client.get("/api/settings/folders", params={"path": "../.."}).status_code == 400


def test_provider_connection_check(env):
    client, _, _ = env
    seen = []

    def handler(request: httpx.Request):
        seen.append(dict(request.headers))
        return httpx.Response(200, json={"data": [{"id": "gpt-b"}, {"id": "gpt-a"}]})

    OpenAICompatProvider.transport = httpx.MockTransport(handler)
    try:
        # api_key left out: the saved one is used
        result = client.post("/api/settings/test", json={"id": "portkey", "kind": "portkey",
                                                         "base_url": "https://gw.example/v1"}).json()
    finally:
        OpenAICompatProvider.transport = None
    assert result["ok"] and result["models"] == ["gpt-a", "gpt-b"]
    assert seen[0]["x-portkey-api-key"] == "pk-test"
