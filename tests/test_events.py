from datetime import date, datetime, timedelta, timezone

import pandas as pd

from stock_signal_monitor.events import EarningsCalendar, apply_event_filter, days_to_earnings
from stock_signal_monitor.signals import Action, Signal


def _sig(ticker="NVDA", day="2026-11-16", action=Action.BUY, score=2):
    return Signal(ticker, pd.Timestamp(day), 180.0, action, score, ["단기 골든크로스"], {})


def test_days_to_earnings_picks_next_release():
    dates = [date(2026, 8, 27), date(2026, 11, 19), date(2027, 2, 25)]
    assert days_to_earnings(date(2026, 11, 16), dates) == 3
    assert days_to_earnings(pd.Timestamp("2026-11-19"), dates) == 0
    assert days_to_earnings(date(2026, 11, 20), dates) == 97
    assert days_to_earnings(date(2027, 3, 1), dates) is None
    assert days_to_earnings(date(2026, 11, 16), []) is None


def test_apply_event_filter_holds_inside_blackout():
    earnings = {"NVDA": [date(2026, 11, 19)]}
    buy = _sig()
    out = apply_event_filter([buy], earnings, blackout_days=3)
    assert out[0].action == Action.HOLD
    assert out[0].reasons[-1] == "실적 발표 D-3, 보류"
    assert out[0].score == 2  # score is kept so the dashboard can show the strength
    assert buy.action == Action.BUY  # original untouched


def test_apply_event_filter_passes_outside_blackout_and_unknown_tickers():
    earnings = {"NVDA": [date(2026, 11, 19)]}
    far = _sig(day="2026-11-10")
    other = _sig(ticker="TSLA")
    hold = _sig(action=Action.HOLD, score=0)
    out = apply_event_filter([far, other, hold], earnings, blackout_days=3)
    assert [s.action for s in out] == [Action.BUY, Action.BUY, Action.HOLD]
    assert out[2].reasons == ["단기 골든크로스"]
    # no calendar at all → nothing changes
    assert apply_event_filter([_sig()], None)[0].action == Action.BUY
    assert apply_event_filter([_sig()], {})[0].action == Action.BUY


def test_apply_event_filter_backtest_with_past_dates():
    # a SELL two days before the past August print is held, a later one passes
    earnings = {"SOXL": [date(2026, 8, 27), date(2026, 11, 19)]}
    sells = [_sig("SOXL", "2026-08-25", Action.SELL), _sig("SOXL", "2026-09-10", Action.SELL)]
    out = apply_event_filter(sells, earnings, 3)
    assert out[0].action == Action.HOLD and "D-2" in out[0].reasons[-1]
    assert out[1].action == Action.SELL


def test_calendar_caches_and_survives_fetch_failure(tmp_path):
    path = tmp_path / "events.json"
    calls: list[str] = []

    def fake_fetch(ticker):
        calls.append(ticker)
        if ticker == "BAD":
            raise RuntimeError("yahoo down")
        return [date(2026, 11, 19)]

    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    cal = EarningsCalendar(path, fetch=fake_fetch, now=now)
    got = cal.dates_for(["NVDA", "BAD"])
    assert got == {"NVDA": [date(2026, 11, 19)]}  # BAD skipped, not raised
    cal.save()
    assert path.exists()

    # fresh cache → no refetch
    cal2 = EarningsCalendar(path, fetch=fake_fetch, now=now + timedelta(days=1))
    assert cal2.dates_for(["NVDA"]) == {"NVDA": [date(2026, 11, 19)]}
    assert calls == ["NVDA", "BAD"]

    # stale cache → refetch; if that fails, keep the stale dates
    def failing(_):
        raise RuntimeError("down again")

    cal3 = EarningsCalendar(path, fetch=failing, now=now + timedelta(days=30))
    assert cal3.dates_for(["NVDA"]) == {"NVDA": [date(2026, 11, 19)]}


def test_calendar_without_path_works_in_memory():
    cal = EarningsCalendar(None, fetch=lambda t: [date(2026, 1, 1)])
    assert cal.dates_for(["X"]) == {"X": [date(2026, 1, 1)]}
    cal.save()  # no-op
