"""News collection and LLM sentiment scoring.

Collection: yfinance ``Ticker.news`` per ticker plus Google News RSS searches for
sector-wide queries, merged and limited to the last ``hours`` (72 by default).

Classification: one Claude call (``claude-opus-5-5``) with a JSON-schema
structured output returns a sector score and a per-ticker score in -2..+2 plus a
two-line Korean summary. The model call is behind the ``Classifier`` interface
so tests inject a fake; without ``ANTHROPIC_API_KEY`` (or on any API failure)
the headlines are kept and the scores are simply absent.

Application: ``apply_news_weight`` nudges BUY/SELL scores by ``weight`` when
the sentiment agrees/disagrees with the signal, ``cautions`` lists tickers whose
sentiment is strong enough to warn about even without a cross.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus

import requests

from .signals import Action, Signal

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_QUERIES = ["semiconductor stocks", "반도체 주식"]
DEFAULT_HOURS = 72
DEFAULT_MAX_STARS = 5
DEFAULT_CAUTION_THRESHOLD = 2
MAX_HEADLINES_PER_SOURCE = 12
MAX_HEADLINES_TOTAL = 80
SCORE_RANGE = (-2, 2)

_HANGUL = re.compile(r"[가-힣]")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Headline:
    title: str
    url: str
    source: str
    published: datetime  # tz-aware UTC
    ticker: str | None = None  # None → sector-wide query result

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published": self.published.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "ticker": self.ticker,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Headline":
        return cls(d["title"], d.get("url", ""), d.get("source", ""), _parse_iso(d["published"]), d.get("ticker"))


@dataclass(frozen=True)
class Sentiment:
    score: int  # -2..+2
    summary: tuple[str, ...] = ()  # up to two Korean lines

    @property
    def label(self) -> str:
        return f"{self.score:+d}"

    def to_dict(self) -> dict:
        return {"score": self.score, "summary": list(self.summary)}

    @classmethod
    def from_dict(cls, d: dict) -> "Sentiment":
        return cls(_clamp_score(d.get("score", 0)), tuple(_two_lines(d.get("summary", []))))


@dataclass
class NewsAnalysis:
    fetched_at: datetime
    headlines: list[Headline] = field(default_factory=list)
    sector: Sentiment | None = None
    tickers: dict[str, Sentiment] = field(default_factory=dict)
    model: str | None = None
    error: str | None = None

    @property
    def scored(self) -> bool:
        return self.sector is not None or bool(self.tickers)

    def for_ticker(self, ticker: str) -> Sentiment | None:
        return self.tickers.get(ticker)

    def headlines_for(self, ticker: str, limit: int = 5) -> list[Headline]:
        own = [h for h in self.headlines if h.ticker == ticker]
        sector = [h for h in self.headlines if h.ticker is None]
        return (own + sector)[:limit]

    def to_dict(self) -> dict:
        return {
            "fetched_at": self.fetched_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "model": self.model,
            "error": self.error,
            "sector": None if self.sector is None else self.sector.to_dict(),
            "tickers": {t: s.to_dict() for t, s in sorted(self.tickers.items())},
            "headlines": [h.to_dict() for h in self.headlines],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NewsAnalysis":
        return cls(
            fetched_at=_parse_iso(d["fetched_at"]),
            headlines=[Headline.from_dict(h) for h in d.get("headlines", [])],
            sector=None if d.get("sector") is None else Sentiment.from_dict(d["sector"]),
            tickers={t: Sentiment.from_dict(s) for t, s in d.get("tickers", {}).items()},
            model=d.get("model"),
            error=d.get("error"),
        )


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _clamp_score(v) -> int:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        n = 0
    return max(SCORE_RANGE[0], min(SCORE_RANGE[1], n))


def _two_lines(summary) -> list[str]:
    if isinstance(summary, str):
        parts = [p.strip() for p in summary.splitlines()]
    else:
        parts = [str(p).strip() for p in (summary or [])]
    return [p for p in parts if p][:2]


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_yf_news(items: Iterable[dict], ticker: str) -> list[Headline]:
    """Normalise yfinance news items (both the 2024 ``content`` shape and the older flat one)."""
    out: list[Headline] = []
    for item in items or []:
        c = item.get("content") if isinstance(item.get("content"), dict) else item
        title = (c.get("title") or "").strip()
        if not title:
            continue
        published = None
        if c.get("pubDate"):
            try:
                published = _parse_iso(str(c["pubDate"]))
            except ValueError:
                published = None
        elif c.get("providerPublishTime") is not None:
            published = datetime.fromtimestamp(float(c["providerPublishTime"]), tz=timezone.utc)
        if published is None:
            continue
        url = ""
        for key in ("canonicalUrl", "clickThroughUrl"):
            if isinstance(c.get(key), dict) and c[key].get("url"):
                url = c[key]["url"]
                break
        url = url or c.get("link") or ""
        provider = c.get("provider")
        source = provider.get("displayName", "") if isinstance(provider, dict) else (c.get("publisher") or "")
        out.append(Headline(title, url, source, published, ticker))
    return out


def fetch_yf_news(ticker: str, count: int = MAX_HEADLINES_PER_SOURCE) -> list[Headline]:
    import yfinance as yf

    return parse_yf_news(yf.Ticker(ticker).get_news(count=count), ticker)


def google_news_rss_url(query: str) -> str:
    if _HANGUL.search(query):
        return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


def parse_rss(xml_text: str) -> list[Headline]:
    """Headlines from a Google News RSS document (``title`` there is ``headline - source``)."""
    out: list[Headline] = []
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate")
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        if not title or not pub:
            continue
        try:
            published = _to_utc(parsedate_to_datetime(pub))
        except (TypeError, ValueError):
            continue
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].rstrip()
        out.append(Headline(title, link, source, published, None))
    return out


def fetch_google_news(query: str, timeout: int = 20) -> list[Headline]:
    resp = requests.get(google_news_rss_url(query), timeout=timeout, headers={"User-Agent": "stock-signal-monitor"})
    resp.raise_for_status()
    return parse_rss(resp.text)[:MAX_HEADLINES_PER_SOURCE]


def _norm_title(title: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", " ", title.lower()).strip()


def collect_headlines(
    tickers: Iterable[str],
    queries: Iterable[str] = DEFAULT_QUERIES,
    *,
    hours: int = DEFAULT_HOURS,
    now: datetime | None = None,
    ticker_fetch: Callable[[str], list[Headline]] = fetch_yf_news,
    query_fetch: Callable[[str], list[Headline]] = fetch_google_news,
) -> list[Headline]:
    """Merge per-ticker and sector headlines from the last ``hours``; newest first, titles de-duplicated."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    raw: list[Headline] = []
    for t in tickers:
        try:
            raw.extend(ticker_fetch(t))
        except Exception as exc:
            print(f"[warn] news {t}: {exc}", file=sys.stderr)
    for q in queries:
        try:
            raw.extend(query_fetch(q))
        except Exception as exc:
            print(f"[warn] news rss {q!r}: {exc}", file=sys.stderr)

    seen: set[str] = set()
    out: list[Headline] = []
    for h in sorted(raw, key=lambda h: h.published, reverse=True):
        if h.published < cutoff or h.published > now + timedelta(hours=1):
            continue
        key = _norm_title(h.title)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out[:MAX_HEADLINES_TOTAL]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

Classifier = Callable[[str, list[str]], dict]
"""``classifier(prompt, tickers) -> dict`` matching ``RESPONSE_SCHEMA``."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "sector": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "enum": [-2, -1, 0, 1, 2]},
                "summary": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["score", "summary"],
            "additionalProperties": False,
        },
        "tickers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "score": {"type": "integer", "enum": [-2, -1, 0, 1, 2]},
                    "summary": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["ticker", "score", "summary"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sector", "tickers"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "당신은 반도체·기술주 뉴스 헤드라인을 읽고 단기(1~2주) 주가 영향을 평가하는 애널리스트입니다. "
    "주어진 헤드라인만 근거로 삼고, 모르면 0을 주세요. "
    "점수: -2 매우 부정, -1 부정, 0 중립/영향 없음, +1 긍정, +2 매우 긍정. "
    "summary 는 한국어 두 줄(각 40자 이내)로, 점수의 이유가 되는 사실만 적습니다. "
    "요청된 모든 종목에 대해 하나씩 답하고, 해당 종목 뉴스가 없으면 0점에 '관련 뉴스 없음'이라고 씁니다."
)


def build_prompt(headlines: Iterable[Headline], tickers: Iterable[str]) -> str:
    lines = ["평가할 종목: " + ", ".join(tickers), "", "최근 72시간 헤드라인 (시각은 UTC):"]
    for h in headlines:
        tag = h.ticker or "업종"
        lines.append(f"- [{tag}] {h.published:%m-%d %H:%M} {h.title} ({h.source})")
    lines.append("")
    lines.append("업종(반도체) 전체 감성과 종목별 감성을 JSON 으로 답하세요.")
    return "\n".join(lines)


class AnthropicClassifier:
    """Calls Claude once for all tickers with a JSON-schema structured output."""

    def __init__(self, model: str = DEFAULT_MODEL, *, api_key: str | None = None, max_tokens: int = 2048):
        import anthropic  # imported lazily so the monitor runs without the package

        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def __call__(self, prompt: str, tickers: list[str]) -> dict:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}, "effort": "low"},
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model refused the request")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise RuntimeError(f"no text block in response (stop_reason={response.stop_reason})")
        return json.loads(text)


def default_classifier(model: str = DEFAULT_MODEL) -> Classifier | None:
    """An Anthropic classifier when ``ANTHROPIC_API_KEY`` is set, else ``None``."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return None
    try:
        return AnthropicClassifier(model)
    except Exception as exc:  # package missing / bad key format
        print(f"[warn] news classifier unavailable: {exc}", file=sys.stderr)
        return None


def parse_response(data: dict, tickers: Iterable[str]) -> tuple[Sentiment, dict[str, Sentiment]]:
    wanted = {t.upper(): t for t in tickers}
    sector_raw = data.get("sector") or {}
    sector = Sentiment(_clamp_score(sector_raw.get("score", 0)), tuple(_two_lines(sector_raw.get("summary"))))
    per: dict[str, Sentiment] = {}
    for item in data.get("tickers") or []:
        key = str(item.get("ticker", "")).upper().strip()
        if key in wanted:
            per[wanted[key]] = Sentiment(_clamp_score(item.get("score", 0)), tuple(_two_lines(item.get("summary"))))
    return sector, per


def analyze(
    headlines: list[Headline],
    tickers: list[str],
    *,
    classifier: Classifier | None = None,
    model: str = DEFAULT_MODEL,
    now: datetime | None = None,
) -> NewsAnalysis:
    """Score ``headlines`` for ``tickers``; never raises — failures leave the scores empty."""
    now = now or datetime.now(timezone.utc)
    result = NewsAnalysis(fetched_at=now, headlines=list(headlines))
    if not headlines:
        result.error = "no headlines"
        return result
    if classifier is None:
        classifier = default_classifier(model)
    if classifier is None:
        result.error = "ANTHROPIC_API_KEY not set"
        return result
    try:
        data = classifier(build_prompt(headlines, tickers), list(tickers))
        result.sector, result.tickers = parse_response(data, tickers)
        result.model = getattr(classifier, "model", model)
    except Exception as exc:
        print(f"[warn] news sentiment: {exc}", file=sys.stderr)
        result.error = str(exc)
    return result


# ---------------------------------------------------------------------------
# Cache (state/news.json) so the monitor and the dashboard build share one call
# ---------------------------------------------------------------------------


def load_cached(path: Path | None, *, max_age: timedelta, now: datetime | None = None) -> NewsAnalysis | None:
    if path is None or not path.exists():
        return None
    now = now or datetime.now(timezone.utc)
    try:
        cached = NewsAnalysis.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, KeyError, OSError) as exc:
        print(f"[warn] news cache unreadable: {exc}", file=sys.stderr)
        return None
    if now - cached.fetched_at > max_age or not cached.scored:
        return None
    return cached


def save_cache(path: Path | None, analysis: NewsAnalysis) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analysis.to_dict(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def news_settings(cfg: dict) -> dict:
    """``[news]`` section with defaults filled in."""
    n = cfg.get("news", {}) or {}
    return {
        "enabled": bool(n.get("enabled", True)),
        "weight": int(n.get("weight", 0)),
        "max_stars": int(n.get("max_stars", DEFAULT_MAX_STARS)),
        "hours": int(n.get("hours", DEFAULT_HOURS)),
        "queries": list(n.get("queries", DEFAULT_QUERIES)),
        "model": str(n.get("model", DEFAULT_MODEL)),
        "cache_hours": float(n.get("cache_hours", 12)),
        "caution_threshold": int(n.get("caution_threshold", DEFAULT_CAUTION_THRESHOLD)),
    }


def get_news(
    cfg: dict,
    *,
    cache_path: Path | None,
    now: datetime | None = None,
    classifier: Classifier | None = None,
    ticker_fetch: Callable[[str], list[Headline]] = fetch_yf_news,
    query_fetch: Callable[[str], list[Headline]] = fetch_google_news,
) -> NewsAnalysis | None:
    """Cached-or-fresh news analysis for the configured tickers (``None`` when ``[news] enabled = false``)."""
    s = news_settings(cfg)
    if not s["enabled"]:
        return None
    now = now or datetime.now(timezone.utc)
    cached = load_cached(cache_path, max_age=timedelta(hours=s["cache_hours"]), now=now)
    if cached is not None:
        return cached
    tickers = list(cfg["tickers"])
    headlines = collect_headlines(
        tickers, s["queries"], hours=s["hours"], now=now, ticker_fetch=ticker_fetch, query_fetch=query_fetch
    )
    analysis = analyze(headlines, tickers, classifier=classifier, model=s["model"], now=now)
    if analysis.scored:
        save_cache(cache_path, analysis)
    return analysis


# ---------------------------------------------------------------------------
# Applying sentiment to signals
# ---------------------------------------------------------------------------


def sentiment_adjustment(action: Action, score: int, weight: int) -> int:
    """+weight when the news agrees with the signal, -weight when it contradicts, else 0.

    BUY: sentiment >= 0 → +, <= -1 → -.  SELL is the mirror image: <= 0 → +, >= 1 → -.
    """
    if weight == 0 or action == Action.HOLD:
        return 0
    if action == Action.BUY:
        return weight if score >= 0 else -weight
    return weight if score <= 0 else -weight


def apply_news_weight(
    signals: Iterable[Signal],
    analysis: NewsAnalysis | None,
    *,
    weight: int = 0,
    max_stars: int = DEFAULT_MAX_STARS,
) -> list[Signal]:
    """Copies of ``signals`` with scores nudged by ticker sentiment (no-op when ``weight`` is 0)."""
    out: list[Signal] = []
    for sig in signals:
        sent = analysis.for_ticker(sig.ticker) if analysis is not None else None
        if sent is None or sig.action == Action.HOLD or weight == 0:
            out.append(sig)
            continue
        delta = sentiment_adjustment(sig.action, sent.score, weight)
        if delta == 0:
            out.append(sig)
            continue
        new_score = max(0, min(max_stars, sig.score + delta))
        verdict = "신호 강화" if delta > 0 else "신호 약화"
        out.append(
            dataclasses.replace(
                sig,
                score=new_score,
                reasons=[*sig.reasons, f"뉴스 감성 {sent.label}, {verdict}"],
            )
        )
    return out


def cautions(
    analysis: NewsAnalysis | None,
    *,
    threshold: int = DEFAULT_CAUTION_THRESHOLD,
    exclude: Iterable[str] = (),
) -> list[tuple[str, Sentiment]]:
    """Tickers whose |sentiment| >= threshold and that are not already in ``exclude`` (signalled tickers)."""
    if analysis is None:
        return []
    skip = set(exclude)
    return [
        (t, s)
        for t, s in sorted(analysis.tickers.items())
        if abs(s.score) >= threshold and t not in skip
    ]
