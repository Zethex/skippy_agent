"""Settings that the web UI can change: profile, the Obsidian vault and the AI providers.

They live in /data/settings.json. On first start the file is seeded from .env and
config/skippy.yaml so existing setups keep working; after that the Settings screen is the
source of truth. Secrets (API keys, tokens) are stored here too, but are never sent back to
the browser: the API reports only whether each one is set.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

PROVIDER_KINDS = ("claude_code", "portkey", "openai_compat")
SECRET_FIELDS = ("oauth_token", "api_key", "virtual_key")
WHISPER_MODELS = ("tiny.en", "base.en", "small.en", "medium.en")
_SKIP_DIRS = {"node_modules", "AppData", "Library", "$RECYCLE.BIN", "System Volume Information"}


class SettingsError(ValueError):
    pass


def _bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _models(value: Any) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = value or []
    seen: list[str] = []
    for m in (str(i).strip() for i in items):
        if m and m not in seen:
            seen.append(m)
    return seen


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40] or "provider"


def clean_rel(path: Any) -> str:
    """A folder path relative to the vaults root. '' means no vault; '.' means the root itself."""
    rel = str(path or "").strip().replace("\\", "/").strip("/")
    if rel == "":
        return ""
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts:
        return "."
    if any(p == ".." or p.startswith(".") for p in parts):
        raise SettingsError("Vault paths can't contain '..' or hidden folders")
    return "/".join(parts)


def normalise_provider(raw: dict) -> dict | None:
    kind = raw.get("kind")
    if kind not in PROVIDER_KINDS:
        return None
    label = str(raw.get("label") or "").strip()[:60]
    out: dict[str, Any] = {
        "id": slug(raw.get("id") or label or kind),
        "label": label or {"claude_code": "Claude", "portkey": "Portkey"}.get(kind, "AI"),
        "kind": kind,
        "enabled": _bool(raw.get("enabled"), True),
        "models": _models(raw.get("models")),
    }
    if kind == "claude_code":
        out["oauth_token"] = str(raw.get("oauth_token") or "").strip()
        if raw.get("binary"):
            out["binary"] = str(raw["binary"])  # tests point this at a fake CLI; not exposed in the UI
    else:
        out["base_url"] = str(raw.get("base_url") or "").strip().rstrip("/")
        out["api_key"] = str(raw.get("api_key") or "").strip()
        out["tools"] = _bool(raw.get("tools"), True)
        out["verify_tls"] = _bool(raw.get("verify_tls"), True)
        if kind == "portkey":
            out["virtual_key"] = str(raw.get("virtual_key") or "").strip()
            out["config"] = str(raw.get("config") or "").strip()
            out["gateway_provider"] = str(raw.get("gateway_provider") or "").strip()
        else:
            headers = raw.get("headers") or {}
            out["headers"] = {str(k).strip(): str(v) for k, v in headers.items() if str(k).strip() and v not in (None, "")}
    return out


def normalise(raw: dict) -> dict:
    profile = raw.get("profile") or {}
    vault = raw.get("vault") or {}
    whisper = str(profile.get("whisper_model") or "base.en").strip()
    providers: list[dict] = []
    for item in raw.get("providers") or []:
        p = normalise_provider(item) if isinstance(item, dict) else None
        if not p:
            continue
        base, n = p["id"], 2
        while any(q["id"] == p["id"] for q in providers):
            p["id"], n = f"{base}-{n}", n + 1
        providers.append(p)
    default = str(raw.get("default_provider") or "")
    if default not in {p["id"] for p in providers}:
        default = ""
    return {
        "version": 1,
        "profile": {"user_name": str(profile.get("user_name") or "").strip()[:60],
                    "whisper_model": whisper if whisper in WHISPER_MODELS else "base.en"},
        "vault": {"path": clean_rel(vault.get("path")),
                  "folder": str(vault.get("folder") or "Skippy").strip().strip("/") or "Skippy",
                  "writable": _bool(vault.get("writable"), True)},
        "providers": providers,
        "default_provider": default,
    }


def runtime_provider(p: dict) -> dict:
    """Turn a stored provider into the config the provider classes take."""
    if p["kind"] == "claude_code":
        return dict(p)
    cfg = {k: p[k] for k in ("id", "label", "models", "base_url", "api_key", "tools", "verify_tls")}
    if p["kind"] == "portkey":
        # Portkey authenticates with its own header, not a bearer token.
        cfg["api_key"] = ""
        cfg["auth_header"] = "x-portkey-api-key"
        cfg["headers"] = {"x-portkey-api-key": p["api_key"], "x-portkey-virtual-key": p["virtual_key"],
                          "x-portkey-config": p["config"], "x-portkey-provider": p["gateway_provider"]}
    else:
        cfg["headers"] = dict(p.get("headers") or {})
    cfg["kind"] = "openai_compat"
    return cfg


def from_yaml_provider(raw: dict) -> dict:
    """Map a v0.1 skippy.yaml entry (already env-expanded) onto the stored shape."""
    item = dict(raw)
    headers = dict(item.get("headers") or {})
    if item.get("kind") == "openai_compat" and ("x-portkey-api-key" in headers or item.get("id") == "portkey"):
        item.update(kind="portkey", api_key=headers.pop("x-portkey-api-key", "") or item.get("api_key", ""),
                    virtual_key=headers.pop("x-portkey-virtual-key", ""), config=headers.pop("x-portkey-config", ""),
                    gateway_provider=headers.pop("x-portkey-provider", ""))
    item["headers"] = headers
    return item


class SettingsStore:
    def __init__(self, path: Path, seed: Callable[[], dict]):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = self._load(seed)

    def _load(self, seed: Callable[[], dict]) -> dict:
        if self.path.is_file():
            try:
                return normalise(json.loads(self.path.read_text("utf-8")))
            except (ValueError, SettingsError):
                broken = self.path.with_name(f"settings.broken-{int(time.time())}.json")
                shutil.move(str(self.path), broken)
        data = normalise(seed())
        self._write(data)
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    @property
    def data(self) -> dict:
        return copy.deepcopy(self._data)

    def provider(self, pid: str) -> dict | None:
        return next((copy.deepcopy(p) for p in self._data["providers"] if p["id"] == pid), None)

    def public(self) -> dict:
        """The settings as the browser sees them: every secret replaced by a `<name>_set` flag."""
        out = self.data
        for p in out["providers"]:
            for f in SECRET_FIELDS:
                if f in p:
                    p[f + "_set"] = bool(p.pop(f))
        return out

    def merge_secrets(self, incoming: dict, existing: dict | None) -> dict:
        """A secret left out (or null) keeps its stored value; '' clears it; a string replaces it."""
        merged = dict(incoming)
        for f in SECRET_FIELDS:
            if merged.get(f) is None:
                merged[f] = (existing or {}).get(f, "")
            merged.pop(f + "_set", None)
        return merged

    def update(self, patch: dict) -> dict:
        with self._lock:
            current = self.data
            if "profile" in patch:
                current["profile"].update(patch["profile"] or {})
            if "vault" in patch:
                current["vault"].update(patch["vault"] or {})
            if "providers" in patch:
                old = {p["id"]: p for p in current["providers"]}
                current["providers"] = [self.merge_secrets(p, old.get(str(p.get("id") or "")))
                                        for p in patch["providers"] or [] if isinstance(p, dict)]
            if "default_provider" in patch:
                current["default_provider"] = patch["default_provider"] or ""
            data = normalise(current)
            self._write(data)
            self._data = data
            return self.public()


# ---- vault discovery ---------------------------------------------------------------

def is_vault(path: Path) -> bool:
    return (path / ".obsidian").is_dir()


def find_vaults(root: Path, max_depth: int = 3, max_dirs: int = 3000) -> list[dict]:
    """Folders under the mounted root that Obsidian treats as vaults (they contain .obsidian/)."""
    found: list[dict] = []
    if not root.is_dir():
        return found
    queue: list[tuple[Path, int]] = [(root, 0)]
    visited = 0
    while queue and visited < max_dirs:
        folder, depth = queue.pop(0)
        visited += 1
        if is_vault(folder):
            rel = "." if folder == root else folder.relative_to(root).as_posix()
            found.append({"path": rel, "name": folder.name if folder != root else (root.name or "Vault")})
            continue  # vaults don't nest
        if depth >= max_depth:
            continue
        try:
            children = sorted((c for c in folder.iterdir() if c.is_dir()), key=lambda c: c.name.lower())
        except OSError:
            continue
        queue += [(c, depth + 1) for c in children if not c.name.startswith((".", "$")) and c.name not in _SKIP_DIRS]
    return found


def list_folders(root: Path, rel: str) -> dict:
    """Subfolders of one folder under the root, for the folder browser."""
    rel = clean_rel(rel) or "."
    folder = (root / rel).resolve() if rel != "." else root.resolve()
    if folder != root.resolve() and root.resolve() not in folder.parents:
        raise SettingsError("That folder is outside the mounted folder")
    if not folder.is_dir():
        raise SettingsError("That folder doesn't exist")
    items = []
    for c in sorted(folder.iterdir(), key=lambda c: c.name.lower()):
        if c.is_dir() and not c.name.startswith((".", "$")) and c.name not in _SKIP_DIRS:
            items.append({"path": c.relative_to(root.resolve()).as_posix(), "name": c.name, "vault": is_vault(c)})
    return {"path": rel, "is_vault": is_vault(folder), "folders": items}


def create_vault(root: Path, parent: str, name: str) -> str:
    name = name.strip()
    if not name or name.startswith(".") or re.search(r'[\\/:*?"<>|]', name):
        raise SettingsError("Pick a folder name without / \\ : * ? \" < > |")
    parent = clean_rel(parent)
    base = root / parent if parent not in ("", ".") else root
    target = base / name
    if target.exists():
        raise SettingsError(f"'{name}' already exists there")
    (target / ".obsidian").mkdir(parents=True)
    (target / "Welcome.md").write_text(
        f"# {name}\n\nCreated by Skippy. Open this folder in Obsidian as a vault to browse it.\n", "utf-8")
    return target.relative_to(root).as_posix()
