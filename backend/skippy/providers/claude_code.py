"""Claude via the Claude Code CLI in headless mode, so a Pro/Max subscription works
without an API key. Auth comes from CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`).

The CLI runs with the vault as its working directory and only its file tools enabled,
which is how this provider reads and writes the second brain.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from .base import ChatRequest, Provider


def render_prompt(messages: list[dict]) -> str:
    """The CLI takes one prompt, so earlier turns are replayed as a transcript."""
    if len(messages) <= 1:
        return messages[-1]["content"] if messages else ""
    parts = ["Our conversation so far:\n"]
    for m in messages[:-1]:
        parts.append(f"<{m['role']}>\n{m['content']}\n</{m['role']}>\n")
    parts.append("Reply to my latest message:\n\n" + messages[-1]["content"])
    return "\n".join(parts)


def _tool_detail(tool_input: dict) -> str:
    for key in ("file_path", "pattern", "path", "query"):
        if tool_input.get(key):
            return str(tool_input[key])
    return ""


class ClaudeCodeProvider(Provider):
    kind = "claude_code"
    tool_style = "files"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.binary = cfg.get("binary") or "claude"
        if not self.models:
            self.models = ["sonnet", "opus", "haiku"]

    def availability(self) -> tuple[bool, str]:
        if not shutil.which(self.binary):
            return False, "The Claude Code CLI is not installed in this image"
        has_auth = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
                    or (Path.home() / ".claude" / ".credentials.json").exists())
        if not has_auth:
            return False, "Set CLAUDE_CODE_OAUTH_TOKEN in .env (run `claude setup-token` to get one)"
        return True, ""

    async def stream(self, req: ChatRequest):
        vault = req.vault if req.vault and req.vault.available else None
        tools = ""
        if vault:
            tools = "Read,Glob,Grep" + (",Edit,Write" if vault.writable else "")
        cmd = [self.binary, "-p", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--no-session-persistence", "--strict-mcp-config",
               "--disable-slash-commands", "--system-prompt", req.system, "--tools", tools]
        if tools:
            cmd += ["--allowedTools", tools]
            if vault.writable:
                cmd += ["--permission-mode", "acceptEdits"]
        if req.model:
            cmd += ["--model", req.model]

        scratch = None if vault else tempfile.mkdtemp(prefix="skippy-")
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(vault.root) if vault else scratch,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
        stderr_task = asyncio.create_task(proc.stderr.read())
        try:
            proc.stdin.write(render_prompt(req.messages).encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

            sent_text = finished = False
            async for raw in proc.stdout:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                kind = event.get("type")
                if kind == "stream_event":
                    inner = event.get("event") or {}
                    if inner.get("type") == "message_start" and sent_text:
                        yield {"type": "delta", "text": "\n\n"}
                    delta = inner.get("delta") or {}
                    if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        sent_text = True
                        yield {"type": "delta", "text": delta.get("text", "")}
                elif kind == "assistant":
                    for block in (event.get("message") or {}).get("content") or []:
                        if block.get("type") == "tool_use":
                            yield {"type": "tool", "name": block.get("name", "tool"),
                                   "detail": _tool_detail(block.get("input") or {})}
                elif kind == "result":
                    finished = True
                    if event.get("is_error"):
                        yield {"type": "error", "message": str(event.get("result") or event.get("subtype"))}
                    elif not sent_text and event.get("result"):
                        yield {"type": "delta", "text": str(event["result"])}
            code = await proc.wait()
            if not finished:
                err = (await stderr_task).decode("utf-8", errors="replace").strip()[-600:]
                yield {"type": "error", "message": err or f"Claude Code exited with status {code}"}
        finally:
            if proc.returncode is None:
                proc.kill()
            stderr_task.cancel()
            if scratch:
                shutil.rmtree(scratch, ignore_errors=True)
