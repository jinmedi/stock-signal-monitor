import json

import numpy as np
import pandas as pd

from stock_signal_monitor.indicators import StochParams
from stock_signal_monitor.report import build_ticker

PRESETS = {
    "short": StochParams(5, 3, 3),
    "mid": StochParams(10, 6, 6),
    "long": StochParams(20, 12, 12),
}


def _frame(n=200, seed=0):
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 1.5, n))
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame(
        {"High": closes + 1, "Low": closes - 1, "Close": closes}, index=idx
    )


def test_build_ticker_shapes_and_json_safe():
    payload = build_ticker("TEST", _frame(), PRESETS, bars=50, signal_lookback=30)
    assert payload["ticker"] == "TEST"
    assert len(payload["dates"]) == 50 == len(payload["close"])
    for tf in payload["timeframes"].values():
        assert len(tf["k"]) == 50 and len(tf["d"]) == 50
        assert all(v is None or 0 <= v <= 100 for v in tf["k"] if v is not None)
    # warm-up is long gone after 200 bars → no NaN holes in the charted window
    assert all(v is not None for v in payload["timeframes"]["long"]["d"])
    json.dumps(payload)  # must serialise (no NaN, no numpy types)


def test_crosses_and_signals_are_consistent():
    payload = build_ticker("TEST", _frame(), PRESETS, bars=120, signal_lookback=60)
    short_cross_dates = {c["date"] for c in payload["crosses"] if c["tf"] == "short"}
    assert short_cross_dates, "random walk should produce short crosses"
    # every BUY/SELL in the history sits on a short-timeframe cross date
    for s in payload["signals"]:
        assert s["action"] in {"BUY", "SELL"}
        assert s["date"] in short_cross_dates
    latest = payload["latest"]
    assert latest["date"] == payload["dates"][-1]
    assert set(latest["kd"]) == set(PRESETS)
