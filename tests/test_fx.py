import pandas as pd

from stock_signal_monitor.fx import align_rates, is_krw_ticker, latest_rate, to_krw
from stock_signal_monitor.indicators import StochParams
from stock_signal_monitor.notifier import format_price, format_report
from stock_signal_monitor.report import build_ticker
from stock_signal_monitor.signals import Action, Signal


def test_is_krw_ticker():
    assert is_krw_ticker("005930.KS") and is_krw_ticker("035720.kq")
    assert not is_krw_ticker("SOXL") and not is_krw_ticker("BRK-B")


def test_align_rates_forward_fills_fx_holidays():
    rates = pd.Series([1300.0, 1310.0], index=pd.to_datetime(["2026-01-05", "2026-01-07"]))
    idx = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
    out = align_rates(rates, idx)
    assert out.tolist() == [1300.0, 1300.0, 1310.0, 1310.0]
    assert align_rates(None, idx) is None
    assert latest_rate(rates) == 1310.0 and latest_rate(None) is None


def test_format_price_variants():
    assert format_price("SOXL", 93.67, 1341.0) == "$93.67 (약 125,611원)"
    assert format_price("SOXL", 93.67, None) == "$93.67"
    assert format_price("005930.KS", 75300, 1341.0) == "75,300원"
    assert to_krw(1.0, None) is None


def test_report_footer_shows_rate():
    sig = Signal("NVDA", pd.Timestamp("2026-10-16"), 214.13, Action.BUY, 1, ["x"], {})
    text = format_report([sig], usdkrw=1341.0)
    assert "$214.13 (약 287,148원)" in text and "환율 1,341원/$" in text


def _frame(n=120):
    closes = pd.Series(range(100, 100 + n), dtype=float)
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"High": closes.values + 1, "Low": closes.values - 1, "Close": closes.values}, index=idx)


def test_build_ticker_adds_krw_for_usd_only():
    df = _frame()
    rates = pd.Series(1300.0, index=df.index)
    presets = {"short": StochParams(5, 3, 3)}
    usd = build_ticker("NVDA", df, presets, bars=10, signal_lookback=5, usdkrw=rates)
    assert usd["currency"] == "USD"
    assert usd["close_krw"][-1] == round(df.Close.iloc[-1] * 1300)
    assert usd["latest"]["close_krw"] == round(df.Close.iloc[-1] * 1300)
    krw = build_ticker("005930.KS", df, presets, bars=10, signal_lookback=5, usdkrw=rates)
    assert krw["currency"] == "KRW" and krw["close_krw"] is None and krw["latest"]["close_krw"] is None
    none = build_ticker("NVDA", df, presets, bars=10, signal_lookback=5, usdkrw=None)
    assert none["close_krw"] is None
