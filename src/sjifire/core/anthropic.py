"""Shared Anthropic API client and model configuration."""

import os

from anthropic import AsyncAnthropic

MODEL = "claude-sonnet-4-5-20250929"

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    """Get or create a shared Anthropic client (module-level singleton)."""
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    return _client


def cached_system(text: str) -> list[dict]:
    """Wrap system prompt text with cache_control for prompt caching."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
