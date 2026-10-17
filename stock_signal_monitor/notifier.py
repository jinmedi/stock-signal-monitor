"""Alert delivery: console and Telegram."""

from __future__ import annotations

import json
import os
import sys

import requests

from .fx import is_krw_ticker, to_krw
from .news import DEFAULT_CAUTION_THRESHOLD, NewsAnalysis, Sentiment, cautions
from .signals import Action, Cross, Signal

DEFAULT_MAX_STARS = 4

_CROSS_KO = {Cross.GOLDEN: "골든크로스", Cross.DEAD: "데드크로스"}
_TF_KO = {"short": "단기", "mid": "중기", "long": "장기"}
_ACTION_EMOJI = {Action.BUY: "🟢", Action.SELL: "🔴", Action.HOLD: "⚪"}


def format_price(ticker: str, usd: float, usdkrw: float | None) -> str:
    """``$93.67 (약 125,610원)`` for USD listings, ``75,300원`` for Korean ones."""
    if is_krw_ticker(ticker):
        return f"{usd:,.0f}원"
    krw = to_krw(usd, usdkrw)
    return f"${usd:,.2f}" if krw is None else f"${usd:,.2f} (약 {krw:,.0f}원)"


def format_stars(score: int, max_stars: int = DEFAULT_MAX_STARS) -> str:
    score = max(0, min(max_stars, score))
    return "★" * score + "☆" * (max_stars - score)


def format_news(sent: Sentiment) -> list[str]:
    """``뉴스: 감성 +1`` followed by the (up to two) summary lines, indented."""
    lines = [f"뉴스: 감성 {sent.label}"]
    lines.extend(f"  {line}" for line in sent.summary)
    return lines


def format_signal(
    sig: Signal,
    usdkrw: float | None = None,
    *,
    max_stars: int = DEFAULT_MAX_STARS,
    news: Sentiment | None = None,
) -> str:
    stars = format_stars(sig.score, max_stars) if sig.action != Action.HOLD else ""
    head = f"{_ACTION_EMOJI[sig.action]} {sig.ticker}  {sig.action.value}  {stars}".rstrip()
    lines = [head, f"{sig.date:%Y-%m-%d}  종가 {format_price(sig.ticker, sig.close, usdkrw)}"]
    for r in sig.reasons:
        lines.append(f"  • {r}")
    if news is not None:
        lines.extend(format_news(news))
    other = [
        f"  • {_TF_KO.get(name, name)} {tf.params.label} {_CROSS_KO[tf.cross]}"
        for name, tf in sig.timeframes.items()
        if name != "short" and tf.cross != Cross.NONE
    ]
    if other:
        lines.append("기타 교차:")
        lines.extend(other)
    kd = "  ".join(
        f"{_TF_KO.get(n, n)} K{tf.k:.0f}/D{tf.d:.0f}" for n, tf in sig.timeframes.items()
    )
    lines.append(kd)
    return "\n".join(lines)


def format_caution(ticker: str, sent: Sentiment) -> str:
    lines = [f"⚠ {ticker}  주의  뉴스 감성 {sent.label}"]
    lines.extend(f"  {line}" for line in sent.summary)
    return "\n".join(lines)


def format_report(
    signals: list[Signal],
    title: str = "Stock Signal Monitor",
    usdkrw: float | None = None,
    *,
    max_stars: int = DEFAULT_MAX_STARS,
    news: NewsAnalysis | None = None,
    caution_threshold: int = DEFAULT_CAUTION_THRESHOLD,
) -> str:
    """Signals, then ⚠ cautions for strongly-newsed tickers without a signal, then sector news / FX footer."""
    blocks = [
        format_signal(s, usdkrw, max_stars=max_stars, news=news.for_ticker(s.ticker) if news else None)
        for s in signals
    ]
    blocks.extend(
        format_caution(t, sent)
        for t, sent in cautions(news, threshold=caution_threshold, exclude={s.ticker for s in signals})
    )
    if not blocks:
        return f"{title}\n알릴 신호가 없습니다."
    foot: list[str] = []
    if news is not None and news.sector is not None:
        foot.append("\n".join([f"업종 뉴스: 감성 {news.sector.label}", *(f"  {ln}" for ln in news.sector.summary)]))
    if usdkrw:
        foot.append(f"환율 {usdkrw:,.0f}원/$ 기준")
    tail = ("\n\n" + "\n\n".join(foot)) if foot else ""
    return f"{title}\n\n" + "\n\n".join(blocks) + tail


class ConsoleNotifier:
    def send(self, text: str) -> None:
        # Windows consoles often default to cp949/cp1252 which cannot encode emoji.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = (token or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        self.chat_id = (chat_id or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
        if not self.token or not self.chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        # Telegram caps messages at 4096 chars; split on blank lines when needed.
        for chunk in _chunk(text, 4000):
            resp = requests.post(url, json={"chat_id": self.chat_id, "text": chunk}, timeout=30)
            if not resp.ok:
                # Never echo the URL: it contains the bot token.
                raise RuntimeError(telegram_error(resp.status_code, resp.text, self.chat_id))


def telegram_error(status: int, body: str, chat_id: str) -> str:
    """Build a token-free, actionable message from a Telegram API error body."""
    try:
        desc = json.loads(body).get("description", body)
    except (ValueError, AttributeError):
        desc = body
    hint = ""
    if "chat not found" in desc.lower():
        hint = (
            " (chat_id가 틀렸거나, 그 사용자가 봇에게 먼저 메시지를 보낸 적이 없습니다. "
            "봇과 대화를 시작한 뒤 getUpdates 로 chat.id 를 다시 확인하세요)"
        )
    elif status == 401:
        hint = " (봇 토큰이 올바르지 않습니다)"
    shown = chat_id if chat_id.lstrip("-").isdigit() else f"{chat_id!r} (숫자가 아님)"
    return f"Telegram API {status}: {desc}{hint} — chat_id={shown}"


def _chunk(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) + 2 > limit:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    return chunks
