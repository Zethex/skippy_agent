"""The Obsidian vault: plain Markdown files on a mounted folder.

Skippy keeps its own notes under one folder (default `Skippy/`):
    Skippy/Todos.md            checkbox list shown in the web UI
    Skippy/Daily/YYYY-MM-DD.md quick captures
    Skippy/Chats/...           conversations saved from the UI
Everything else in the vault is readable and searchable.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

TASK = re.compile(r"^(\s*[-*] \[)([ xX])(\] )(.*)$")
DONE_STAMP = re.compile(r"\s*✅ \d{4}-\d{2}-\d{2}\s*$")
MAX_NOTE_BYTES = 2_000_000
MAX_READ_CHARS = 24_000


class VaultError(Exception):
    pass


class Vault:
    def __init__(self, root: Path, folder: str = "Skippy", writable: bool = True):
        self.root = Path(root)
        self.folder = folder
        self.writable = writable
        self._count: tuple[float, int] = (0.0, 0)

    # ---- basics -----------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.root.is_dir()

    def _resolve(self, rel: str) -> Path:
        if not self.available:
            raise VaultError("No vault is selected. Pick one in Settings.")
        rel = (rel or "").strip().replace("\\", "/").lstrip("/")
        if not rel:
            raise VaultError("A note path is required")
        if not Path(rel).suffix:
            rel += ".md"
        path = (self.root / rel).resolve()
        root = self.root.resolve()
        if root != path and root not in path.parents:
            raise VaultError("That path is outside the vault")
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            raise VaultError("Hidden folders such as .obsidian are off limits")
        return path

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def _need_write(self) -> None:
        if not self.available:
            raise VaultError("No vault is selected. Pick one in Settings.")
        if not self.writable:
            raise VaultError("The vault is read-only (turn on editing in Settings)")

    def iter_notes(self):
        for base, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if name.lower().endswith(".md"):
                    yield Path(base) / name

    def note_count(self) -> int:
        stamp, count = self._count
        if time.time() - stamp > 60:
            count = sum(1 for _ in self.iter_notes()) if self.available else 0
            self._count = (time.time(), count)
        return count

    # ---- read / search ----------------------------------------------------
    def read(self, rel: str) -> str:
        path = self._resolve(rel)
        if not path.is_file():
            raise VaultError(f"No note at {rel}")
        return path.read_text("utf-8", errors="replace")

    def search(self, query: str, limit: int = 8) -> list[dict]:
        terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 1]
        if not terms or not self.available:
            return []
        hits = []
        for path in self.iter_notes():
            try:
                if path.stat().st_size > MAX_NOTE_BYTES:
                    continue
                text = path.read_text("utf-8", errors="ignore")
            except OSError:
                continue
            low, name = text.lower(), path.stem.lower()
            score, matched, first = 0.0, 0, -1
            for term in terms:
                in_body, in_name = low.count(term), name.count(term)
                if in_body or in_name:
                    matched += 1
                if first < 0 and in_body:
                    first = low.find(term)
                score += min(in_body, 10) + 8 * in_name
            if not matched:
                continue
            start = max(first - 90, 0) if first >= 0 else 0
            snippet = re.sub(r"\s+", " ", text[start:start + 240]).strip()
            hits.append({
                "path": self._rel(path),
                "title": path.stem,
                "snippet": snippet,
                "score": round(score * matched / len(terms), 2),
            })
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:limit]

    # ---- write ------------------------------------------------------------
    def write(self, rel: str, content: str, overwrite: bool = False) -> str:
        self._need_write()
        path = self._resolve(rel)
        if path.exists() and not overwrite:
            raise VaultError(f"{self._rel(path)} already exists; pass overwrite=true or append instead")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, "utf-8")
        self._count = (0.0, 0)
        return self._rel(path)

    def append(self, rel: str, content: str, header: str = "") -> str:
        self._need_write()
        path = self._resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text("utf-8") if path.exists() else header
        if existing and not existing.endswith("\n"):
            existing += "\n"
        path.write_text(existing + content.rstrip("\n") + "\n", "utf-8")
        return self._rel(path)

    # ---- todos ------------------------------------------------------------
    @property
    def todo_path(self) -> str:
        return f"{self.folder}/Todos.md"

    def list_todos(self) -> list[dict]:
        if not self.available:
            return []
        path = self._resolve(self.todo_path)
        if not path.is_file():
            return []
        todos = []
        for number, line in enumerate(path.read_text("utf-8").splitlines()):
            m = TASK.match(line)
            if m:
                todos.append({"line": number, "done": m.group(2) != " ",
                              "text": DONE_STAMP.sub("", m.group(4))})
        return todos

    def add_todo(self, text: str) -> str:
        text = " ".join(text.split())
        if not text:
            raise VaultError("A todo needs some text")
        return self.append(self.todo_path, f"- [ ] {text}", header="# Todos\n\n")

    def set_todo(self, line: int, done: bool) -> None:
        self._need_write()
        path = self._resolve(self.todo_path)
        lines = path.read_text("utf-8").splitlines() if path.is_file() else []
        m = TASK.match(lines[line]) if 0 <= line < len(lines) else None
        if not m:
            raise VaultError("That todo has moved; refresh the list")
        text = DONE_STAMP.sub("", m.group(4))
        if done:
            text += f" ✅ {datetime.now():%Y-%m-%d}"
        lines[line] = f"{m.group(1)}{'x' if done else ' '}{m.group(3)}{text}"
        path.write_text("\n".join(lines) + "\n", "utf-8")

    # ---- daily capture ----------------------------------------------------
    def capture(self, text: str) -> str:
        text = text.strip()
        if not text:
            raise VaultError("Nothing to capture")
        now = datetime.now()
        rel = f"{self.folder}/Daily/{now:%Y-%m-%d}.md"
        body = text.replace("\n", "\n  ")
        return self.append(rel, f"- {now:%H:%M} {body}", header=f"# {now:%Y-%m-%d}\n\n")


# ---- function-calling tools for API providers ------------------------------
def tool_specs(vault: Vault) -> list[dict]:
    def fn(name, description, props, required):
        return {"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required}}}

    path = {"type": "string", "description": "Path relative to the vault root, e.g. Projects/Billing API.md"}
    specs = [
        fn("vault_search", "Full-text search across the user's Obsidian vault. Returns paths and snippets.",
           {"query": {"type": "string"}}, ["query"]),
        fn("vault_read", "Read one note from the vault.", {"path": path}, ["path"]),
        fn("todo_list", "List the items in the user's todo list.", {}, []),
    ]
    if vault.writable:
        specs += [
            fn("vault_write", "Create a note (or replace one when overwrite is true).",
               {"path": path, "content": {"type": "string"}, "overwrite": {"type": "boolean"}},
               ["path", "content"]),
            fn("vault_append", "Append Markdown to the end of a note, creating it if needed.",
               {"path": path, "content": {"type": "string"}}, ["path", "content"]),
            fn("todo_add", "Add one item to the user's todo list.", {"text": {"type": "string"}}, ["text"]),
        ]
    return specs


def run_tool(vault: Vault, name: str, args: dict) -> str:
    try:
        if name == "vault_search":
            return json.dumps(vault.search(str(args.get("query", ""))))
        if name == "vault_read":
            text = vault.read(str(args.get("path", "")))
            return text if len(text) <= MAX_READ_CHARS else text[:MAX_READ_CHARS] + "\n…(truncated)"
        if name == "todo_list":
            return json.dumps(vault.list_todos())
        if name == "vault_write":
            return "Wrote " + vault.write(str(args.get("path", "")), str(args.get("content", "")),
                                          bool(args.get("overwrite", False)))
        if name == "vault_append":
            return "Appended to " + vault.append(str(args.get("path", "")), str(args.get("content", "")))
        if name == "todo_add":
            vault.add_todo(str(args.get("text", "")))
            return "Added"
        return f"Unknown tool {name}"
    except (VaultError, OSError) as exc:
        return f"Error: {exc}"
