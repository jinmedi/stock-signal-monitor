"""Build the JSON payload the static dashboard (site/) renders.

    python -m stock_signal_monitor.report --out site/data.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .cli import load_config, presets_from_config
from .data import fetch_history
from .fx import align_rates, fetch_usdkrw, is_krw_ticker
from .indicators import StochParams, stoch_slow
from .signals import Action, Cross, detect_cross, evaluate


def _clean(values: pd.Series, ndigits: int) -> list[float | None]:
    out: list[float | None] = []
    for v in values.tolist():
        out.append(None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, ndigits))
    return out


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
) -> dict:
    """Pure function: everything the dashboard needs for one ticker.

    ``usdkrw`` is a daily USD/KRW series; when given and the ticker is USD-priced,
    KRW equivalents are added next to every USD price.
    """
    stochs = {name: stoch_slow(df, p) for name, p in presets.items()}
    tail = df.tail(bars)
    dates = [d.strftime("%Y-%m-%d") for d in tail.index]

    currency = "KRW" if is_krw_ticker(ticker) else "USD"
    rates = None if currency == "KRW" else align_rates(usdkrw, df.index)

    def krw_at(i: int) -> float | None:
        if rates is None:
            return None
        return round(float(df["Close"].iloc[i]) * float(rates.iloc[i]))

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
        sig = evaluate(ticker, df, presets, at=at, **kwargs)
        if sig.action != Action.HOLD:
            signals.append(
                {
                    "date": sig.date.strftime("%Y-%m-%d"),
                    "action": sig.action.value,
                    "score": sig.score,
                    "close": round(sig.close, 2),
                    "close_krw": krw_at(at),
                    "reasons": sig.reasons,
                }
            )

    latest = evaluate(ticker, df, presets, at=-1, **kwargs)
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
        "latest": {
            "date": latest.date.strftime("%Y-%m-%d"),
            "close": round(latest.close, 2),
            "close_krw": krw_at(-1),
            "action": latest.action.value,
            "score": latest.score,
            "reasons": latest.reasons,
            "kd": {
                name: {"k": round(tf.k, 1), "d": round(tf.d, 1), "bullish": tf.bullish}
                for name, tf in latest.timeframes.items()
            },
        },
    }


def build_report(cfg: dict, *, bars: int = 130, signal_lookback: int = 60) -> dict:
    presets = presets_from_config(cfg)
    th = cfg.get("thresholds", {})
    alerts = cfg.get("alerts", {})
    usdkrw = fetch_usdkrw(cfg.get("history_period", "1y"))
    tickers = []
    errors = []
    for ticker in cfg["tickers"]:
        try:
            df = fetch_history(ticker, cfg.get("history_period", "1y"))
            tickers.append(
                build_ticker(
                    ticker,
                    df,
                    presets,
                    bars=bars,
                    signal_lookback=signal_lookback,
                    oversold=th.get("oversold", 20),
                    overbought=th.get("overbought", 80),
                    require_mid_confirmation=alerts.get("require_mid_confirmation", False),
                    usdkrw=usdkrw,
                )
            )
        except Exception as exc:  # keep the rest of the dashboard alive
            print(f"[warn] {ticker}: {exc}", file=sys.stderr)
            errors.append({"ticker": ticker, "error": str(exc)})
    fx = None
    if usdkrw is not None and not usdkrw.empty:
        fx = {"usdkrw": round(float(usdkrw.iloc[-1]), 2), "date": usdkrw.index[-1].strftime("%Y-%m-%d")}
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fx": fx,
        "presets": {name: p.label for name, p in presets.items()},
        "thresholds": {"oversold": th.get("oversold", 20), "overbought": th.get("overbought", 80)},
        "tickers": tickers,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Write dashboard data JSON")
    p.add_argument("--config", type=Path, default=Path("config.toml"))
    p.add_argument("--out", type=Path, default=Path("site/data.json"))
    p.add_argument("--bars", type=int, default=130, help="bars of history to chart")
    p.add_argument("--signal-lookback", type=int, default=60, help="bars of signal history")
    args = p.parse_args(argv)

    report = build_report(load_config(args.config), bars=args.bars, signal_lookback=args.signal_lookback)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.out} ({len(report['tickers'])} tickers)")
    return 1 if not report["tickers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
