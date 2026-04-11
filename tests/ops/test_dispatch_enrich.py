"""Tests for ``sjifire.ops.dispatch.enrich``.

Focuses on the PII safety net (``_scrub_llm_output``) and its integration
with ``enrich_dispatch``. The LLM-calling layer is covered by
``test_dispatch_analysis.py``.
"""

from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.dispatch.enrich import _scrub_llm_output, enrich_dispatch
from sjifire.ops.dispatch.models import (
    CrewOnDuty,
    DispatchAnalysis,
    DispatchCallDocument,
    SanitizedNote,
    UnitTiming,
)
from tests.factories import DispatchCallDocumentFactory

# ---------------------------------------------------------------------------
# _scrub_llm_output
# ---------------------------------------------------------------------------


class TestScrubLlmOutput:
    """The regex safety net catches PII the LLM missed or echoed."""

    def test_scrubs_summary(self):
        analysis = DispatchAnalysis(summary="13yo female fell from bike")
        _scrub_llm_output(analysis)
        assert "13yo" not in analysis.summary
        assert "[patient]" in analysis.summary

    def test_scrubs_short_dsc(self):
        analysis = DispatchAnalysis(short_dsc="72yo male fall, transported")
        _scrub_llm_output(analysis)
        assert "72yo" not in analysis.short_dsc

    def test_scrubs_outcome(self):
        analysis = DispatchAnalysis(outcome="transported 55yo female to PeaceHealth")
        _scrub_llm_output(analysis)
        assert "55yo" not in analysis.outcome
        assert "PeaceHealth" in analysis.outcome

    def test_scrubs_actions_taken(self):
        analysis = DispatchAnalysis(
            actions_taken=[
                "Forced entry",
                "BLS assessment on 72yo male patient",
                "Applied water",
            ]
        )
        _scrub_llm_output(analysis)
        assert "72yo" not in analysis.actions_taken[1]
        assert "BLS assessment" in analysis.actions_taken[1]

    def test_scrubs_key_events(self):
        analysis = DispatchAnalysis(
            key_events=[
                "14:38 E31 — nothing showing",
                "14:40 M31 — pt contact, 13yo female, conscious",
            ]
        )
        _scrub_llm_output(analysis)
        assert "13yo" not in analysis.key_events[1]
        assert analysis.key_events[0] == "14:38 E31 — nothing showing"

    def test_scrubs_sanitized_cad_comments(self):
        analysis = DispatchAnalysis(
            sanitized_cad_comments=(
                "18:56:01 02/12/2026 - M Rennick\n"
                "13yo female passenger, possible injury\n"
                "callback (360) 555-1234"
            )
        )
        _scrub_llm_output(analysis)
        assert "13yo" not in analysis.sanitized_cad_comments
        assert "555-1234" not in analysis.sanitized_cad_comments
        assert "M Rennick" in analysis.sanitized_cad_comments  # not PII
        assert "[patient]" in analysis.sanitized_cad_comments
        assert "[phone]" in analysis.sanitized_cad_comments

    def test_scrubs_sanitized_radio_notes(self):
        analysis = DispatchAnalysis(
            sanitized_radio_notes=[
                SanitizedNote(
                    timestamp="2026-02-12T14:38:00",
                    unit="E31",
                    text="pt contact 13yo female, alert",
                ),
                SanitizedNote(
                    timestamp="2026-02-12T14:42:00",
                    unit="M31",
                    text="transporting 72yo male to PeaceHealth",
                ),
            ]
        )
        _scrub_llm_output(analysis)
        assert "13yo" not in analysis.sanitized_radio_notes[0].text
        assert "72yo" not in analysis.sanitized_radio_notes[1].text
        # Timestamp + unit preserved
        assert analysis.sanitized_radio_notes[0].unit == "E31"
        assert analysis.sanitized_radio_notes[1].timestamp == "2026-02-12T14:42:00"

    def test_idempotent_on_clean_input(self):
        """Running scrub twice should produce the same result."""
        analysis = DispatchAnalysis(
            summary="the patient fell from standing",
            short_dsc="patient fall, transported",
            outcome="transported to PeaceHealth",
            actions_taken=["BLS assessment", "Transport"],
            key_events=["14:38 E31 — nothing showing"],
            sanitized_cad_comments="the patient injured, callback [phone]",
            sanitized_radio_notes=[
                SanitizedNote(
                    timestamp="2026-02-12T14:38:00",
                    unit="E31",
                    text="pt contact the patient",
                ),
            ],
        )
        # Snapshot before
        before = analysis.model_dump()

        _scrub_llm_output(analysis)
        _scrub_llm_output(analysis)  # second pass — must not change anything

        after = analysis.model_dump()
        assert before == after

    def test_does_not_touch_deterministic_fields(self):
        """Scrub must leave code-derived fields (unit_times, crew, etc.) alone."""
        analysis = DispatchAnalysis(
            incident_commander="BN31",
            incident_commander_name="Kyle Dodd",
            alarm_time="2026-02-12T14:30:00",
            first_enroute="2026-02-12T14:32:00",
            patient_count=1,
            escalated=True,
            unit_times=[
                UnitTiming(
                    unit="E31",
                    paged="2026-02-12T14:30:00",
                    enroute="2026-02-12T14:32:00",
                    arrived="2026-02-12T14:38:00",
                )
            ],
            on_duty_crew=[
                CrewOnDuty(name="Kyle Dodd", position="Battalion Chief", section="Chief Officer")
            ],
        )
        before = analysis.model_dump()

        _scrub_llm_output(analysis)

        after = analysis.model_dump()
        # These fields are untouched by the safety net
        for field in (
            "incident_commander",
            "incident_commander_name",
            "alarm_time",
            "first_enroute",
            "patient_count",
            "escalated",
            "unit_times",
            "on_duty_crew",
        ):
            assert before[field] == after[field], f"field {field} was modified"

    def test_empty_analysis_unchanged(self):
        """Scrubbing an empty analysis is a no-op."""
        analysis = DispatchAnalysis()
        _scrub_llm_output(analysis)

        assert analysis.summary == ""
        assert analysis.sanitized_cad_comments == ""
        assert analysis.sanitized_radio_notes == []
        assert analysis.key_events == []

    def test_handles_empty_text_in_sanitized_radio_notes(self):
        """Notes with empty text still pass through (scrub is a no-op on empty)."""
        analysis = DispatchAnalysis(
            sanitized_radio_notes=[
                SanitizedNote(timestamp="2026-02-12T14:38:00", unit="E31", text=""),
            ]
        )
        _scrub_llm_output(analysis)
        assert analysis.sanitized_radio_notes[0].text == ""
        # Structure preserved
        assert len(analysis.sanitized_radio_notes) == 1


# ---------------------------------------------------------------------------
# Instrumentation — structured logs feed issue #93 leak-rate measurement
# ---------------------------------------------------------------------------


class TestScrubLlmOutputInstrumentation:
    """Structured logging fires when regex catches LLM leaks.

    The safety net should emit a single structured WARNING log per
    enrichment with the dispatch_id and list of field names that
    changed.  NEVER the raw text — that's PII by definition.
    """

    def test_no_log_when_output_is_clean(self, caplog):
        import logging

        analysis = DispatchAnalysis(
            summary="the patient fell from standing",
            short_dsc="patient fall, transported",
            sanitized_cad_comments="the patient injured",
        )

        with caplog.at_level(logging.WARNING, logger="sjifire.ops.dispatch.enrich"):
            _scrub_llm_output(analysis, dispatch_id="26-001678")

        # No safety-net log should fire — output was already clean
        assert not any("pii_safety_net_triggered" in rec.message for rec in caplog.records)

    def test_logs_when_summary_leaks(self, caplog):
        import logging

        analysis = DispatchAnalysis(summary="13yo female fell")

        with caplog.at_level(logging.WARNING, logger="sjifire.ops.dispatch.enrich"):
            _scrub_llm_output(analysis, dispatch_id="26-001678")

        leak_logs = [r for r in caplog.records if "pii_safety_net_triggered" in r.message]
        assert len(leak_logs) == 1
        log = leak_logs[0]
        assert "26-001678" in log.message
        assert "summary" in log.message
        assert log.levelname == "WARNING"

    def test_logs_multiple_changed_fields(self, caplog):
        import logging

        analysis = DispatchAnalysis(
            summary="13yo female fell",
            short_dsc="13yo fall",
            sanitized_cad_comments="13yo female passenger",
            sanitized_radio_notes=[
                SanitizedNote(
                    timestamp="2026-02-12T14:38:00",
                    unit="E31",
                    text="pt contact 13yo female",
                )
            ],
        )

        with caplog.at_level(logging.WARNING, logger="sjifire.ops.dispatch.enrich"):
            _scrub_llm_output(analysis, dispatch_id="26-001678")

        leak_logs = [r for r in caplog.records if "pii_safety_net_triggered" in r.message]
        assert len(leak_logs) == 1, "should emit exactly one log per enrichment, not per field"
        msg = leak_logs[0].message
        # All four changed fields should be mentioned
        for field in ("summary", "short_dsc", "sanitized_cad_comments", "sanitized_radio_notes"):
            assert field in msg, f"expected {field} in log message: {msg}"

    def test_log_does_not_contain_raw_pii(self, caplog):
        """CRITICAL: the log message must never include the raw PII text."""
        import logging

        analysis = DispatchAnalysis(
            summary="13yo female patient named Jane Doe at 360-555-1234",
            sanitized_cad_comments="caller John Smith advises 72yo male injured",
        )

        with caplog.at_level(logging.WARNING, logger="sjifire.ops.dispatch.enrich"):
            _scrub_llm_output(analysis, dispatch_id="26-001678")

        # Every log record — message AND its positional args — must
        # exclude the raw PII tokens.  caplog.records holds the raw
        # LogRecord objects; rec.message is post-format, rec.args is
        # the tuple of values passed to the logger.
        parts: list[str] = []
        for rec in caplog.records:
            parts.append(rec.message)
            parts.append(rec.getMessage())
            if rec.args:
                parts.extend(str(a) for a in rec.args)
        all_log_text = " ".join(parts)

        assert "Jane Doe" not in all_log_text
        assert "John Smith" not in all_log_text
        assert "555-1234" not in all_log_text
        assert "13yo" not in all_log_text
        assert "72yo" not in all_log_text
        assert "female" not in all_log_text
        assert "male" not in all_log_text

    def test_logs_unknown_when_dispatch_id_missing(self, caplog):
        """Dispatch ID is optional; log should fall back gracefully."""
        import logging

        analysis = DispatchAnalysis(summary="13yo female fell")

        with caplog.at_level(logging.WARNING, logger="sjifire.ops.dispatch.enrich"):
            _scrub_llm_output(analysis)  # no dispatch_id

        leak_logs = [r for r in caplog.records if "pii_safety_net_triggered" in r.message]
        assert len(leak_logs) == 1
        assert "(unknown)" in leak_logs[0].message

    async def test_enrich_dispatch_passes_long_term_call_id_to_scrub(self, caplog):
        """Leak logs are correlatable to the originating dispatch call.

        End-to-end verification that ``enrich_dispatch`` passes
        ``doc.long_term_call_id`` into the safety net.
        """
        import logging

        doc = DispatchCallDocumentFactory.build(
            long_term_call_id="26-999001",
            responder_details=[],
            cad_comments="13yo female",
        )
        leaky = DispatchAnalysis(summary="13yo female fell")

        with (
            patch(
                "sjifire.ops.dispatch.analysis.analyze_dispatch",
                new_callable=AsyncMock,
                return_value=leaky,
            ),
            patch(
                "sjifire.ops.dispatch.enrich._get_on_duty_entries",
                new_callable=AsyncMock,
                return_value=[],
            ),
            caplog.at_level(logging.WARNING, logger="sjifire.ops.dispatch.enrich"),
        ):
            await enrich_dispatch(doc)

        leak_logs = [r for r in caplog.records if "pii_safety_net_triggered" in r.message]
        assert len(leak_logs) >= 1
        assert "26-999001" in leak_logs[0].message


# ---------------------------------------------------------------------------
# enrich_dispatch integration — safety net catches LLM slippage
# ---------------------------------------------------------------------------


class TestEnrichDispatchSafetyNet:
    """End-to-end: a leaky LLM response should be scrubbed by enrich_dispatch."""

    @pytest.fixture
    def doc(self) -> DispatchCallDocument:
        return DispatchCallDocumentFactory.build(
            nature="Medical Aid",
            address="200 Spring St",
            responder_details=[],
            cad_comments="13yo female passenger, possible head injury",
        )

    async def test_leaky_llm_output_is_scrubbed(self, doc):
        """Leaky LLM output should be scrubbed by the enrichment safety net.

        Simulates an LLM that ignored the 'no PII' instructions and
        echoed patient demographics into its sanitized_* output.
        """
        leaky_analysis = DispatchAnalysis(
            incident_commander="BN31",
            summary="13yo female fell and was transported",
            short_dsc="13yo fall, transported",
            outcome="transported 13yo female",
            actions_taken=["BLS on 13yo female"],
            key_events=["14:38 M31 — pt contact, 13yo female, alert"],
            # The LLM echoed raw CAD back verbatim (bad)
            sanitized_cad_comments="13yo female passenger, possible head injury",
            sanitized_radio_notes=[
                SanitizedNote(
                    timestamp="2026-02-12T14:38:00",
                    unit="E31",
                    text="pt contact 13yo female",
                )
            ],
        )

        with (
            patch(
                "sjifire.ops.dispatch.analysis.analyze_dispatch",
                new_callable=AsyncMock,
                return_value=leaky_analysis,
            ),
            patch(
                "sjifire.ops.dispatch.enrich._get_on_duty_entries",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = await enrich_dispatch(doc)

        # Every LLM-authored field must have the 13yo leak stripped
        assert "13yo" not in result.summary
        assert "13yo" not in result.short_dsc
        assert "13yo" not in result.outcome
        assert "13yo" not in result.actions_taken[0]
        assert "13yo" not in result.key_events[0]
        assert "13yo" not in result.sanitized_cad_comments
        assert "13yo" not in result.sanitized_radio_notes[0].text

        # Operational content still present
        assert "[patient]" in result.summary
        assert "transported" in result.outcome
        assert "BLS" in result.actions_taken[0]
        assert "M31" in result.key_events[0]

    async def test_clean_llm_output_unchanged(self, doc):
        """A well-behaved LLM response should pass through unchanged."""
        clean_analysis = DispatchAnalysis(
            incident_commander="BN31",
            summary="Patient fall at 200 Spring St, transported.",
            short_dsc="patient fall, transported",
            outcome="transported",
            actions_taken=["BLS assessment", "Transport to PeaceHealth"],
            key_events=["14:38 M31 — pt contact, alert"],
            sanitized_cad_comments="the patient injured, callback [phone]",
            sanitized_radio_notes=[
                SanitizedNote(
                    timestamp="2026-02-12T14:38:00",
                    unit="E31",
                    text="pt contact the patient",
                )
            ],
        )

        with (
            patch(
                "sjifire.ops.dispatch.analysis.analyze_dispatch",
                new_callable=AsyncMock,
                return_value=clean_analysis,
            ),
            patch(
                "sjifire.ops.dispatch.enrich._get_on_duty_entries",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = await enrich_dispatch(doc)

        assert result.summary == "Patient fall at 200 Spring St, transported."
        assert result.sanitized_cad_comments == "the patient injured, callback [phone]"
        assert result.sanitized_radio_notes[0].text == "pt contact the patient"
