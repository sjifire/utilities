"""Normalization and validation utilities for common data types.

This module provides a single source of truth for normalizing and validating:
- Phone numbers (using Google's libphonenumber)
- Email addresses (using email-validator)
- Names (for comparison and display)

All normalization should be done at the point of data ingestion (e.g., when
reading from Aladtec) so that downstream code can rely on consistent formats.
"""

import logging
import re

logger = logging.getLogger(__name__)


def format_phone(phone: str | None) -> str | None:
    """Format phone number to standard US format using libphonenumber.

    Use this when storing/displaying phone numbers.

    Args:
        phone: Raw phone number string

    Returns:
        Formatted phone number in national format (XXX) XXX-XXXX, or None if empty
    """
    if not phone:
        return None

    import phonenumbers

    try:
        # Parse as US number (default region)
        parsed = phonenumbers.parse(phone, "US")

        # Validate it's a possible number
        if not phonenumbers.is_possible_number(parsed):
            return phone.strip()

        # Format in national format: (XXX) XXX-XXXX
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
    except phonenumbers.NumberParseException:
        # Return original stripped if parsing fails
        return phone.strip() if phone else None


def normalize_phone(phone: str | None) -> str | None:
    """Normalize phone number to digits only for comparison.

    Use this when comparing phone numbers that may be in different formats.

    Args:
        phone: Phone number string in any format

    Returns:
        Digits only (e.g., "5551234567") or None if empty
    """
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    return digits if digits else None


def validate_email(email: str | None, context: str = "") -> str | None:
    """Validate and normalize email address.

    Use this when ingesting email addresses to ensure validity.

    Args:
        email: Email address to validate
        context: Context for logging (e.g., member name)

    Returns:
        Normalized valid email or None if invalid
    """
    if not email:
        return None

    # Strip whitespace before validation
    email = email.strip()
    if not email:
        return None

    from email_validator import EmailNotValidError
    from email_validator import validate_email as ev

    try:
        # Validate and normalize the email
        result = ev(email, check_deliverability=False)
        return result.normalized
    except EmailNotValidError as e:
        logger.warning(f"Invalid email '{email}'{f' for {context}' if context else ''}: {e}")
        return None


def normalize_email(email: str | None) -> str | None:
    """Normalize email for comparison (lowercase, stripped).

    Use this when comparing emails that are already validated.

    Args:
        email: Email address

    Returns:
        Lowercase stripped email or None
    """
    if not email:
        return None
    return email.lower().strip()


def normalize_name(first: str | None, last: str | None) -> str:
    """Normalize a full name for comparison.

    Combines first and last name, lowercased and stripped.

    Args:
        first: First name
        last: Last name

    Returns:
        Normalized "first last" string, lowercase and stripped
    """
    first_clean = (first or "").lower().strip()
    last_clean = (last or "").lower().strip()
    return f"{first_clean} {last_clean}"


def normalize_name_part(name: str | None) -> str:
    """Normalize a single name part for UPN generation or comparison.

    Removes spaces, apostrophes, and lowercases.

    Args:
        name: Name string (first or last)

    Returns:
        Normalized name suitable for UPN generation
    """
    if not name:
        return ""
    return name.lower().replace(" ", "").replace("'", "")


def clean_name_for_display(name: str | None) -> str:
    """Clean a name for display purposes.

    Strips whitespace and normalizes internal spacing.

    Args:
        name: Name string

    Returns:
        Cleaned name or empty string
    """
    if not name:
        return ""
    # Normalize multiple spaces to single space
    return re.sub(r"\s+", " ", name.strip())
