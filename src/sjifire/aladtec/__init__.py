"""Aladtec integration module."""

from sjifire.aladtec.client import AladtecClient, get_aladtec_credentials
from sjifire.aladtec.member_scraper import AladtecMemberScraper
from sjifire.aladtec.models import Member
from sjifire.aladtec.schedule_scraper import (
    AladtecScheduleScraper,
    load_schedules,
    save_schedules,
)

__all__ = [
    "AladtecClient",
    "AladtecMemberScraper",
    "AladtecScheduleScraper",
    "Member",
    "get_aladtec_credentials",
    "load_schedules",
    "save_schedules",
]
