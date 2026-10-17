"""Build the JSON payload the static dashboard (site/) renders.

    python -m stock_signal_monitor.report --out site/data.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from . import tracking
from .cli import apply_filters, blackout_days, load_config, presets_from_config
from .data import fetch_history
from .events import EarningsCalendar
from .fx import align_rates, fetch_usdkrw, is_krw_ticker
from .indicators import StochParams, stoch_slow
from .news import NewsAnalysis, get_news, news_settings
from .signals import Action, Cross, Signal, detect_cross, evaluate  # noqa: F401


def _clean(values: pd.Series, ndigits: int) -> list[float | None]:
    out: list[float | None] = []
    for v in values.tolist():
        out.append(None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, ndigits))
    return out


def _news_payload(ticker: str, news: NewsAnalysis | None) -> dict | None:
    """``{"score", "summary", "headlines"}`` for one ticker, or ``None`` when there is no news data."""
    if news is None:
        return None
    sent = news.for_ticker(ticker)
    heads = news.headlines_for(ticker, 5)
    if sent is None and not heads:
        return None
    return {
        "score": None if sent is None else sent.score,
        "summary": [] if sent is None else list(sent.summary),
        "headlines": [
            {"title": h.title, "url": h.url, "source": h.source, "published": h.published.strftime("%Y-%m-%d %H:%M")}
            for h in heads
        ],
    }


def build_ticker(
    ticker: str,
    df: pd.DataFrame,
    presets: dict[str, StochParams],
    *,
    bars: int = 130,
    signal_lookback: int = 60,
    oversold: float = 20,
    overbought: float = 80,
    require_mid_confirmation: bool = False,
    usdkrw: pd.Series | None = None,
    earnings_dates: Sequence[date] | None = None,
    blackout: int = 0,
    news: NewsAnalysis | None = None,
    news_weight: int = 0,
    max_stars: int = 4,
) -> dict:
    """Pure function: everything the dashboard needs for one ticker.

    ``usdkrw`` is a daily USD/KRW series; when given and the ticker is USD-priced,
    KRW equivalents are added next to every USD price. ``earnings_dates`` /
    ``blackout`` apply the earnings filter to the signal history too (back-test),
    ``news`` adds the sentiment and headline links.
    """
    stochs = {name: stoch_slow(df, p) for name, p in presets.items()}
    tail = df.tail(bars)
    dates = [d.strftime("%Y-%m-%d") for d in tail.index]

    currency = "KRW" if is_krw_ticker(ticker) else "USD"
    rates = None if currency == "KRW" else align_rates(usdkrw, df.index)
    earnings = {ticker: list(earnings_dates)} if earnings_dates else None

    def krw_at(i: int) -> float | None:
        if rates is None:
            return None
        return round(float(df["Close"].iloc[i]) * float(rates.iloc[i]))

    def post(sig: Signal, *, with_news: bool) -> Signal:
        return apply_filters(
            [sig],
            earnings=earnings,
            blackout=blackout,
            news=news if with_news else None,
            weight=news_weight,
            max_stars=max_stars,
        )[0]

    timeframes: dict[str, dict] = {}
    crosses: list[dict] = []
    for name, st in stochs.items():
        st_tail = st.tail(bars)
        timeframes[name] = {
            "label": presets[name].label,
            "k": _clean(st_tail["k"], 1),
            "d": _clean(st_tail["d"], 1),
        }
        k, d = st_tail["k"].to_numpy(), st_tail["d"].to_numpy()
        for i in range(1, len(st_tail)):
            c = detect_cross(k[i - 1], d[i - 1], k[i], d[i])
            if c != Cross.NONE:
                crosses.append({"date": dates[i], "tf": name, "type": c.value, "k": round(float(k[i]), 1)})

    kwargs = dict(
        oversold=oversold,
        overbought=overbought,
        require_mid_confirmation=require_mid_confirmation,
    )
    signals = []
    lookback = min(signal_lookback, len(df) - 1)
    for at in range(-lookback, 0):
        raw = evaluate(ticker, df, presets, at=at, **kwargs)
        if raw.action == Action.HOLD:
            continue
        # Earnings filter applies to history (back-test); today's news only to the latest bar.
        sig = post(raw, with_news=(at == -1))
        signals.append(
            {
                "date": sig.date.strftime("%Y-%m-%d"),
                "action": sig.action.value,
                "cross": raw.action.value,
                "score": sig.score,
                "close": round(sig.close, 2),
                "close_krw": krw_at(at),
                "reasons": sig.reasons,
            }
        )

    latest = post(evaluate(ticker, df, presets, at=-1, **kwargs), with_news=True)
    close_krw = None if rates is None else _clean((tail["Close"] * rates.tail(bars)).round(), 0)
    return {
        "ticker": ticker,
        "currency": currency,
        "dates": dates,
        "close": _clean(tail["Close"], 2),
        "close_krw": close_krw,
        "timeframes": timeframes,
        "crosses": crosses,
        "signals": signals,
        "news": _news_payload(ticker, news),
        "latest": {
            "date": latest.date.strftime("%Y-%m-%d"),
            "close": round(latest.close, 2),
            "close_krw": krw_at(-1),
            "action": latest.action.value,
            "cross": latest.timeframes["short"].cross.value if "short" in latest.timeframes else "none",
            "score": latest.score,
            "reasons": latest.reasons,
            "kd": {
                name: {"k": round(tf.k, 1), "d": round(tf.d, 1), "bullish": tf.bullish}
                for name, tf in latest.timeframes.items()
            },
        },
    }


def build_report(
    cfg: dict,
    *,
    bars: int = 130,
    signal_lookback: int = 60,
    state_dir: Path | None = Path("state"),
    skip_news: bool = False,
) -> dict:
    presets = presets_from_config(cfg)
    th = cfg.get("thresholds", {})
    alerts = cfg.get("alerts", {})
    ns = news_settings(cfg)
    usdkrw = fetch_usdkrw(cfg.get("history_period", "1y"))

    blackout = blackout_days(cfg)
    earnings: dict[str, list[date]] = {}
    if blackout > 0:
        calendar = EarningsCalendar(None if state_dir is None else state_dir / "events.json")
        earnings = calendar.dates_for(cfg["tickers"])
        calendar.save()

    news = None if skip_news else get_news(cfg, cache_path=None if state_dir is None else state_dir / "news.json")

    tickers = []
    errors = []
    closes: dict[str, pd.Series] = {}
    latest_rows: list[dict | None] = []
    for ticker in cfg["tickers"]:
        try:
            df = fetch_history(ticker, cfg.get("history_period", "1y"))
            payload = build_ticker(
                ticker,
                df,
                presets,
                bars=bars,
                signal_lookback=signal_lookback,
                oversold=th.get("oversold", 20),
                overbought=th.get("overbought", 80),
                require_mid_confirmation=alerts.get("require_mid_confirmation", False),
                usdkrw=usdkrw,
                earnings_dates=earnings.get(ticker),
                blackout=blackout,
                news=news,
                news_weight=ns["weight"],
                max_stars=ns["max_stars"],
            )
            L = payload["latest"]
            sent = news.for_ticker(ticker) if news else None
            latest_rows.append(
                tracking.row(L["date"], ticker, L["cross"], L["action"], L["score"], None if sent is None else sent.score, L["close"])
            )
            closes[ticker] = df["Close"]
            tickers.append(payload)
        except Exception as exc:  # keep the rest of the dashboard alive
            print(f"[warn] {ticker}: {exc}", file=sys.stderr)
            errors.append({"ticker": ticker, "error": str(exc)})

    # Hit-rate tracking: record today's crosses, fill 5/10-day returns, aggregate.
    track_rows = tracking.update(
        None if state_dir is None else state_dir / "track.csv", new_rows=latest_rows, closes=closes
    )

    fx = None
    if usdkrw is not None and not usdkrw.empty:
        fx = {"usdkrw": round(float(usdkrw.iloc[-1]), 2), "date": usdkrw.index[-1].strftime("%Y-%m-%d")}
    news_meta = None
    if news is not None:
        news_meta = {
            "fetched_at": news.fetched_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": news.model,
            "scored": news.scored,
            "error": news.error,
            "weight": ns["weight"],
            "caution_threshold": ns["caution_threshold"],
            "sector": None if news.sector is None else news.sector.to_dict(),
            "headline_count": len(news.headlines),
        }
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fx": fx,
        "presets": {name: p.label for name, p in presets.items()},
        "thresholds": {"oversold": th.get("oversold", 20), "overbought": th.get("overbought", 80)},
        "max_stars": ns["max_stars"],
        "events": {"blackout_days": blackout, "earnings": {t: [d.isoformat() for d in ds] for t, ds in earnings.items()}},
        "news": news_meta,
        "tracking": tracking.hit_rate(track_rows),
        "tickers": tickers,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Write dashboard data JSON")
    p.add_argument("--config", type=Path, default=Path("config.toml"))
    p.add_argument("--out", type=Path, default=Path("site/data.json"))
    p.add_argument("--state-dir", type=Path, default=Path("state"), help="events/news cache and track.csv location")
    p.add_argument("--no-news", action="store_true", help="skip news collection and sentiment")
    p.add_argument("--bars", type=int, default=130, help="bars of history to chart")
    p.add_argument("--signal-lookback", type=int, default=60, help="bars of signal history")
    args = p.parse_args(argv)

    report = build_report(
        load_config(args.config),
        bars=args.bars,
        signal_lookback=args.signal_lookback,
        state_dir=args.state_dir,
        skip_news=args.no_news,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.out} ({len(report['tickers'])} tickers)")
    return 1 if not report["tickers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
