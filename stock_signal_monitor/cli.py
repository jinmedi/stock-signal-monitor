"""Command-line entry point.

    python -m stock_signal_monitor              # console only, latest bar
    python -m stock_signal_monitor --telegram   # also push to Telegram
    python -m stock_signal_monitor --lookback 10 --all   # review last 10 bars
    python -m stock_signal_monitor --no-news    # skip news collection / sentiment
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pandas as pd

from . import tracking
from .data import fetch_history
from .events import DEFAULT_BLACKOUT_DAYS, EarningsCalendar, apply_event_filter
from .fx import fetch_usdkrw, latest_rate
from .indicators import StochParams
from .market import last_bar_intraday, market_status
from .news import NewsAnalysis, apply_news_weight, cautions, get_news, news_settings
from .notifier import ConsoleNotifier, TelegramNotifier, format_report
from .signals import Action, Signal, evaluate
from .state import State


def load_config(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def presets_from_config(cfg: dict) -> dict[str, StochParams]:
    return {name: StochParams(**vals) for name, vals in cfg["stochastic"].items()}


def blackout_days(cfg: dict) -> int:
    return int(cfg.get("events", {}).get("earnings_blackout_days", DEFAULT_BLACKOUT_DAYS))


def apply_filters(
    signals: Iterable[Signal],
    *,
    earnings: dict[str, list[date]] | None,
    blackout: int,
    news: NewsAnalysis | None,
    weight: int,
    max_stars: int,
) -> list[Signal]:
    """Post-process evaluated signals: earnings blackout → HOLD, then news-sentiment score nudge.

    ``signals.py`` stays pure; this is the one place both the CLI and the dashboard
    builder apply the event/news layers, so they always agree.
    """
    out = list(signals)
    if blackout > 0:
        out = apply_event_filter(out, earnings, blackout)
    return apply_news_weight(out, news, weight=weight, max_stars=max_stars)


def run(
    cfg: dict,
    *,
    lookback: int,
    include_hold: bool,
    state: State | None,
    closes: dict[str, pd.Series] | None = None,
) -> list[Signal]:
    """Evaluate every ticker; when ``closes`` is given it is filled with each ticker's close series."""
    presets = presets_from_config(cfg)
    th = cfg.get("thresholds", {})
    alerts = cfg.get("alerts", {})
    out: list[Signal] = []
    min_bars = max(p.min_bars for p in presets.values()) + lookback + 1

    for ticker in cfg["tickers"]:
        try:
            df = fetch_history(ticker, cfg.get("history_period", "1y"))
        except Exception as exc:  # network / bad ticker: report and continue
            print(f"[warn] {ticker}: {exc}", file=sys.stderr)
            continue
        if len(df) < min_bars:
            print(f"[warn] {ticker}: only {len(df)} bars, need {min_bars}", file=sys.stderr)
            continue
        if closes is not None:
            closes[ticker] = df["Close"]

        for at in range(-lookback, 0):
            sig = evaluate(
                ticker,
                df,
                presets,
                oversold=th.get("oversold", 20),
                overbought=th.get("overbought", 80),
                require_mid_confirmation=alerts.get("require_mid_confirmation", False),
                at=at,
            )
            if state is not None and at == -1:
                if not state.is_new(ticker, sig.date):
                    continue
                state.mark(ticker, sig.date)
            interesting = sig.action != Action.HOLD or (
                alerts.get("notify_mid_long_crosses", True) and sig.crosses
            )
            if interesting or include_hold:
                out.append(sig)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Stochastic Slow cross monitor")
    p.add_argument("--config", type=Path, default=Path("config.toml"))
    p.add_argument("--state", type=Path, default=Path("state/last_signals.json"))
    p.add_argument("--no-state", action="store_true", help="ignore/skip the dedup state file")
    p.add_argument("--events-cache", type=Path, default=Path("state/events.json"), help="earnings-date cache")
    p.add_argument("--news-cache", type=Path, default=Path("state/news.json"), help="news sentiment cache")
    p.add_argument("--no-news", action="store_true", help="skip news collection and sentiment")
    p.add_argument("--refresh-news", action="store_true", help="ignore the news cache and classify again")
    p.add_argument("--track", type=Path, default=Path("state/track.csv"), help="hit-rate tracking CSV")
    p.add_argument("--lookback", type=int, default=1, help="evaluate the last N bars")
    p.add_argument("--all", action="store_true", help="include HOLD rows (no signal)")
    p.add_argument("--telegram", action="store_true", help="send the report to Telegram")
    p.add_argument("--quiet-when-empty", action="store_true", help="send nothing when no signals")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    state = None if (args.no_state or args.lookback > 1) else State(args.state)
    closes: dict[str, pd.Series] = {}
    signals = run(cfg, lookback=args.lookback, include_hold=args.all, state=state, closes=closes)

    # Earnings blackout: fetched once a week per ticker, cached in state/events.json.
    earnings = None
    blackout = blackout_days(cfg)
    if blackout > 0:
        calendar = EarningsCalendar(args.events_cache)
        earnings = calendar.dates_for(cfg["tickers"])
        calendar.save()

    # News + sentiment: one Claude call for all tickers, cached so the dashboard build reuses it.
    ns = news_settings(cfg)
    news = None if args.no_news else get_news(cfg, cache_path=args.news_cache, force=args.refresh_news)

    signals = apply_filters(
        signals, earnings=earnings, blackout=blackout, news=news, weight=ns["weight"], max_stars=ns["max_stars"]
    )

    # Hit-rate tracking only for the live (latest-bar) run, where sentiment and signal share a date,
    # and never while the newest bar is still forming (resend during the US session).
    status = market_status()
    intraday = last_bar_intraday(status, {s.date.strftime("%Y-%m-%d") for s in signals})
    if state is not None and args.lookback == 1 and not intraday:
        rows = [
            tracking.row_from_signal(s, news.for_ticker(s.ticker).score if news and news.for_ticker(s.ticker) else None)
            for s in signals
        ]
        tracking.update(args.track, new_rows=rows, closes=closes)

    warn = cautions(news, threshold=ns["caution_threshold"], exclude={s.ticker for s in signals})
    usdkrw = latest_rate(fetch_usdkrw("1mo")) if (signals or warn) else None

    report = format_report(
        signals, usdkrw=usdkrw, max_stars=ns["max_stars"], news=news, caution_threshold=ns["caution_threshold"]
    )
    ConsoleNotifier().send(report)
    if args.telegram and (signals or warn or not args.quiet_when_empty):
        TelegramNotifier().send(report)
    if state is not None:
        state.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
