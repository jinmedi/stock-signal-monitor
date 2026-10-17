"""USD/KRW exchange rate helpers (Yahoo Finance ``KRW=X``)."""

from __future__ import annotations

import sys

import pandas as pd
import yfinance as yf

KRW_SUFFIXES = (".KS", ".KQ")


def is_krw_ticker(ticker: str) -> bool:
    """Korean listings are already priced in KRW; everything else is treated as USD."""
    return ticker.upper().endswith(KRW_SUFFIXES)


def fetch_usdkrw(period: str = "1y") -> pd.Series | None:
    """Daily USD/KRW closes indexed by date, or ``None`` when the download fails."""
    try:
        df = yf.download("KRW=X", period=period, interval="1d", auto_adjust=False, progress=False)
    except Exception as exc:  # network hiccup: run without KRW figures
        print(f"[warn] KRW=X: {exc}", file=sys.stderr)
        return None
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df["Close"].dropna().rename("usdkrw")


def align_rates(rates: pd.Series | None, index: pd.DatetimeIndex) -> pd.Series | None:
    """Rates re-indexed to the price dates (forward-filled over FX holidays)."""
    if rates is None:
        return None
    return rates.reindex(rates.index.union(index)).ffill().reindex(index).bfill()


def latest_rate(rates: pd.Series | None) -> float | None:
    if rates is None or rates.empty:
        return None
    return float(rates.iloc[-1])


def to_krw(usd: float, rate: float | None) -> float | None:
    return None if rate is None else usd * rate
