import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from stock_signal_monitor import news
from stock_signal_monitor.news import (
    Headline,
    NewsAnalysis,
    Sentiment,
    analyze,
    apply_news_weight,
    build_prompt,
    cautions,
    collect_headlines,
    get_news,
    google_news_rss_url,
    parse_response,
    parse_rss,
    parse_yf_news,
    sentiment_adjustment,
)
from stock_signal_monitor.signals import Action, Signal

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def _h(title, hours_ago=1, ticker=None):
    return Headline(title, f"https://x/{abs(hash(title))}", "Src", NOW - timedelta(hours=hours_ago), ticker)


def _sig(ticker="NVDA", action=Action.BUY, score=2):
    return Signal(ticker, pd.Timestamp("2026-10-16"), 180.0, action, score, ["단기 골든크로스"], {})


# ---------- collection ----------


def test_parse_yf_news_handles_new_and_old_shapes():
    items = [
        {
            "id": "a",
            "content": {
                "title": "Nvidia unveils new chip",
                "pubDate": "2026-10-17T07:30:00Z",
                "provider": {"displayName": "Reuters"},
                "canonicalUrl": {"url": "https://r/1"},
            },
        },
        {"title": "Old style item", "link": "https://o/2", "publisher": "CNBC", "providerPublishTime": 1792314000},
        {"content": {"title": "", "pubDate": "2026-10-17T07:30:00Z"}},  # dropped: no title
        {"content": {"title": "No date"}},  # dropped: no date
    ]
    out = parse_yf_news(items, "NVDA")
    assert [h.title for h in out] == ["Nvidia unveils new chip", "Old style item"]
    assert out[0].source == "Reuters" and out[0].url == "https://r/1" and out[0].ticker == "NVDA"
    assert out[1].published.tzinfo is not None and out[1].source == "CNBC"


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Chip stocks rally on AI demand - Bloomberg</title><link>https://b/1</link>
<pubDate>Fri, 17 Oct 2026 08:00:00 GMT</pubDate><source url="https://bloomberg.com">Bloomberg</source></item>
<item><title>반도체 수출 급증 - 연합뉴스</title><link>https://y/2</link>
<pubDate>Fri, 17 Oct 2026 02:00:00 GMT</pubDate><source url="https://yna.co.kr">연합뉴스</source></item>
<item><title>no date</title><link>https://n/3</link></item>
</channel></rss>"""


def test_parse_rss_strips_source_suffix():
    out = parse_rss(RSS)
    assert [h.title for h in out] == ["Chip stocks rally on AI demand", "반도체 수출 급증"]
    assert out[0].published == datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)
    assert out[1].source == "연합뉴스" and out[0].ticker is None


def test_google_news_url_switches_locale_on_hangul():
    assert "hl=ko" in google_news_rss_url("반도체 주식") and "ceid=KR:ko" in google_news_rss_url("반도체 주식")
    assert "hl=en-US" in google_news_rss_url("semiconductor stocks")


def test_collect_headlines_merges_dedupes_and_windows():
    def ticker_fetch(t):
        if t == "BAD":
            raise RuntimeError("yahoo down")
        return [_h(f"{t} earnings beat", 2, t), _h("Stale story", 100, t)]

    def query_fetch(q):
        return [_h("Chip stocks rally", 1), _h("NVDA earnings beat!", 3), _h("Future dated", -5)]

    out = collect_headlines(["NVDA", "BAD"], ["semis"], hours=72, now=NOW, ticker_fetch=ticker_fetch, query_fetch=query_fetch)
    titles = [h.title for h in out]
    assert titles == ["Chip stocks rally", "NVDA earnings beat"]  # newest first; dup, stale, future dropped
    assert out[1].ticker == "NVDA"


# ---------- classification ----------


def fake_classifier(payload):
    calls = []

    def _call(prompt, tickers):
        calls.append((prompt, tickers))
        return payload

    _call.calls = calls
    _call.model = "fake-model"
    return _call


def test_analyze_with_fake_classifier_parses_and_clamps():
    heads = [_h("Nvidia beats", 1, "NVDA"), _h("Sector up", 2)]
    payload = {
        "sector": {"score": 1, "summary": ["AI 수요로 업종 강세", "수출 지표 호조", "세 번째 줄은 버림"]},
        "tickers": [
            {"ticker": "nvda", "score": 7, "summary": "실적 서프라이즈\n가이던스 상향"},
            {"ticker": "TSLA", "score": -1, "summary": []},
            {"ticker": "ZZZ", "score": 2, "summary": ["무관"]},
        ],
    }
    clf = fake_classifier(payload)
    res = analyze(heads, ["NVDA", "TSLA"], classifier=clf, now=NOW)
    assert res.scored and res.error is None and res.model == "fake-model"
    assert res.sector == Sentiment(1, ("AI 수요로 업종 강세", "수출 지표 호조"))
    assert res.tickers["NVDA"] == Sentiment(2, ("실적 서프라이즈", "가이던스 상향"))  # clamped to +2
    assert res.tickers["TSLA"].score == -1 and "ZZZ" not in res.tickers
    prompt, tickers = clf.calls[0]
    assert tickers == ["NVDA", "TSLA"] and "[NVDA]" in prompt and "[업종]" in prompt


def test_analyze_without_key_or_on_failure_keeps_headlines(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    heads = [_h("x", 1, "NVDA")]
    res = analyze(heads, ["NVDA"], now=NOW)
    assert not res.scored and res.error == "ANTHROPIC_API_KEY not set" and res.headlines == heads

    def boom(prompt, tickers):
        raise RuntimeError("api down")

    res2 = analyze(heads, ["NVDA"], classifier=boom, now=NOW)
    assert not res2.scored and "api down" in res2.error
    assert not analyze([], ["NVDA"], classifier=boom, now=NOW).scored


def test_anthropic_classifier_uses_structured_output(monkeypatch):
    """The SDK call is mocked: no network, but the request shape is checked."""
    anthropic = pytest.importorskip("anthropic")
    captured = {}

    class FakeBlock:
        type = "text"
        text = json.dumps({"sector": {"score": 0, "summary": ["a"]}, "tickers": []})

    class FakeResponse:
        stop_reason = "end_turn"
        content = [FakeBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    clf = news.AnthropicClassifier("claude-opus-5-5", api_key="test")
    data = clf("prompt", ["NVDA"])
    assert data["sector"]["score"] == 0
    assert captured["model"] == "claude-opus-5-5"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["output_config"]["format"]["schema"] == news.RESPONSE_SCHEMA
    assert captured["messages"][0]["role"] == "user"


def test_parse_response_matches_tickers_case_insensitively():
    sector, per = parse_response({"sector": {"score": "-3"}, "tickers": [{"ticker": "soxl", "score": 1}]}, ["SOXL"])
    assert sector.score == -2 and per["SOXL"].score == 1 and per["SOXL"].summary == ()


def test_build_prompt_lists_tickers_and_headlines():
    p = build_prompt([_h("A story", 1, "NVDA")], ["NVDA", "TSM"])
    assert "NVDA, TSM" in p and "A story" in p


# ---------- cache ----------


def test_get_news_uses_cache_then_refreshes(tmp_path):
    cfg = {"tickers": ["NVDA"], "news": {"cache_hours": 6}}
    path = tmp_path / "news.json"
    payload = {"sector": {"score": 1, "summary": ["x"]}, "tickers": [{"ticker": "NVDA", "score": 2, "summary": ["y"]}]}
    clf = fake_classifier(payload)
    fetch = lambda t: [_h("story", 1, t)]  # noqa: E731
    first = get_news(cfg, cache_path=path, now=NOW, classifier=clf, ticker_fetch=fetch, query_fetch=lambda q: [])
    assert first.tickers["NVDA"].score == 2 and path.exists()
    again = get_news(cfg, cache_path=path, now=NOW + timedelta(hours=1), classifier=clf, ticker_fetch=fetch, query_fetch=lambda q: [])
    assert len(clf.calls) == 1 and again.tickers["NVDA"].score == 2  # served from cache
    get_news(cfg, cache_path=path, now=NOW + timedelta(hours=7), classifier=clf, ticker_fetch=fetch, query_fetch=lambda q: [])
    assert len(clf.calls) == 2  # stale → refreshed
    assert get_news({"tickers": [], "news": {"enabled": False}}, cache_path=path) is None


def test_analysis_roundtrip():
    a = NewsAnalysis(NOW, [_h("t", 1, "NVDA")], Sentiment(1, ("a", "b")), {"NVDA": Sentiment(-2, ("c",))}, "m")
    b = NewsAnalysis.from_dict(json.loads(json.dumps(a.to_dict())))
    assert b.sector == a.sector and b.tickers == a.tickers and b.headlines == a.headlines
    assert b.headlines_for("NVDA", 5) == a.headlines


# ---------- application ----------


@pytest.mark.parametrize(
    "action,score,expected",
    [
        (Action.BUY, 0, 1), (Action.BUY, 2, 1), (Action.BUY, -1, -1), (Action.BUY, -2, -1),
        (Action.SELL, 0, 1), (Action.SELL, -2, 1), (Action.SELL, 1, -1), (Action.SELL, 2, -1),
        (Action.HOLD, 2, 0),
    ],
)
def test_sentiment_adjustment(action, score, expected):
    assert sentiment_adjustment(action, score, 1) == expected
    assert sentiment_adjustment(action, score, 0) == 0


def test_apply_news_weight_adjusts_and_clamps():
    analysis = NewsAnalysis(NOW, [], None, {"NVDA": Sentiment(1, ("good",)), "TSLA": Sentiment(-2, ("bad",))})
    buy, sell_tsla = _sig("NVDA", Action.BUY, 4), _sig("TSLA", Action.SELL, 1)
    weak_buy = _sig("TSLA", Action.BUY, 1)
    out = apply_news_weight([buy, sell_tsla, weak_buy, _sig("GLD")], analysis, weight=1, max_stars=5)
    assert out[0].score == 5 and out[0].reasons[-1] == "뉴스 감성 +1, 신호 강화"
    assert out[1].score == 2 and "신호 강화" in out[1].reasons[-1]
    assert out[2].score == 0 and "뉴스 감성 -2, 신호 약화" in out[2].reasons[-1]
    assert out[3].score == 2 and out[3].reasons == ["단기 골든크로스"]  # no sentiment for GLD
    # weight 0 → untouched; no analysis → untouched
    assert apply_news_weight([buy], analysis, weight=0)[0] is buy
    assert apply_news_weight([buy], None, weight=1)[0] is buy


def test_cautions_lists_strong_sentiment_only():
    analysis = NewsAnalysis(NOW, [], None, {"NVDA": Sentiment(2), "TSLA": Sentiment(-2), "GLD": Sentiment(1)})
    assert [t for t, _ in cautions(analysis, threshold=2)] == ["NVDA", "TSLA"]
    assert [t for t, _ in cautions(analysis, threshold=2, exclude={"NVDA"})] == ["TSLA"]
    assert cautions(None) == []
