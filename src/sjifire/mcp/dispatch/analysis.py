"""Structured analysis of dispatch calls using LLM.

Extracts incident commander, summary, actions taken, patient count,
escalation status, and outcome from radio logs and CAD comments.

Provider selection (checked in order):
1. Azure OpenAI — set ``AZURE_OPENAI_ENDPOINT`` (+ ``AZURE_OPENAI_API_KEY``)
2. Anthropic — set ``ANTHROPIC_API_KEY`` (requires ``pip install anthropic``)
3. No provider configured → returns empty ``DispatchAnalysis``
"""

import logging
import os
from pathlib import Path

from sjifire.mcp.dispatch.models import DispatchAnalysis, DispatchCallDocument

logger = logging.getLogger(__name__)

# Load dispatch analysis instructions + reference from markdown file.
# Contains both the LLM system prompt and the dispatch cheat sheet.
# Try source tree first, then /app (Docker).
_SRC_DOCS = Path(__file__).resolve().parents[4] / "docs"
_APP_DOCS = Path("/app/docs")
_DOCS_DIR = _SRC_DOCS if _SRC_DOCS.is_dir() else _APP_DOCS
_CHEATSHEET_PATH = _DOCS_DIR / "dispatch-cheatsheet.md"
_SYSTEM_PROMPT = ""
if _CHEATSHEET_PATH.is_file():
    _SYSTEM_PROMPT = _CHEATSHEET_PATH.read_text().strip()


def _build_prompt(doc: DispatchCallDocument, crew_context: str = "") -> str:
    """Build the user prompt from dispatch document fields."""
    reported = doc.time_reported.strftime("%Y-%m-%d %H:%M:%S") if doc.time_reported else "N/A"
    lines = [
        f"Call: {doc.nature} at {doc.address}",
        f"Dispatch opened: {reported}",
        f"Agency: {doc.agency_code}",
        f"Units: {doc.responding_units}",
        "",
        "Radio log (chronological):",
    ]

    for entry in doc.responder_details:
        unit = entry.get("unit_number", "")
        status = entry.get("status", "")
        radio_log = entry.get("radio_log", "")
        time = entry.get("time_of_status_change", "")
        lines.append(f"  {unit} [{status}] {time} — {radio_log}")

    if doc.cad_comments:
        lines.append("")
        lines.append("CAD comments:")
        lines.append(doc.cad_comments)

    if crew_context:
        lines.append("")
        lines.append(crew_context)

    return "\n".join(lines)


async def _call_azure_openai(system: str, user_prompt: str) -> str:
    """Call Azure OpenAI with JSON mode."""
    from openai import AsyncAzureOpenAI

    client = AsyncAzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        api_version="2024-10-21",
    )
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    response = await client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return response.choices[0].message.content or ""


async def _call_anthropic(system: str, user_prompt: str) -> str:
    """Call Anthropic Claude with JSON output."""
    from anthropic import AsyncAnthropic  # requires: pip install anthropic

    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()


async def _call_llm(system: str, user_prompt: str) -> str:
    """Route to the configured LLM provider.

    Checks env vars to determine which provider to use.
    Returns raw text (expected to be JSON).
    """
    if os.getenv("AZURE_OPENAI_ENDPOINT"):
        return await _call_azure_openai(system, user_prompt)
    if os.getenv("ANTHROPIC_API_KEY"):
        return await _call_anthropic(system, user_prompt)

    logger.warning("No LLM provider configured — set AZURE_OPENAI_ENDPOINT or ANTHROPIC_API_KEY")
    return ""


def _clean_json(text: str) -> str:
    """Strip markdown code fences if the model wraps its JSON output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop first line (```json) and last line (```)
        lines = [line for line in lines[1:] if line.strip() != "```"]
        text = "\n".join(lines).strip()
    return text


async def analyze_dispatch(
    doc: DispatchCallDocument,
    crew_context: str = "",
) -> DispatchAnalysis:
    """Extract structured analysis from a dispatch call document.

    Uses the configured LLM provider to parse radio logs and CAD
    comments. Returns a ``DispatchAnalysis`` with all extracted fields.

    Fails gracefully: returns empty ``DispatchAnalysis`` if no provider
    is configured or the call fails.

    Args:
        doc: Dispatch call document with responder_details and cad_comments
        crew_context: Optional on-duty crew roster to include in prompt

    Returns:
        Populated DispatchAnalysis, or empty defaults on failure
    """
    if not doc.responder_details and not doc.cad_comments:
        return DispatchAnalysis()

    prompt = _build_prompt(doc, crew_context)
    text = await _call_llm(_SYSTEM_PROMPT, prompt)
    if not text:
        return DispatchAnalysis()

    clean = _clean_json(text)

    try:
        result = DispatchAnalysis.model_validate_json(clean)
    except Exception:
        logger.warning(
            "Failed to parse analysis JSON for %s: %s",
            doc.long_term_call_id,
            clean[:200],
            exc_info=True,
        )
        return DispatchAnalysis()

    if result.incident_commander:
        logger.info(
            "Dispatch analysis for %s: IC=%s, outcome=%s",
            doc.long_term_call_id,
            result.incident_commander,
            result.outcome,
        )
    else:
        logger.info(
            "Dispatch analysis for %s: no IC, outcome=%s", doc.long_term_call_id, result.outcome
        )

    return result
