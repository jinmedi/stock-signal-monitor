import pandas as pd

from stock_signal_monitor.indicators import StochParams
from stock_signal_monitor.notifier import _chunk, format_report, format_signal
from stock_signal_monitor.signals import Action, Cross, Signal, TimeframeState
from stock_signal_monitor.state import State


def _sig():
    tf = TimeframeState("short", StochParams(5, 3, 3), 25.0, 20.0, 15.0, 18.0, Cross.GOLDEN)
    mid = TimeframeState("mid", StochParams(10, 6, 6), 40.0, 35.0, 38.0, 36.0, Cross.NONE)
    return Signal(
        ticker="NVDA",
        date=pd.Timestamp("2026-10-15"),
        close=182.5,
        action=Action.BUY,
        score=2,
        reasons=["단기 5-3-3 골든크로스", "과매도권에서 발생"],
        timeframes={"short": tf, "mid": mid},
    )


def test_format_signal_contains_key_fields():
    text = format_signal(_sig())
    assert "NVDA" in text and "BUY" in text and "2026-10-15" in text
    assert "★★☆☆" in text
    assert "골든크로스" in text


def test_format_report_empty():
    assert "알릴 신호가 없습니다" in format_report([])


def test_chunk_splits_on_paragraphs():
    text = "\n\n".join(["x" * 100] * 50)
    chunks = _chunk(text, 1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)
    assert "\n\n".join(chunks) == text


def test_state_roundtrip(tmp_path):
    path = tmp_path / "s.json"
    st = State(path)
    d = pd.Timestamp("2026-10-15")
    assert st.is_new("NVDA", d)
    st.mark("NVDA", d)
    st.save()
    again = State(path)
    assert not again.is_new("NVDA", d)
    assert again.is_new("NVDA", pd.Timestamp("2026-10-16"))
