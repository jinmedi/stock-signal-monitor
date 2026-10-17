import numpy as np
import pandas as pd

from stock_signal_monitor.indicators import StochParams, stoch_slow


def _frame(closes, spread=1.0):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range("2026-01-01", periods=len(closes))
    return pd.DataFrame(
        {"High": closes + spread, "Low": closes - spread, "Close": closes}, index=idx
    )


def test_fast_k_bounds_and_sma_smoothing():
    df = _frame([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
    out = stoch_slow(df, StochParams(5, 1, 1))  # m=t=1 → raw fast %K
    # Monotonic +1/bar rise with ±1 range: 5-bar high = close+1, 5-bar low =
    # (close-4)-1, so K = (close-low)/(high-low) = 5/6*100 ≈ 83.3 once warmed up.
    assert out["k"].iloc[4:].round(3).eq(round(500 / 6, 3)).all()
    assert out["d"].equals(out["k"])


def test_flat_range_yields_nan_not_error():
    df = _frame([5, 5, 5, 5, 5, 5], spread=0.0)
    out = stoch_slow(df, StochParams(3, 1, 1))
    assert out["k"].isna().all()


def test_min_bars_matches_first_valid_index():
    df = _frame(np.linspace(1, 50, 50) + np.sin(np.arange(50)))
    p = StochParams(5, 3, 3)
    out = stoch_slow(df, p)
    first_valid = out["d"].first_valid_index()
    assert df.index.get_loc(first_valid) == p.min_bars - 1


def test_default_presets_labels():
    assert StochParams(5, 3, 3).label == "5-3-3"
    assert StochParams(20, 12, 12).min_bars == 42
