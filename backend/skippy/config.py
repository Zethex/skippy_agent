"""Runtime settings. Everything comes from environment variables (see .env.*.example)
plus config/skippy.yaml for the list of AI providers."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_ENV = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-([^}]*))?\}")


def expand_env(value: Any) -> Any:
    """Replace ${VAR} and ${VAR:-default} inside strings, recursively."""
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path
    vault_path: Path | None        # legacy single-vault mount; seeds the vault setting on first run
    vault_folder: str
    vault_write: bool
    config_path: Path
    static_dir: Path
    whisper_model: str
    user_name: str
    default_provider: str
    vaults_root: Path | None = None  # folder the UI can pick vaults from (mounted at /vaults)

    @property
    def vaults_dir(self) -> Path:
        if self.vaults_root is not None:
            return self.vaults_root
        return self.vault_path.parent if self.vault_path else Path("/vaults")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("SKIPPY_DATA_DIR", "/data")),
            vault_path=Path(os.environ["SKIPPY_VAULT_PATH"]) if os.environ.get("SKIPPY_VAULT_PATH") else None,
            vaults_root=Path(os.environ.get("SKIPPY_VAULTS_ROOT", "/vaults")),
            vault_folder=os.environ.get("SKIPPY_VAULT_FOLDER", "Skippy").strip("/") or "Skippy",
            vault_write=_bool("SKIPPY_VAULT_WRITE", True),
            config_path=Path(os.environ.get("SKIPPY_CONFIG", "/app/config/skippy.yaml")),
            static_dir=Path(os.environ.get("SKIPPY_STATIC_DIR", "/app/static")),
            whisper_model=os.environ.get("WHISPER_MODEL", "base.en"),
            user_name=os.environ.get("SKIPPY_USER_NAME", "").strip(),
            default_provider=os.environ.get("SKIPPY_PROVIDER", "").strip(),
        )

    def load_provider_config(self) -> list[dict]:
        if not self.config_path.is_file():
            return [{"id": "claude-code", "label": "Claude (subscription)", "kind": "claude_code"}]
        raw = yaml.safe_load(self.config_path.read_text("utf-8")) or {}
        return expand_env(raw.get("providers") or [])

    def seed(self) -> dict:
        """First-run settings, built from .env and config/skippy.yaml (the v0.1 way of configuring)."""
        from .settings_store import from_yaml_provider, is_vault

        providers = []
        for raw in self.load_provider_config():
            item = from_yaml_provider(raw)
            if item.get("kind") == "claude_code":
                binary = item.get("binary") or "claude"
                if not shutil.which(binary):
                    continue
                item["oauth_token"] = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
            elif not item.get("base_url"):
                continue  # an example entry with nothing filled in
            providers.append(item)

        root = self.vaults_dir
        vault_rel = ""
        if self.vault_path and self.vault_path.is_dir():
            try:
                vault_rel = self.vault_path.resolve().relative_to(root.resolve()).as_posix() or "."
            except ValueError:
                vault_rel = ""
        elif is_vault(root):
            vault_rel = "."  # the mounted folder is itself a vault
        return {
            "profile": {"user_name": self.user_name, "whisper_model": self.whisper_model},
            "vault": {"path": vault_rel, "folder": self.vault_folder, "writable": self.vault_write},
            "providers": providers,
            "default_provider": self.default_provider,
        }
