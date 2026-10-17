"""Alert delivery: console and Telegram."""

from __future__ import annotations

import os
import sys

import requests

from .signals import Action, Cross, Signal

_CROSS_KO = {Cross.GOLDEN: "골든크로스", Cross.DEAD: "데드크로스"}
_TF_KO = {"short": "단기", "mid": "중기", "long": "장기"}
_ACTION_EMOJI = {Action.BUY: "🟢", Action.SELL: "🔴", Action.HOLD: "⚪"}


def format_signal(sig: Signal) -> str:
    stars = "★" * sig.score + "☆" * max(0, 4 - sig.score) if sig.action != Action.HOLD else ""
    head = f"{_ACTION_EMOJI[sig.action]} {sig.ticker}  {sig.action.value}  {stars}".rstrip()
    lines = [head, f"{sig.date:%Y-%m-%d}  종가 {sig.close:,.2f}"]
    for r in sig.reasons:
        lines.append(f"  • {r}")
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


def format_report(signals: list[Signal], title: str = "Stock Signal Monitor") -> str:
    if not signals:
        return f"{title}\n알릴 신호가 없습니다."
    body = "\n\n".join(format_signal(s) for s in signals)
    return f"{title}\n\n{body}"


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
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        if not self.token or not self.chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        # Telegram caps messages at 4096 chars; split on blank lines when needed.
        for chunk in _chunk(text, 4000):
            resp = requests.post(url, json={"chat_id": self.chat_id, "text": chunk}, timeout=30)
            resp.raise_for_status()


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
