"""Earnings-calendar filter.

A short-timeframe cross that lands within a few days of an earnings release is
mostly noise: the print itself will move the stock far more than the stochastic
set-up. ``apply_event_filter`` turns such BUY/SELL signals into HOLD and says why.

The filter is a pure function over a list of earnings dates, so it works for
back-tests as well as for the live run. ``EarningsCalendar`` fetches the dates
with yfinance and caches them in ``state/events.json``; any failure simply means
the ticker is not filtered.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .signals import Action, Signal

DEFAULT_BLACKOUT_DAYS = 3
CACHE_MAX_AGE_DAYS = 7

EarningsDates = Mapping[str, Sequence[date]]


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    return ts.date()


def days_to_earnings(on: date | pd.Timestamp, dates: Iterable[date]) -> int | None:
    """Calendar days from ``on`` to the nearest earnings date on or after it.

    Returns ``None`` when no earnings date is on/after ``on`` (e.g. the list
    only holds past releases).
    """
    on_d = _as_date(on)
    if on_d is None:
        return None
    ahead = [(_as_date(d) - on_d).days for d in dates if _as_date(d) is not None and _as_date(d) >= on_d]
    return min(ahead) if ahead else None


def blackout_reason(days: int) -> str:
    return f"실적 발표 D-{days}, 보류"


def apply_event_filter(
    signals: Iterable[Signal],
    earnings: EarningsDates | None,
    blackout_days: int = DEFAULT_BLACKOUT_DAYS,
) -> list[Signal]:
    """Return copies of ``signals`` with BUY/SELL inside the earnings blackout set to HOLD.

    ``earnings`` maps ticker -> earnings dates (past and future). Tickers that
    are missing from the mapping are passed through unchanged. ``signals.py``
    stays untouched: this is post-processing on the evaluated ``Signal`` objects.
    """
    out: list[Signal] = []
    for sig in signals:
        if sig.action == Action.HOLD or not earnings or sig.ticker not in earnings:
            out.append(sig)
            continue
        n = days_to_earnings(sig.date, earnings[sig.ticker])
        if n is None or n > blackout_days:
            out.append(sig)
            continue
        out.append(
            dataclasses.replace(
                sig,
                action=Action.HOLD,
                reasons=[*sig.reasons, blackout_reason(n)],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Fetching + cache
# ---------------------------------------------------------------------------


def fetch_earnings_dates(ticker: str) -> list[date]:
    """Past and upcoming earnings dates from yfinance (``get_earnings_dates`` then ``calendar``)."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    found: set[date] = set()
    try:
        df = t.get_earnings_dates(limit=12)
    except Exception:
        df = None
    if df is not None and len(df):
        for idx in df.index:
            d = _as_date(idx)
            if d is not None:
                found.add(d)
    if not found:
        try:
            cal = t.calendar or {}
        except Exception:
            cal = {}
        raw = cal.get("Earnings Date") if isinstance(cal, dict) else None
        for v in raw if isinstance(raw, (list, tuple)) else [raw]:
            d = _as_date(v)
            if d is not None:
                found.add(d)
    return sorted(found)


class EarningsCalendar:
    """Ticker -> earnings dates, cached on disk so the daily run rarely hits Yahoo."""

    def __init__(
        self,
        path: Path | None,
        *,
        fetch: Callable[[str], list[date]] | None = None,
        max_age: timedelta = timedelta(days=CACHE_MAX_AGE_DAYS),
        now: datetime | None = None,
    ):
        self.path = path
        # Resolved at call time so tests can monkeypatch ``events.fetch_earnings_dates``.
        self.fetch = fetch if fetch is not None else (lambda t: fetch_earnings_dates(t))
        self.max_age = max_age
        self.now = now or datetime.now(timezone.utc)
        self.entries: dict[str, dict] = {}
        if path is not None and path.exists():
            try:
                self.entries = json.loads(path.read_text(encoding="utf-8")).get("tickers", {})
            except (ValueError, OSError) as exc:
                print(f"[warn] events cache unreadable, refetching: {exc}", file=sys.stderr)

    def _fresh(self, entry: dict) -> bool:
        try:
            fetched = datetime.fromisoformat(entry["fetched_at"])
        except (KeyError, ValueError):
            return False
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return self.now - fetched < self.max_age

    def dates_for(self, tickers: Iterable[str]) -> dict[str, list[date]]:
        """Earnings dates per ticker; tickers whose fetch failed and have no cache are omitted."""
        out: dict[str, list[date]] = {}
        for ticker in tickers:
            entry = self.entries.get(ticker)
            if entry is None or not self._fresh(entry):
                try:
                    dates = self.fetch(ticker)
                    entry = {
                        "fetched_at": self.now.isoformat(timespec="seconds"),
                        "dates": [d.isoformat() for d in dates],
                    }
                    self.entries[ticker] = entry
                except Exception as exc:  # network / parser: keep stale entry if any
                    print(f"[warn] earnings dates {ticker}: {exc}", file=sys.stderr)
                    if entry is None:
                        continue
            out[ticker] = [date.fromisoformat(s) for s in entry.get("dates", [])]
        return out

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": self.now.isoformat(timespec="seconds"),
            "tickers": dict(sorted(self.entries.items())),
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
