"""US regular-session helper, so an on-demand refresh can flag an unfinished daily bar."""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


def market_status(now: datetime | None = None) -> dict:
    """``{"ny_time", "ny_date", "open"}`` for the NYSE regular session (holidays are not modelled)."""
    now = now or datetime.now(timezone.utc)
    ny = now.astimezone(NY)
    is_open = ny.weekday() < 5 and SESSION_OPEN <= ny.time() < SESSION_CLOSE
    return {"ny_time": ny.strftime("%Y-%m-%d %H:%M"), "ny_date": ny.strftime("%Y-%m-%d"), "open": is_open}


def last_bar_intraday(status: dict, last_bar_dates: set[str]) -> bool:
    """True when the session is open and the newest bar we fetched is today's (still forming)."""
    return bool(status["open"] and status["ny_date"] in last_bar_dates)
