"""Tests for the shared Anthropic client and helpers."""

import sjifire.core.anthropic as anthropic_mod


class TestGetClient:
    def test_returns_async_anthropic_instance(self, monkeypatch):
        monkeypatch.setattr(anthropic_mod, "_client", None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        client = anthropic_mod.get_client()

        from anthropic import AsyncAnthropic

        assert isinstance(client, AsyncAnthropic)

    def test_returns_same_singleton(self, monkeypatch):
        monkeypatch.setattr(anthropic_mod, "_client", None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        first = anthropic_mod.get_client()
        second = anthropic_mod.get_client()

        assert first is second

    def test_works_without_api_key(self, monkeypatch):
        monkeypatch.setattr(anthropic_mod, "_client", None)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        client = anthropic_mod.get_client()
        assert client is not None


class TestCachedSystem:
    def test_returns_list_with_cache_control(self):
        result = anthropic_mod.cached_system("Hello system")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "Hello system"
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_preserves_full_text(self):
        long_text = "A" * 10_000
        result = anthropic_mod.cached_system(long_text)
        assert result[0]["text"] == long_text


class TestModelConstant:
    def test_model_is_sonnet(self):
        assert "sonnet" in anthropic_mod.MODEL
