"""Email dispatch module for processing county dispatch emails."""

from sjifire.dispatch.cleanup import cleanup_old_emails
from sjifire.dispatch.processor import process_email

__all__ = ["cleanup_old_emails", "process_email"]
