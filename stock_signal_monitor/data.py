"""Price data access via Yahoo Finance."""

from __future__ import annotations

import pandas as pd
import yfinance as yf


def fetch_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Download daily OHLCV for ``ticker``; returns a flat-column DataFrame."""
    df = yf.download(ticker, period=period, interval="1d", auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise ValueError(f"No price data returned for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.dropna(subset=["High", "Low", "Close"])
    return df
