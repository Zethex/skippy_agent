from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from ..vault import Vault


@dataclass
class ChatRequest:
    system: str
    messages: list[dict]          # [{"role": "user"|"assistant", "content": str}, ...]
    model: str | None = None
    vault: Vault | None = None


def as_models(value) -> list[str]:
    if isinstance(value, str):
        return [m.strip() for m in value.split(",") if m.strip()]
    return [str(m) for m in (value or [])]


class Provider:
    """One way of reaching a model. `stream` yields events:
        {"type": "delta", "text": "..."}            a piece of the reply
        {"type": "tool", "name": "...", "detail": "..."}  the model used a tool
        {"type": "error", "message": "..."}
    """
    kind = "base"
    tool_style = "none"   # how this provider reaches the vault: files | functions | none

    def __init__(self, cfg: dict):
        self.id: str = cfg["id"]
        self.label: str = cfg.get("label") or self.id
        self.models: list[str] = as_models(cfg.get("models"))

    def availability(self) -> tuple[bool, str]:
        return True, ""

    def describe(self) -> dict:
        ok, reason = self.availability()
        return {"id": self.id, "label": self.label, "kind": self.kind,
                "models": self.models, "available": ok, "reason": reason}

    async def check(self) -> dict:
        """Settings' "Test connection": {"ok": bool, "message": str, "models"?: [...]}."""
        ok, reason = self.availability()
        return {"ok": ok, "message": reason or "Looks configured"}

    def stream(self, req: ChatRequest) -> AsyncIterator[dict]:
        raise NotImplementedError
