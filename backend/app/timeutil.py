"""Dates in the shop's reporting time zone."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings


def today_local() -> date:
    """Today's calendar date in REPORT_TIMEZONE (not the server's time zone)."""
    return datetime.now(ZoneInfo(get_settings().report_timezone)).date()
