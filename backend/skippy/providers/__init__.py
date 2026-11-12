from __future__ import annotations

from .base import ChatRequest, Provider
from .claude_code import ClaudeCodeProvider
from .openai_compat import OpenAICompatProvider

KINDS = {"claude_code": ClaudeCodeProvider, "openai_compat": OpenAICompatProvider}


class Registry:
    def __init__(self, configs: list[dict], preferred: str = ""):
        self.providers: dict[str, Provider] = {}
        for cfg in configs:
            cls = KINDS.get(cfg.get("kind", ""))
            if cls and cfg.get("id"):
                self.providers[cfg["id"]] = cls(cfg)
        self.preferred = preferred

    def default_id(self) -> str | None:
        pref = self.providers.get(self.preferred)
        if pref and pref.availability()[0]:
            return pref.id
        for provider in self.providers.values():
            if provider.availability()[0]:
                return provider.id
        return None

    def get(self, provider_id: str | None) -> Provider | None:
        return self.providers.get(provider_id or self.default_id() or "")


__all__ = ["ChatRequest", "Provider", "Registry"]
