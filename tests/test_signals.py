import numpy as np
import pandas as pd
import pytest

from stock_signal_monitor.indicators import StochParams
from stock_signal_monitor.signals import Action, Cross, detect_cross, evaluate

PRESETS = {
    "short": StochParams(5, 3, 3),
    "mid": StochParams(10, 6, 6),
    "long": StochParams(20, 12, 12),
}


def _frame(closes):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range("2025-01-01", periods=len(closes))
    return pd.DataFrame(
        {"High": closes * 1.01, "Low": closes * 0.99, "Close": closes}, index=idx
    )


@pytest.mark.parametrize(
    "prev_k,prev_d,k,d,expected",
    [
        (40, 45, 50, 48, Cross.GOLDEN),
        (45, 45, 50, 48, Cross.GOLDEN),  # touching then above counts
        (60, 55, 50, 52, Cross.DEAD),
        (60, 55, 58, 56, Cross.NONE),
        (float("nan"), 55, 58, 56, Cross.NONE),
    ],
)
def test_detect_cross(prev_k, prev_d, k, d, expected):
    assert detect_cross(prev_k, prev_d, k, d) == expected


def _v_shape():
    # 60 bars down, then a sharp turn up: should produce a short golden cross
    down = np.linspace(100, 60, 60)
    up = np.linspace(60, 75, 12)
    return _frame(np.concatenate([down, up[1:]]))


def test_golden_cross_after_v_bottom_is_buy():
    df = _v_shape()
    found = [
        evaluate("T", df, PRESETS, at=at)
        for at in range(-12, 0)
    ]
    buys = [s for s in found if s.action == Action.BUY]
    assert buys, "expected a BUY somewhere in the rebound"
    first = buys[0]
    assert first.timeframes["short"].cross == Cross.GOLDEN
    assert first.score >= 2  # cross happened in oversold zone
    assert any("과매도" in r for r in first.reasons)


def test_dead_cross_after_peak_is_sell():
    # A perfectly linear rise keeps %K == %D up to float noise, which hides the
    # cross; add a small wobble so the top produces a clean dead cross.
    up = np.linspace(40, 100, 60) + 0.3 * np.sin(np.arange(60) * 1.3)
    down = np.linspace(100, 85, 12)
    df = _frame(np.concatenate([up, down[1:]]))
    sells = [
        s for at in range(-16, 0)
        if (s := evaluate("T", df, PRESETS, at=at)).action == Action.SELL
    ]
    assert sells
    assert sells[0].timeframes["short"].cross == Cross.DEAD
    assert any("과매수" in r for r in sells[0].reasons)


def test_mid_confirmation_can_block_buy():
    df = _v_shape()
    unblocked = [
        evaluate("T", df, PRESETS, at=at) for at in range(-12, 0)
    ]
    buy_idx = next(i for i, s in enumerate(unblocked) if s.action == Action.BUY)
    at = -12 + buy_idx
    sig = evaluate("T", df, PRESETS, at=at, require_mid_confirmation=True)
    mid_bullish = sig.timeframes["mid"].bullish
    assert (sig.action == Action.BUY) == mid_bullish


def test_hold_when_no_short_cross():
    df = _frame(np.linspace(50, 100, 80))  # steady rise, K stays above D
    sig = evaluate("T", df, PRESETS)
    assert sig.action == Action.HOLD
    assert sig.score == 0
