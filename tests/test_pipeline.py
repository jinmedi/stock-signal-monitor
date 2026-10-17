"""Integration of events/news/tracking with cli, notifier and report (all network mocked)."""

import json
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from stock_signal_monitor import cli, report
from stock_signal_monitor.cli import apply_filters
from stock_signal_monitor.indicators import StochParams
from stock_signal_monitor.news import Headline, NewsAnalysis, Sentiment
from stock_signal_monitor.notifier import format_report, format_signal, format_stars
from stock_signal_monitor.report import build_ticker
from stock_signal_monitor.signals import Action, Cross, Signal, TimeframeState

PRESETS = {"short": StochParams(5, 3, 3), "mid": StochParams(10, 6, 6), "long": StochParams(20, 12, 12)}
NOW = datetime(2026, 10, 17, 9, tzinfo=timezone.utc)


def _frame(n=200, seed=0):
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 1.5, n))
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame({"High": closes + 1, "Low": closes - 1, "Close": closes}, index=idx)


def _sig(ticker="NVDA", action=Action.BUY, score=2, day="2026-10-16"):
    tf = TimeframeState("short", StochParams(5, 3, 3), 25.0, 20.0, 15.0, 18.0, Cross.GOLDEN)
    return Signal(ticker, pd.Timestamp(day), 182.5, action, score, ["단기 5-3-3 골든크로스"], {"short": tf})


def _news(nvda=1, tsla=-2, sector=0):
    heads = [Headline("Nvidia beats", "https://r/1", "Reuters", NOW, "NVDA"), Headline("Chips up", "https://r/2", "AP", NOW)]
    return NewsAnalysis(
        NOW, heads, Sentiment(sector, ("업종 중립",)),
        {"NVDA": Sentiment(nvda, ("실적 호조", "가이던스 상향")), "TSLA": Sentiment(tsla, ("리콜 악재",))}, "m",
    )


# ---------- notifier ----------


def test_format_stars_respects_max():
    assert format_stars(2, 4) == "★★☆☆" and format_stars(5, 5) == "★★★★★" and format_stars(7, 5) == "★★★★★"
    assert format_stars(0, 5) == "☆☆☆☆☆"


def test_format_signal_with_news_and_five_stars():
    text = format_signal(_sig(score=5), max_stars=5, news=Sentiment(1, ("실적 호조", "가이던스 상향")))
    assert "★★★★★" in text
    assert "뉴스: 감성 +1\n  실적 호조\n  가이던스 상향" in text


def test_format_report_adds_cautions_and_sector_footer():
    text = format_report([_sig()], usdkrw=1300.0, max_stars=5, news=_news(), caution_threshold=2)
    assert "🟢 NVDA  BUY  ★★☆☆☆" in text
    assert "뉴스: 감성 +1" in text
    assert "⚠ TSLA  주의  뉴스 감성 -2\n  리콜 악재" in text  # strong sentiment, no cross
    assert "업종 뉴스: 감성 +0" in text and "환율 1,300원/$" in text
    # a caution alone is enough to produce a report
    only_caution = format_report([], news=_news(), caution_threshold=2)
    assert "⚠ TSLA" in only_caution and "알릴 신호가 없습니다" not in only_caution
    assert "알릴 신호가 없습니다" in format_report([], news=_news(tsla=-1), caution_threshold=2)


# ---------- cli.apply_filters ----------


def test_apply_filters_order_event_then_news():
    earnings = {"NVDA": [date(2026, 10, 18)]}
    held = apply_filters([_sig()], earnings=earnings, blackout=3, news=_news(), weight=1, max_stars=5)[0]
    assert held.action == Action.HOLD and held.reasons[-1] == "실적 발표 D-2, 보류"
    assert held.score == 2  # HOLD gets no news nudge
    boosted = apply_filters([_sig()], earnings=earnings, blackout=0, news=_news(), weight=1, max_stars=5)[0]
    assert boosted.action == Action.BUY and boosted.score == 3 and "뉴스 감성 +1" in boosted.reasons[-1]
    plain = apply_filters([_sig()], earnings=None, blackout=3, news=_news(), weight=0, max_stars=5)[0]
    assert plain.score == 2 and len(plain.reasons) == 1


# ---------- report.build_ticker ----------


def test_build_ticker_applies_earnings_to_history_and_news_to_latest():
    df = _frame()
    base = build_ticker("NVDA", df, PRESETS, bars=120, signal_lookback=60)
    assert base["news"] is None and base["latest"]["cross"] in {"golden", "dead", "none"}
    first = base["signals"][0]
    earnings = [date.fromisoformat(first["date"])]  # print on the day of the first historical cross
    filtered = build_ticker(
        "NVDA", df, PRESETS, bars=120, signal_lookback=60, earnings_dates=earnings, blackout=3, news=_news(), max_stars=5
    )
    held = filtered["signals"][0]
    assert held["date"] == first["date"] and held["action"] == "HOLD" and held["cross"] == first["action"]
    assert held["reasons"][-1] == "실적 발표 D-0, 보류"
    assert filtered["news"]["score"] == 1 and filtered["news"]["summary"] == ["실적 호조", "가이던스 상향"]
    assert [h["title"] for h in filtered["news"]["headlines"]] == ["Nvidia beats", "Chips up"]
    json.dumps(filtered)
    # ticker without own sentiment still gets sector headlines
    gld = build_ticker("GLD", df, PRESETS, bars=50, signal_lookback=10, news=_news())
    assert gld["news"]["score"] is None and gld["news"]["headlines"][0]["title"] == "Chips up"


# ---------- end to end (report + cli) with everything mocked ----------


def _mock_network(monkeypatch, tmp_path, frames):
    monkeypatch.setattr(report, "fetch_history", lambda t, period: frames[t])
    monkeypatch.setattr(cli, "fetch_history", lambda t, period: frames[t])
    rates = pd.Series(1300.0, index=next(iter(frames.values())).index)
    monkeypatch.setattr(report, "fetch_usdkrw", lambda period: rates)
    monkeypatch.setattr(cli, "fetch_usdkrw", lambda period: rates)
    monkeypatch.setattr(report, "get_news", lambda cfg, cache_path: _news())
    monkeypatch.setattr(cli, "get_news", lambda cfg, cache_path: _news())
    import stock_signal_monitor.events as ev

    monkeypatch.setattr(ev, "fetch_earnings_dates", lambda t: [date(2026, 11, 19)])


def _cfg():
    return {
        "tickers": ["NVDA", "TSLA"],
        "stochastic": {"short": {"n": 5, "m": 3, "t": 3}, "mid": {"n": 10, "m": 6, "t": 6}, "long": {"n": 20, "m": 12, "t": 12}},
        "events": {"earnings_blackout_days": 3},
        "news": {"weight": 1, "max_stars": 5},
    }


def test_build_report_end_to_end(monkeypatch, tmp_path):
    frames = {"NVDA": _frame(seed=1), "TSLA": _frame(seed=2)}
    _mock_network(monkeypatch, tmp_path, frames)

    out = report.build_report(_cfg(), bars=60, signal_lookback=30, state_dir=tmp_path)
    json.dumps(out)
    assert out["max_stars"] == 5 and out["events"]["blackout_days"] == 3
    assert out["events"]["earnings"] == {"NVDA": ["2026-11-19"], "TSLA": ["2026-11-19"]}
    assert out["news"]["scored"] and out["news"]["sector"]["score"] == 0 and out["news"]["headline_count"] == 2
    assert out["tracking"]["ready"] is False and out["tracking"]["min_settled"] == 20
    assert {t["ticker"] for t in out["tickers"]} == {"NVDA", "TSLA"}
    assert (tmp_path / "events.json").exists()
    track = tmp_path / "track.csv"
    assert track.exists()
    rows = track.read_text(encoding="utf-8").splitlines()
    crossed = [t for t in out["tickers"] if t["latest"]["cross"] != "none"]
    assert len(rows) == 1 + len(crossed)  # header + one row per ticker that crossed today


def test_cli_main_end_to_end(monkeypatch, tmp_path, capsys):
    frames = {"NVDA": _frame(seed=1), "TSLA": _frame(seed=2)}
    _mock_network(monkeypatch, tmp_path, frames)
    sent: list[str] = []

    class FakeTelegram:
        def __init__(self, *a, **k):
            pass

        def send(self, text):
            sent.append(text)

    monkeypatch.setattr(cli, "TelegramNotifier", FakeTelegram)
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'tickers = ["NVDA", "TSLA"]\n[stochastic.short]\nn=5\nm=3\nt=3\n[stochastic.mid]\nn=10\nm=6\nt=6\n'
        "[stochastic.long]\nn=20\nm=12\nt=12\n[events]\nearnings_blackout_days = 3\n[news]\nweight = 1\nmax_stars = 5\n",
        encoding="utf-8",
    )
    argv = [
        "--config", str(cfg_path), "--state", str(tmp_path / "s.json"), "--events-cache", str(tmp_path / "e.json"),
        "--news-cache", str(tmp_path / "n.json"), "--track", str(tmp_path / "t.csv"), "--telegram", "--all",
    ]
    assert cli.main(argv) == 0
    assert len(sent) == 1
    text = sent[0]
    # TSLA sentiment is -2 → either boosts a SELL / weakens a BUY, or appears as a caution
    assert "TSLA" in text and ("뉴스 감성 -2" in text or "⚠ TSLA" in text)
    assert "업종 뉴스: 감성 +0" in text and "환율 1,300원/$" in text
    assert (tmp_path / "e.json").exists() and (tmp_path / "t.csv").exists() and (tmp_path / "s.json").exists()
    # second run: same bars already alerted → nothing new, quiet flag suppresses the send
    assert cli.main(argv + ["--quiet-when-empty"]) == 0
    assert len(sent) == 2  # TSLA caution (-2) still goes out even without a new cross
    assert "⚠ TSLA" in sent[1]
