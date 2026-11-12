"""Any OpenAI-compatible chat completions endpoint: a Portkey gateway, OpenAI itself,
Ollama, LiteLLM and so on. Portkey is just this with `x-portkey-*` headers.

The vault is exposed to the model as function-calling tools.
"""
from __future__ import annotations

import json
import os
import ssl

import httpx

from ..vault import run_tool, tool_specs
from .base import ChatRequest, Provider

MAX_TOOL_ROUNDS = 8


def _ssl_context() -> ssl.SSLContext | bool:
    """Trust the image's system certificate store (SSL_CERT_FILE, set in the Dockerfile), so a
    corporate base image's CA certificates apply. SKIPPY_CA_BUNDLE still overrides it."""
    bundle = os.environ.get("SKIPPY_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if bundle and os.path.isfile(bundle):
        return ssl.create_default_context(cafile=bundle)
    return True


def _truthy(value, default=True) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class OpenAICompatProvider(Provider):
    kind = "openai_compat"
    tool_style = "functions"
    transport = None  # tests swap in an httpx.MockTransport

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.api_key = cfg.get("api_key") or ""
        self.headers = {k: str(v) for k, v in (cfg.get("headers") or {}).items() if v not in (None, "")}
        self.use_tools = _truthy(cfg.get("tools"))
        self.verify_tls = _truthy(cfg.get("verify_tls"))
        self.auth_header = cfg.get("auth_header") or ""  # header that must carry the key (Portkey)
        if not self.use_tools:
            self.tool_style = "none"

    def availability(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "No base URL configured"
        if self.auth_header and not self.headers.get(self.auth_header):
            return False, "No API key configured"
        if not (self.api_key or self.headers):
            return False, "No API key configured"
        if not self.models:
            return False, "No models listed for this provider"
        return True, ""

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict = {"timeout": httpx.Timeout(300, connect=20)}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        else:
            kwargs["verify"] = _ssl_context() if self.verify_tls else False
        return httpx.AsyncClient(**kwargs)

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", **self.headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def check(self) -> dict:
        """List the gateway's models; if it has no /models, send a one-token request instead."""
        if not self.base_url:
            return {"ok": False, "message": "Enter a base URL"}
        if not (self.api_key or self.headers) or (self.auth_header and not self.headers.get(self.auth_header)):
            return {"ok": False, "message": "Enter an API key"}
        try:
            async with self._client() as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers(), timeout=20)
                if resp.status_code < 400:
                    try:
                        found = sorted({str(m["id"]) for m in resp.json().get("data") or [] if m.get("id")})
                    except (ValueError, AttributeError, TypeError):
                        found = []
                    if found:
                        return {"ok": True, "message": f"Connected. The gateway offers {len(found)} models.", "models": found}
                if resp.status_code in (401, 403):
                    return {"ok": False, "message": f"The key was rejected ({resp.status_code}): {resp.text[:300]}"}
                if not self.models:
                    return {"ok": False, "message": "Connected, but the gateway doesn't list its models. Enter at least one model name."}
                resp = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), timeout=60,
                                         json={"model": self.models[0], "max_tokens": 5,
                                               "messages": [{"role": "user", "content": "Reply with OK"}]})
                if resp.status_code >= 400:
                    return {"ok": False, "message": f"{resp.status_code} from the gateway: {resp.text[:300]}"}
                return {"ok": True, "message": f"Connected. {self.models[0]} answered."}
        except httpx.HTTPError as exc:
            return {"ok": False, "message": f"Could not reach {self.base_url}: {exc!r}"}

    async def stream(self, req: ChatRequest):
        headers = self._headers()
        messages = [{"role": "system", "content": req.system}, *req.messages]
        vault = req.vault if self.use_tools and req.vault and req.vault.available else None
        payload_base = {"model": req.model or self.models[0], "stream": True}
        if vault:
            payload_base["tools"] = tool_specs(vault)

        sent_text = False
        async with self._client() as client:
            for _ in range(MAX_TOOL_ROUNDS):
                calls: dict[int, dict] = {}
                text = ""
                try:
                    async with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers,
                                             json={**payload_base, "messages": messages}) as resp:
                        if resp.status_code >= 400:
                            body = (await resp.aread()).decode("utf-8", errors="replace")[:600]
                            yield {"type": "error", "message": f"{self.label} returned {resp.status_code}: {body}"}
                            return
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except ValueError:
                                continue
                            if chunk.get("error"):
                                yield {"type": "error", "message": json.dumps(chunk["error"])[:600]}
                                return
                            for choice in chunk.get("choices") or []:
                                delta = choice.get("delta") or {}
                                if delta.get("content"):
                                    if sent_text and not text:
                                        yield {"type": "delta", "text": "\n\n"}
                                    text += delta["content"]
                                    sent_text = True
                                    yield {"type": "delta", "text": delta["content"]}
                                for tc in delta.get("tool_calls") or []:
                                    slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                                    fn = tc.get("function") or {}
                                    slot["id"] = tc.get("id") or slot["id"]
                                    slot["name"] = fn.get("name") or slot["name"]
                                    slot["args"] += fn.get("arguments") or ""
                except httpx.HTTPError as exc:
                    yield {"type": "error", "message": f"Could not reach {self.label}: {exc!r}"}
                    return

                if not calls or not vault:
                    return
                ordered = [calls[i] for i in sorted(calls)]
                messages.append({"role": "assistant", "content": text or None, "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": c["args"] or "{}"}} for c in ordered]})
                for call in ordered:
                    try:
                        args = json.loads(call["args"] or "{}")
                    except ValueError:
                        args = {}
                    detail = str(args.get("path") or args.get("query") or args.get("text") or "")
                    yield {"type": "tool", "name": call["name"], "detail": detail}
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": run_tool(vault, call["name"], args)})
        yield {"type": "error", "message": "Stopped after too many tool calls in one reply"}
