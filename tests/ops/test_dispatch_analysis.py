"""Tests for dispatch call LLM analysis."""

import json
from unittest.mock import AsyncMock, patch

from sjifire.ops.dispatch.analysis import (
    _build_prompt,
    _clean_json,
    analyze_dispatch,
)
from sjifire.ops.dispatch.models import DispatchAnalysis
from tests.factories import DispatchCallDocumentFactory

# ---------------------------------------------------------------------------
# Valid JSON the mock LLM can return
# ---------------------------------------------------------------------------

_VALID_ANALYSIS_JSON = json.dumps(
    {
        "incident_commander": "BN31",
        "summary": "Kitchen fire in single-family residence, contained and extinguished.",
        "actions_taken": ["Forced entry", "Applied water to seat of fire"],
        "patient_count": 0,
        "escalated": False,
        "outcome": "Fire extinguished",
        "short_dsc": "Kitchen fire, contained",
        "key_events": ["E31 on scene, smoke showing", "Fire knocked down"],
    }
)


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_basic_fields_in_prompt(self):
        doc = DispatchCallDocumentFactory.build(
            nature="Structure Fire",
            address="100 Main St",
            agency_code="SJF",
            responding_units="E31, BN31",
            responder_details=[],
            cad_comments="",
        )
        prompt = _build_prompt(doc)

        assert "Structure Fire" in prompt
        assert "100 Main St" in prompt
        assert "SJF" in prompt
        assert "E31, BN31" in prompt
        assert "Radio log (chronological):" in prompt

    def test_responder_details_included(self):
        doc = DispatchCallDocumentFactory.build(
            responder_details=[
                {
                    "unit_number": "E31",
                    "status": "Dispatched",
                    "radio_log": "Engine 31 responding",
                    "time_of_status_change": "2026-02-12T14:30:15",
                },
                {
                    "unit_number": "BN31",
                    "status": "On Scene",
                    "radio_log": "Battalion 31 assuming command",
                    "time_of_status_change": "2026-02-12T14:35:00",
                },
            ],
            cad_comments="",
        )
        prompt = _build_prompt(doc)

        assert "E31 [Dispatched]" in prompt
        assert "Engine 31 responding" in prompt
        assert "BN31 [On Scene]" in prompt
        assert "Battalion 31 assuming command" in prompt

    def test_responder_details_missing_keys(self):
        """Entries with missing keys should not crash, just produce empty strings."""
        doc = DispatchCallDocumentFactory.build(
            responder_details=[{"unit_number": "E31"}],
            cad_comments="",
        )
        prompt = _build_prompt(doc)
        assert "E31 []" in prompt

    def test_cad_comments_included(self):
        doc = DispatchCallDocumentFactory.build(
            responder_details=[],
            cad_comments="Patient fall, conscious and breathing",
        )
        prompt = _build_prompt(doc)

        assert "CAD comments:" in prompt
        assert "Patient fall, conscious and breathing" in prompt

    def test_no_cad_comments_section_when_empty(self):
        doc = DispatchCallDocumentFactory.build(
            responder_details=[],
            cad_comments="",
        )
        prompt = _build_prompt(doc)

        assert "CAD comments:" not in prompt

    def test_crew_context_included(self):
        doc = DispatchCallDocumentFactory.build(
            responder_details=[],
            cad_comments="",
        )
        crew = "On duty: Capt Smith (E31), FF Garcia (E31)"
        prompt = _build_prompt(doc, crew_context=crew)

        assert crew in prompt

    def test_no_crew_context_when_empty(self):
        doc = DispatchCallDocumentFactory.build(
            responder_details=[],
            cad_comments="",
        )
        prompt = _build_prompt(doc, crew_context="")
        lines = prompt.rstrip().split("\n")
        assert lines[-1] == "Radio log (chronological):"

    def test_time_reported_formatted(self):
        from datetime import datetime

        doc = DispatchCallDocumentFactory.build(
            time_reported=datetime(2026, 3, 10, 9, 15, 0),
            responder_details=[],
            cad_comments="",
        )
        prompt = _build_prompt(doc)
        assert "2026-03-10 09:15:00" in prompt

    def test_time_reported_none(self):
        doc = DispatchCallDocumentFactory.build(
            time_reported=None,
            responder_details=[],
            cad_comments="",
        )
        prompt = _build_prompt(doc)
        assert "N/A" in prompt


# ---------------------------------------------------------------------------
# _clean_json
# ---------------------------------------------------------------------------


class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"summary": "test"}'
        assert _clean_json(raw) == raw

    def test_strips_json_code_fence(self):
        raw = '```json\n{"summary": "test"}\n```'
        assert _clean_json(raw) == '{"summary": "test"}'

    def test_strips_plain_code_fence(self):
        raw = '```\n{"summary": "test"}\n```'
        assert _clean_json(raw) == '{"summary": "test"}'

    def test_strips_surrounding_whitespace(self):
        raw = '  \n  {"summary": "test"}  \n  '
        assert _clean_json(raw) == '{"summary": "test"}'

    def test_multiline_json_inside_fence(self):
        raw = '```json\n{\n  "summary": "test",\n  "outcome": "ok"\n}\n```'
        result = _clean_json(raw)
        parsed = json.loads(result)
        assert parsed["summary"] == "test"
        assert parsed["outcome"] == "ok"

    def test_no_fence_multiline_json(self):
        raw = '{\n  "summary": "test"\n}'
        assert _clean_json(raw) == raw.strip()


# ---------------------------------------------------------------------------
# _call_llm — provider routing
# ---------------------------------------------------------------------------


class TestCallLlm:
    async def test_returns_empty_when_no_provider(self, monkeypatch):
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        from sjifire.ops.dispatch.analysis import _call_llm

        result = await _call_llm("system", "user prompt")
        assert result == ""

    async def test_routes_to_azure_when_endpoint_set(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        mock_azure = AsyncMock(return_value='{"summary": "azure result"}')
        with patch("sjifire.ops.dispatch.analysis._call_azure_openai", mock_azure):
            from sjifire.ops.dispatch.analysis import _call_llm

            result = await _call_llm("system prompt", "user prompt")

        assert result == '{"summary": "azure result"}'
        mock_azure.assert_awaited_once_with("system prompt", "user prompt")

    async def test_routes_to_anthropic_when_api_key_set(self, monkeypatch):
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")

        mock_anthropic = AsyncMock(return_value='{"summary": "anthropic result"}')
        with patch("sjifire.ops.dispatch.analysis._call_anthropic", mock_anthropic):
            from sjifire.ops.dispatch.analysis import _call_llm

            result = await _call_llm("system prompt", "user prompt")

        assert result == '{"summary": "anthropic result"}'
        mock_anthropic.assert_awaited_once_with("system prompt", "user prompt")

    async def test_azure_takes_precedence_over_anthropic(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")

        mock_azure = AsyncMock(return_value='{"summary": "azure"}')
        mock_anthropic = AsyncMock(return_value='{"summary": "anthropic"}')
        with (
            patch("sjifire.ops.dispatch.analysis._call_azure_openai", mock_azure),
            patch("sjifire.ops.dispatch.analysis._call_anthropic", mock_anthropic),
        ):
            from sjifire.ops.dispatch.analysis import _call_llm

            result = await _call_llm("sys", "usr")

        assert result == '{"summary": "azure"}'
        mock_azure.assert_awaited_once()
        mock_anthropic.assert_not_awaited()


# ---------------------------------------------------------------------------
# analyze_dispatch — integration
# ---------------------------------------------------------------------------


class TestAnalyzeDispatch:
    async def test_empty_doc_returns_empty_analysis(self):
        doc = DispatchCallDocumentFactory.build(
            responder_details=[],
            cad_comments="",
        )
        result = await analyze_dispatch(doc)

        assert result == DispatchAnalysis()
        assert result.summary == ""
        assert result.incident_commander == ""

    async def test_successful_analysis(self):
        doc = DispatchCallDocumentFactory.build(
            nature="Structure Fire",
            type="FIRE",
            responder_details=[
                {
                    "unit_number": "E31",
                    "status": "On Scene",
                    "radio_log": "Smoke showing from roof",
                    "time_of_status_change": "2026-03-10T09:20:00",
                },
            ],
            cad_comments="Visible flames from kitchen window",
        )

        with patch(
            "sjifire.ops.dispatch.analysis._call_llm",
            AsyncMock(return_value=_VALID_ANALYSIS_JSON),
        ):
            result = await analyze_dispatch(doc)

        assert result.incident_commander == "BN31"
        assert "Kitchen fire" in result.summary
        assert result.actions_taken == ["Forced entry", "Applied water to seat of fire"]
        assert result.patient_count == 0
        assert result.escalated is False
        assert result.outcome == "Fire extinguished"
        assert result.short_dsc == "Kitchen fire, contained"
        assert len(result.key_events) == 2

    async def test_llm_returns_empty_string(self):
        doc = DispatchCallDocumentFactory.build(
            responder_details=[{"unit_number": "E31", "status": "Dispatched"}],
            cad_comments="Some comments",
        )

        with patch(
            "sjifire.ops.dispatch.analysis._call_llm",
            AsyncMock(return_value=""),
        ):
            result = await analyze_dispatch(doc)

        assert result == DispatchAnalysis()

    async def test_llm_returns_invalid_json(self):
        doc = DispatchCallDocumentFactory.build(
            responder_details=[{"unit_number": "E31", "status": "Dispatched"}],
            cad_comments="Active call",
        )

        with patch(
            "sjifire.ops.dispatch.analysis._call_llm",
            AsyncMock(return_value="This is not JSON at all."),
        ):
            result = await analyze_dispatch(doc)

        assert result == DispatchAnalysis()

    async def test_llm_returns_json_in_code_fence(self):
        fenced = f"```json\n{_VALID_ANALYSIS_JSON}\n```"
        doc = DispatchCallDocumentFactory.build(
            responder_details=[{"unit_number": "E31", "status": "On Scene"}],
            cad_comments="Test",
        )

        with patch(
            "sjifire.ops.dispatch.analysis._call_llm",
            AsyncMock(return_value=fenced),
        ):
            result = await analyze_dispatch(doc)

        assert result.incident_commander == "BN31"
        assert result.outcome == "Fire extinguished"

    async def test_crew_context_passed_to_prompt(self):
        doc = DispatchCallDocumentFactory.build(
            responder_details=[{"unit_number": "E31", "status": "Dispatched"}],
            cad_comments="Test",
        )
        crew = "On duty: Capt Smith (E31), FF Garcia (E31)"

        captured_prompts = []

        async def capture_llm(_system, user_prompt):
            captured_prompts.append(user_prompt)
            return _VALID_ANALYSIS_JSON

        with patch(
            "sjifire.ops.dispatch.analysis._call_llm",
            side_effect=capture_llm,
        ):
            await analyze_dispatch(doc, crew_context=crew)

        assert len(captured_prompts) == 1
        assert crew in captured_prompts[0]

    async def test_only_cad_comments_triggers_analysis(self):
        doc = DispatchCallDocumentFactory.build(
            responder_details=[],
            cad_comments="Patient complaining of chest pain",
        )

        with patch(
            "sjifire.ops.dispatch.analysis._call_llm",
            AsyncMock(return_value=_VALID_ANALYSIS_JSON),
        ):
            result = await analyze_dispatch(doc)

        assert result.incident_commander == "BN31"

    async def test_partial_json_fields_use_defaults(self):
        partial = json.dumps({"summary": "Brief fire", "outcome": "Extinguished"})
        doc = DispatchCallDocumentFactory.build(
            responder_details=[{"unit_number": "E31", "status": "Dispatched"}],
            cad_comments="Test",
        )

        with patch(
            "sjifire.ops.dispatch.analysis._call_llm",
            AsyncMock(return_value=partial),
        ):
            result = await analyze_dispatch(doc)

        assert result.summary == "Brief fire"
        assert result.outcome == "Extinguished"
        assert result.incident_commander == ""
        assert result.actions_taken == []
        assert result.patient_count == 0
