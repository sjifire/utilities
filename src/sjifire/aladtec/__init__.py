"""Aladtec integration module."""

from sjifire.aladtec.client import AladtecClient, get_aladtec_credentials
from sjifire.aladtec.models import Member
from sjifire.aladtec.schedule import AladtecScheduleScraper
from sjifire.aladtec.scraper import AladtecMemberScraper

__all__ = [
    "AladtecClient",
    "AladtecMemberScraper",
    "AladtecScheduleScraper",
    "Member",
    "get_aladtec_credentials",
]
