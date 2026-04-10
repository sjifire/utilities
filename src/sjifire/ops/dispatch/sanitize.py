"""LLM-facing sanitization helpers for dispatch call data.

Prefers ``DispatchAnalysis.sanitized_cad_comments`` and
``sanitized_radio_notes`` (produced by the enrichment LLM) and falls
back to regex-based redaction from ``sjifire.core.pii`` for records
that haven't been enriched yet.

**Policy:** raw dispatch data (``cad_comments``, ``responder_details``)
is kept untouched in Cosmos DB and surfaced to human-facing UIs
(dashboard, kiosk, chat sidebar).  These helpers produce the version
shown to Claude — via MCP tools, the chat engine system prompt, and
incident documents (which ultimately flow to NERIS).
"""

import html

from sjifire.core.pii import redact_pii
from sjifire.ops.dispatch.models import DispatchCallDocument


def sanitize_cad_comments(doc: DispatchCallDocument) -> str:
    """Return the LLM-facing CAD comments for a dispatch call.

    Prefers ``doc.analysis.sanitized_cad_comments`` (written by the
    enrichment LLM per the instructions in ``docs/dispatch-cheatsheet.md``).
    Falls back to regex-based redaction of the raw ``cad_comments``
    blob for un-enriched records.

    Always HTML-decodes the source (iSpyFire encodes apostrophes, etc.)
    before returning.
    """
    if doc.analysis and doc.analysis.sanitized_cad_comments:
        return html.unescape(doc.analysis.sanitized_cad_comments)
    if not doc.cad_comments:
        return ""
    return redact_pii(html.unescape(doc.cad_comments))


def sanitize_radio_notes(doc: DispatchCallDocument) -> list[dict]:
    """Return LLM-facing NOTE entries from a dispatch call's radio log.

    Each dict has ``timestamp``, ``unit``, and ``text`` keys — matching
    the shape that ``_extract_dispatch_notes`` produces from raw
    ``responder_details``.

    Prefers ``doc.analysis.sanitized_radio_notes`` when populated.
    Falls back to regex-redacting the raw NOTE entries from
    ``responder_details`` for un-enriched records.
    """
    if doc.analysis and doc.analysis.sanitized_radio_notes:
        return [
            {
                "timestamp": n.timestamp,
                "unit": n.unit,
                "text": html.unescape(n.text),
            }
            for n in doc.analysis.sanitized_radio_notes
            if n.text
        ]

    notes: list[dict] = []
    for entry in doc.responder_details or []:
        if entry.get("status") != "NOTE":
            continue
        text = html.unescape(entry.get("radio_log", "") or "").strip()
        if not text:
            continue
        notes.append(
            {
                "timestamp": str(entry.get("time_of_status_change", "")),
                "unit": entry.get("unit_number", ""),
                "text": redact_pii(text),
            }
        )
    return notes


def sanitize_dispatch_for_llm(doc: DispatchCallDocument) -> dict:
    """Return a dispatch call dict suitable for LLM consumption.

    Starts from ``doc.to_dict()`` and replaces PII-bearing fields with
    their sanitized equivalents:

    - ``cad_comments`` → from ``sanitize_cad_comments(doc)``
    - ``responder_details[].radio_log`` for NOTE entries → matched from
      ``doc.analysis.sanitized_radio_notes`` by ``(timestamp, unit)``,
      falling back to regex redaction for unmatched entries
    - ``analysis.summary`` / ``short_dsc`` / ``key_events`` → regex-
      redacted as a safety net (the enrichment prompt already tells the
      LLM to keep these PII-free)

    Does not mutate ``doc``. Returns a fresh dict safe for the caller
    to further modify.

    Use this in MCP dispatch tools (``get_dispatch_call``, etc.) and
    anywhere else Claude receives a full dispatch dict.
    """
    d = doc.to_dict()

    # cad_comments: prefer LLM sanitized version
    if doc.cad_comments or (doc.analysis and doc.analysis.sanitized_cad_comments):
        d["cad_comments"] = sanitize_cad_comments(doc)

    # responder_details: sanitize NOTE radio_log entries
    sanitized_lookup: dict[tuple[str, str], str] = {}
    if doc.analysis and doc.analysis.sanitized_radio_notes:
        sanitized_lookup = {
            (n.timestamp, n.unit): n.text for n in doc.analysis.sanitized_radio_notes if n.text
        }

    for entry in d.get("responder_details", []):
        if entry.get("status") != "NOTE":
            continue
        raw_text = entry.get("radio_log") or ""
        if not raw_text:
            continue
        key = (
            str(entry.get("time_of_status_change", "")),
            entry.get("unit_number", ""),
        )
        if key in sanitized_lookup:
            entry["radio_log"] = sanitized_lookup[key]
        else:
            entry["radio_log"] = redact_pii(raw_text)

    # analysis: safety-net regex pass over text fields
    analysis = d.get("analysis")
    if isinstance(analysis, dict):
        if analysis.get("summary"):
            analysis["summary"] = redact_pii(analysis["summary"])
        if analysis.get("short_dsc"):
            analysis["short_dsc"] = redact_pii(analysis["short_dsc"])
        analysis["key_events"] = [redact_pii(e) for e in analysis.get("key_events", [])]
        # Raw sanitized fields are already clean by construction; no need to
        # re-process them. They're kept in the dict so callers that want
        # them explicitly can still access them.

    return d
