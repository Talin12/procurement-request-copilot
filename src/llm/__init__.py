"""Reasoner selection.

Default is `auto`: use the hosted model when credentials and the SDK are both
present, otherwise fall back to the offline reasoner so the product and the
evaluation still run end to end on a clean machine. Set LLM_PROVIDER to
`anthropic` or `offline` to pin the choice.
"""
from __future__ import annotations

import os

from src.llm.base import Narration, Reasoner, ToolCall
from src.llm.offline import OfflineReasoner

__all__ = ["Narration", "Reasoner", "ToolCall", "get_reasoner", "describe_provider"]


def _anthropic_available() -> bool:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def get_reasoner(provider: str | None = None) -> Reasoner:
    choice = (provider or os.getenv("LLM_PROVIDER") or "auto").strip().lower()

    if choice in {"offline", "deterministic", "none"}:
        return OfflineReasoner()

    if choice in {"auto", "anthropic", "claude"}:
        if _anthropic_available():
            from src.llm.anthropic_reasoner import AnthropicReasoner

            return AnthropicReasoner()
        if choice != "auto":
            raise RuntimeError(
                "LLM_PROVIDER=anthropic was requested but ANTHROPIC_API_KEY is unset or the "
                "`anthropic` package is not installed. Run `pip install -r requirements.txt` "
                "and set the key in .env, or use LLM_PROVIDER=offline."
            )
        return OfflineReasoner()

    raise RuntimeError(f"Unknown LLM_PROVIDER: {choice!r}. Use 'auto', 'anthropic' or 'offline'.")


def describe_provider(reasoner: Reasoner) -> str:
    return f"{reasoner.name} ({'hosted model' if reasoner.is_model_backed else 'local deterministic reasoner'})"
