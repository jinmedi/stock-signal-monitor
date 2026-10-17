import pandas as pd

from stock_signal_monitor.indicators import StochParams
from stock_signal_monitor.signals import Action, Cross, Signal, TimeframeState
from stock_signal_monitor.tracking import (
    COLUMNS,
    fill_returns,
    hit_rate,
    load_rows,
    record,
    row_from_signal,
    save_rows,
    update,
)


def _sig(ticker="NVDA", day="2026-01-05", action=Action.BUY, cross=Cross.GOLDEN, close=100.0):
    tf = TimeframeState("short", StochParams(5, 3, 3), 25.0, 20.0, 15.0, 18.0, cross)
    return Signal(ticker, pd.Timestamp(day), close, action, 2, [], {"short": tf})


def _closes(start="2026-01-05", n=15, step=1.0):
    idx = pd.bdate_range(start, periods=n)
    return pd.Series([100.0 + i * step for i in range(n)], index=idx)


def test_row_from_signal_uses_short_cross_and_sentiment():
    row = row_from_signal(_sig(), 1)
    assert row["date"] == "2026-01-05" and row["cross"] == "golden" and row["sentiment"] == "1"
    assert row["close"] == "100.0000" and row["ret_5d"] == ""
    assert row_from_signal(_sig(action=Action.HOLD, cross=Cross.NONE), None) is None
    # held-by-filter BUY keeps its cross but records the HOLD action
    held = row_from_signal(_sig(action=Action.HOLD, cross=Cross.GOLDEN), None)
    assert held["cross"] == "golden" and held["action"] == "HOLD" and held["sentiment"] == ""


def test_record_is_idempotent_per_date_ticker():
    rows = record([], [row_from_signal(_sig(), 0), None])
    rows = record(rows, [row_from_signal(_sig(), 0), row_from_signal(_sig("TSLA"), None)])
    assert [(r["date"], r["ticker"]) for r in rows] == [("2026-01-05", "NVDA"), ("2026-01-05", "TSLA")]


def test_fill_returns_needs_enough_bars_and_keeps_existing():
    rows = [row_from_signal(_sig(), 0)]
    short = _closes(n=6)  # only 5 bars after → ret_5d yes, ret_10d no
    out = fill_returns(rows, {"NVDA": short})
    assert out[0]["ret_5d"] == "5.00" and out[0]["ret_10d"] == ""
    out2 = fill_returns(out, {"NVDA": _closes(n=15, step=2.0)})
    assert out2[0]["ret_5d"] == "5.00"  # existing value kept
    assert out2[0]["ret_10d"] == "20.00"
    # unknown ticker / date not in series → untouched
    assert fill_returns(rows, {"TSLA": short})[0]["ret_5d"] == ""
    assert fill_returns([row_from_signal(_sig(day="2026-01-04"), 0)], {"NVDA": short})[0]["ret_5d"] == ""


def test_fill_returns_handles_tz_aware_index():
    s = _closes(n=8)
    s.index = s.index.tz_localize("America/New_York")
    out = fill_returns([row_from_signal(_sig(), 0)], {"NVDA": s})
    assert out[0]["ret_5d"] == "5.00"


def test_hit_rate_buckets_and_readiness():
    rows = []
    for i in range(12):
        rows.append({"date": f"2026-01-{i + 1:02d}", "ticker": "A", "cross": "golden", "action": "BUY", "score": "1",
                     "sentiment": "1", "close": "1", "ret_5d": "2.0" if i % 3 else "-1.0", "ret_10d": ""})
    for i in range(10):
        rows.append({"date": f"2026-02-{i + 1:02d}", "ticker": "B", "cross": "dead", "action": "SELL", "score": "1",
                     "sentiment": "", "close": "1", "ret_5d": "-3.0", "ret_10d": "1.0"})
    rows.append({"date": "2026-03-01", "ticker": "C", "cross": "golden", "action": "BUY", "score": "1",
                 "sentiment": "-2", "close": "1", "ret_5d": "", "ret_10d": ""})
    hr = hit_rate(rows, min_settled=20)
    assert hr["count"] == 23 and hr["settled"] == 22 and hr["ready"]
    by = {(r["sentiment"], r["cross"]): r for r in hr["rows"]}
    assert [r["sentiment"] for r in hr["rows"]] == ["-2", "+1", "none"]  # negative → positive → none
    golden = by[("+1", "golden")]
    assert golden["h5"] == {"n": 12, "hit": round(100 * 8 / 12, 1), "avg": 1.0}
    assert golden["h10"] == {"n": 0, "hit": None, "avg": None}
    dead = by[("none", "dead")]
    assert dead["h5"]["hit"] == 100.0 and dead["h10"]["hit"] == 0.0
    assert not hit_rate(rows[:5])["ready"]
    assert hit_rate([])["count"] == 0 and hit_rate([])["rows"] == []


def test_csv_roundtrip_and_update(tmp_path):
    path = tmp_path / "track.csv"
    assert load_rows(path) == []
    rows = update(path, new_rows=[row_from_signal(_sig(), 1)], closes={"NVDA": _closes(n=3)})
    assert rows[0]["ret_5d"] == ""
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(COLUMNS)
    rows2 = update(path, new_rows=[row_from_signal(_sig(), 1)], closes={"NVDA": _closes(n=12)})
    assert len(rows2) == 1 and rows2[0]["ret_5d"] == "5.00" and rows2[0]["ret_10d"] == "10.00"
    assert load_rows(path) == rows2
    save_rows(None, rows2)  # no path → no-op
