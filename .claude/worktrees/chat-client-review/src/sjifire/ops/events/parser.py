"""Parse attendee names from uploaded event logs using Claude.

Supports images (sign-in sheets), text (chat screenshots), and PDFs.
Matches parsed names against the personnel roster.
"""

import json
import logging
import re

from sjifire.core.anthropic import MODEL, get_client

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """Extract all attendee/participant names from this content.
This is an attendance record from a fire department.

Return a JSON array of objects, each with a "name" field containing the full name.
Strip any rank prefixes (Captain, Lieutenant, Chief, FF, etc.) from the names.
If you're uncertain about a name, include it anyway but add "uncertain": true.

Return ONLY the JSON array, no other text. Example:
[{"name": "John Smith"}, {"name": "Jane Doe", "uncertain": true}]"""


async def _call_claude_vision(image_bytes: bytes, content_type: str) -> str:
    """Send image to Claude for text extraction."""
    import base64

    client = get_client()
    b64 = base64.b64encode(image_bytes).decode()

    # Map content types to Claude media types
    media_type = content_type
    if media_type == "image/tiff":
        media_type = "image/png"  # Claude doesn't support TIFF directly

    response = await client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {"type": "text", "text": _EXTRACTION_PROMPT},
                ],
            }
        ],
    )
    return response.content[0].text


async def _call_claude_text(text: str) -> str:
    """Send text to Claude for name extraction."""
    client = get_client()
    response = await client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": f"{_EXTRACTION_PROMPT}\n\nContent:\n{text}",
            }
        ],
    )
    return response.content[0].text


async def _call_claude_pdf(pdf_bytes: bytes) -> str:
    """Send PDF to Claude for name extraction."""
    import base64

    client = get_client()
    b64 = base64.b64encode(pdf_bytes).decode()

    response = await client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _EXTRACTION_PROMPT},
                ],
            }
        ],
    )
    return response.content[0].text


def _parse_json_response(raw: str) -> list[dict]:
    """Parse JSON from Claude's response, stripping markdown fences."""
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        logger.warning("Failed to parse Claude response as JSON: %s", text[:200])
    return []


def _normalize_name(name: str) -> str:
    """Normalize a name for matching — lowercase, strip rank prefixes."""
    n = name.strip().lower()
    # Strip common rank prefixes
    for prefix in (
        "battalion chief",
        "assistant chief",
        "fire chief",
        "chief",
        "captain",
        "capt",
        "lieutenant",
        "lt",
        "firefighter",
        "ff",
        "emt",
    ):
        if n.startswith(prefix + " "):
            n = n[len(prefix) :].strip()
            break
    return n


async def _match_against_roster(
    parsed_names: list[dict],
) -> list[dict]:
    """Match parsed names against the personnel roster.

    Returns enriched list with email and match confidence.
    """
    from sjifire.ops.personnel.tools import get_personnel

    try:
        roster = await get_personnel()
    except Exception:
        logger.warning("Could not fetch personnel roster for matching")
        return [
            {
                "name": n.get("name", ""),
                "email": None,
                "source": "parsed",
                "uncertain": n.get("uncertain", False),
            }
            for n in parsed_names
        ]

    # Build lookup indexes
    full_name_map: dict[str, dict] = {}  # normalized full name → person
    last_name_map: dict[str, list[dict]] = {}  # normalized last name → [persons]
    for person in roster:
        norm = _normalize_name(person["name"])
        full_name_map[norm] = person
        parts = norm.split()
        if parts:
            last = parts[-1]
            last_name_map.setdefault(last, []).append(person)

    results = []
    for entry in parsed_names:
        raw_name = entry.get("name", "").strip()
        if not raw_name:
            continue

        norm = _normalize_name(raw_name)
        matched_person = None

        # Try full name match
        if norm in full_name_map:
            matched_person = full_name_map[norm]
        else:
            # Try last name fallback
            parts = norm.split()
            if parts:
                last = parts[-1]
                candidates = last_name_map.get(last, [])
                if len(candidates) == 1:
                    matched_person = candidates[0]

        results.append(
            {
                "name": matched_person["name"] if matched_person else raw_name,
                "email": matched_person["email"] if matched_person else None,
                "source": "parsed",
                "uncertain": entry.get("uncertain", False),
            }
        )

    return results


async def parse_attendees_from_image(image_bytes: bytes, content_type: str) -> list[dict]:
    """Parse attendee names from an image (sign-in sheet photo)."""
    raw = await _call_claude_vision(image_bytes, content_type)
    parsed = _parse_json_response(raw)
    return await _match_against_roster(parsed)


async def parse_attendees_from_text(text: str) -> list[dict]:
    """Parse attendee names from plain text."""
    raw = await _call_claude_text(text)
    parsed = _parse_json_response(raw)
    return await _match_against_roster(parsed)


async def parse_attendees_from_pdf(pdf_bytes: bytes) -> list[dict]:
    """Parse attendee names from a PDF document."""
    raw = await _call_claude_pdf(pdf_bytes)
    parsed = _parse_json_response(raw)
    return await _match_against_roster(parsed)
