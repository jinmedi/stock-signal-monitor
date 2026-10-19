"""On-demand refresh: forced news re-classification and the intraday-bar flag."""

from datetime import datetime, timedelta, timezone

from stock_signal_monitor.market import last_bar_intraday, market_status
from stock_signal_monitor.news import Headline, NewsAnalysis, Sentiment, get_news, save_cache

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def _cfg():
    return {"tickers": ["NVDA"], "news": {"enabled": True, "cache_hours": 6, "model": "test"}}


def _headlines(_query):
    return [Headline(title="x", url="u", source="s", published=NOW - timedelta(hours=1), ticker="NVDA")]


class _Classifier:
    """Callable (prompt, tickers) -> raw JSON dict, counting calls."""

    calls = 0
    model = "test"

    def __call__(self, prompt, tickers):
        _Classifier.calls += 1
        return {
            "sector": {"score": 1, "summary": ["a", "b"]},
            "tickers": [{"ticker": t, "score": 1, "summary": ["c"]} for t in tickers],
        }


def test_get_news_uses_cache_unless_forced(tmp_path):
    cache = tmp_path / "news.json"
    save_cache(cache, NewsAnalysis(NOW - timedelta(minutes=5), [], Sentiment(0), {"NVDA": Sentiment(0)}, "test"))
    kw = dict(cache_path=cache, now=NOW, classifier=_Classifier(), ticker_fetch=_headlines, query_fetch=_headlines)

    _Classifier.calls = 0
    cached = get_news(_cfg(), **kw)
    assert _Classifier.calls == 0 and cached.fetched_at == NOW - timedelta(minutes=5)

    fresh = get_news(_cfg(), force=True, **kw)
    assert _Classifier.calls == 1 and fresh.fetched_at == NOW and fresh.scored
    # the forced result replaces the cache for the next (unforced) run
    assert get_news(_cfg(), **kw).fetched_at == NOW and _Classifier.calls == 1


def test_market_status_and_intraday_flag():
    # 2026-10-19 is a Monday. 14:00 UTC = 10:00 ET → session open.
    st = market_status(datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc))
    assert st["open"] and st["ny_date"] == "2026-10-19"
    assert last_bar_intraday(st, {"2026-10-19"})
    assert not last_bar_intraday(st, {"2026-10-16"})  # provider still on Friday's bar

    closed = market_status(datetime(2026, 10, 19, 22, 0, tzinfo=timezone.utc))  # 18:00 ET
    assert not closed["open"] and not last_bar_intraday(closed, {"2026-10-19"})
    assert not market_status(datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc))["open"]  # Sunday


def test_build_report_skips_tracking_while_bar_is_forming(monkeypatch, tmp_path):
    """An on-demand refresh during the US session must not record half-day closes."""
    import numpy as np
    import pandas as pd

    from stock_signal_monitor import report, tracking

    n = 80
    idx = pd.bdate_range(end="2026-10-19", periods=n)
    closes = 100 + np.cumsum(np.random.default_rng(1).normal(0, 1, n))
    df = pd.DataFrame({"High": closes + 1, "Low": closes - 1, "Close": closes}, index=idx)
    monkeypatch.setattr(report, "fetch_history", lambda t, p: df)
    monkeypatch.setattr(report, "fetch_usdkrw", lambda p: None)
    monkeypatch.setattr(report, "get_news", lambda cfg, cache_path, **kw: None)
    monkeypatch.setattr(report, "market_status", lambda: {"ny_time": "2026-10-19 10:46", "ny_date": "2026-10-19", "open": True})
    calls = []
    monkeypatch.setattr(tracking, "update", lambda *a, **k: calls.append(1) or [])

    cfg = {
        "tickers": ["NVDA"],
        "stochastic": {"short": {"n": 5, "m": 3, "t": 3}},
        "events": {"earnings_blackout_days": 0},
        "news": {"enabled": False},
    }
    out = report.build_report(cfg, state_dir=tmp_path)
    assert out["market"]["last_bar_intraday"] is True
    assert calls == []  # nothing recorded while the session is open

    monkeypatch.setattr(report, "market_status", lambda: {"ny_time": "2026-10-19 18:00", "ny_date": "2026-10-19", "open": False})
    out = report.build_report(cfg, state_dir=tmp_path)
    assert out["market"]["last_bar_intraday"] is False and calls == [1]
