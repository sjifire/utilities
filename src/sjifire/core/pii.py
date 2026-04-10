"""PII redaction for dispatch logs and incident data.

Strips personally identifiable information (patient demographics, phone
numbers) from dispatch text while preserving operational content.  The raw
dispatch data in Cosmos DB is kept intact; redaction is applied when data
surfaces to reports, narratives, or the AI chat context.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Sanity cap on the input size redact_pii will process.  Dispatch CAD
# comments are typically <10KB; the IncidentDocument schema caps
# dispatch_comments at 100KB.  Anything above this is either malformed
# upstream data or a crafted adversarial input — log a warning so we
# notice, but still process (the regexes are linear-time in practice).
_MAX_INPUT_BYTES = 200_000

# Hyphen-like separators: regular hyphen + en-dash (U+2013)
_DASH = "-\u2013"

# Age + gender: "13yo female", "72-year-old male", "9 yr old boy", "55 y/o m"
_AGE_GENDER_RE = re.compile(
    rf"\b\d{{1,3}}\s*[{_DASH}]?\s*"
    rf"(?:y/?\.?o\.?|year[{_DASH}\s]*old|yr[{_DASH}\s]*old)"
    r"\s+(?:male|female|man|woman|boy|girl|m\b|f\b)",
    re.IGNORECASE,
)

# Standalone age descriptor: "13yo", "72 year old" (no gender follows)
_AGE_RE = re.compile(
    rf"\b\d{{1,3}}\s*[{_DASH}]?\s*(?:y/?\.?o\.?|year[{_DASH}\s]*old|yr[{_DASH}\s]*old)\b",
    re.IGNORECASE,
)

# Phone numbers: (360) 555-1234, 360-555-1234, 360.555.1234
_PHONE_RE = re.compile(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}")

# Caller/reporting party names: "caller: John Smith", "RP Jane Doe"
_CALLER_RE = re.compile(
    rf"(?:caller|rp|reporting\s+party)\s*[:{_DASH}]?\s*[A-Z][a-z]+\s+[A-Z][a-z]+",
    re.IGNORECASE,
)


def redact_pii(text: str) -> str:
    """Remove patient demographics and phone numbers from text.

    Replaces age+gender descriptors (e.g. "13yo female") and standalone
    age descriptors (e.g. "72yo") with ``[patient]``, phone numbers with
    ``[phone]``, and caller/RP names with ``[caller]``.

    Operational content (addresses, unit codes, actions, conditions) is
    preserved.

    Args:
        text: Raw dispatch text (CAD comments, radio log notes, etc.)

    Returns:
        Text with PII patterns replaced by bracketed placeholders.
    """
    if not text:
        return text

    if len(text) > _MAX_INPUT_BYTES:
        # Canary: something upstream produced an unexpectedly large blob.
        # The regexes are linear-time in practice so we still process it,
        # but we want to notice if it becomes common.
        logger.warning(
            "redact_pii received oversized input: %d chars (cap: %d)",
            len(text),
            _MAX_INPUT_BYTES,
        )

    # Order matters: age+gender first (more specific), then standalone age
    text = _AGE_GENDER_RE.sub("[patient]", text)
    text = _AGE_RE.sub("[patient]", text)
    text = _PHONE_RE.sub("[phone]", text)
    text = _CALLER_RE.sub("[caller]", text)
    return text
