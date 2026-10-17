"""Hit-rate tracking for short-timeframe crosses, split by news sentiment.

``state/track.csv`` gets one row per (date, ticker) cross with the sentiment
that day and the close; ``fill_returns`` adds the 5- and 10-trading-day returns
once enough bars exist, and ``hit_rate`` aggregates them for the dashboard.
Everything is a pure function over row dicts so it can be unit-tested and
back-filled from history.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from pathlib import Path

import pandas as pd

from .signals import Action, Cross, Signal

COLUMNS = ["date", "ticker", "cross", "action", "score", "sentiment", "close", "ret_5d", "ret_10d"]
HORIZONS = (5, 10)
MIN_SETTLED = 20


def load_rows(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return [{k: (v or "") for k, v in row.items() if k in COLUMNS} for row in csv.DictReader(f)]


def save_rows(path: Path | None, rows: Iterable[dict]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["date"], r["ticker"])):
            w.writerow({k: r.get(k, "") for k in COLUMNS})


def row(
    day: str, ticker: str, cross: str, action: str, score: int, sentiment: int | None, close: float
) -> dict | None:
    """A track row, or ``None`` when there was no short cross (nothing to track)."""
    if cross not in (Cross.GOLDEN.value, Cross.DEAD.value):
        return None
    return {
        "date": day,
        "ticker": ticker,
        "cross": cross,
        "action": action,
        "score": str(score),
        "sentiment": "" if sentiment is None else str(int(sentiment)),
        "close": f"{float(close):.4f}",
        "ret_5d": "",
        "ret_10d": "",
    }


def row_from_signal(sig: Signal, sentiment: int | None) -> dict | None:
    """A track row for a signal whose short timeframe crossed today, else ``None``."""
    short = sig.timeframes.get("short")
    cross = short.cross if short is not None else Cross.NONE
    if cross == Cross.NONE:
        # Signals built without timeframes (tests, post-processed copies) fall back to the action.
        if sig.action == Action.BUY:
            cross = Cross.GOLDEN
        elif sig.action == Action.SELL:
            cross = Cross.DEAD
        else:
            return None
    return row(
        pd.Timestamp(sig.date).strftime("%Y-%m-%d"), sig.ticker, cross.value, sig.action.value, sig.score, sentiment, sig.close
    )


def record(rows: list[dict], new_rows: Iterable[dict | None]) -> list[dict]:
    """Append rows whose (date, ticker) is not present yet; returns the merged list."""
    seen = {(r["date"], r["ticker"]) for r in rows}
    out = list(rows)
    for r in new_rows:
        if r is None:
            continue
        key = (r["date"], r["ticker"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def fill_returns(rows: list[dict], closes: Mapping[str, pd.Series]) -> list[dict]:
    """Fill ``ret_5d`` / ``ret_10d`` (percent) where enough bars after the signal exist."""
    normalised: dict[str, pd.Series] = {}
    for ticker, s in closes.items():
        s = s.dropna()
        idx = pd.to_datetime(s.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        normalised[ticker] = pd.Series(s.to_numpy(dtype=float), index=idx.normalize())

    out = []
    for r in rows:
        r = dict(r)
        s = normalised.get(r["ticker"])
        if s is not None:
            when = pd.Timestamp(r["date"])
            pos = s.index.searchsorted(when)
            if pos < len(s) and s.index[pos] == when:
                base = float(s.iloc[pos])
                for h in HORIZONS:
                    col = f"ret_{h}d"
                    if not r.get(col) and pos + h < len(s) and base:
                        r[col] = f"{(float(s.iloc[pos + h]) / base - 1) * 100:.2f}"
        out.append(r)
    return out


def _is_hit(cross: str, ret: float) -> bool:
    return ret > 0 if cross == Cross.GOLDEN.value else ret < 0


def _bucket(sentiment: str) -> str:
    if sentiment == "":
        return "none"
    n = int(sentiment)
    return f"{n:+d}"


def hit_rate(rows: Iterable[dict], *, min_settled: int = MIN_SETTLED) -> dict:
    """Aggregate hit rates by sentiment bucket and cross type.

    ``ready`` is False while fewer than ``min_settled`` rows have a 5-day return;
    the dashboard then shows "집계 중".
    """
    rows = list(rows)
    groups: dict[tuple[str, str], dict] = {}
    settled = 0
    for r in rows:
        if r.get("ret_5d"):
            settled += 1
        key = (_bucket(r.get("sentiment", "")), r["cross"])
        g = groups.setdefault(key, {h: [] for h in HORIZONS})
        for h in HORIZONS:
            v = r.get(f"ret_{h}d")
            if v:
                g[h].append(float(v))

    def summarise(vals: list[float], cross: str) -> dict:
        if not vals:
            return {"n": 0, "hit": None, "avg": None}
        hits = sum(_is_hit(cross, v) for v in vals)
        return {"n": len(vals), "hit": round(100 * hits / len(vals), 1), "avg": round(sum(vals) / len(vals), 2)}

    order = {"-2": 0, "-1": 1, "+0": 2, "+1": 3, "+2": 4, "none": 5}
    table = [
        {
            "sentiment": bucket,
            "cross": cross,
            **{f"h{h}": summarise(g[h], cross) for h in HORIZONS},
        }
        for (bucket, cross), g in sorted(groups.items(), key=lambda kv: (order.get(kv[0][0], 9), kv[0][1]))
    ]
    return {
        "count": len(rows),
        "settled": settled,
        "min_settled": min_settled,
        "ready": settled >= min_settled,
        "horizons": list(HORIZONS),
        "rows": table,
    }


def update(
    path: Path | None,
    *,
    new_rows: Iterable[dict | None] = (),
    closes: Mapping[str, pd.Series] | None = None,
) -> list[dict]:
    """Load → append today's crosses → fill returns → save. Returns the rows."""
    rows = record(load_rows(path), new_rows)
    if closes:
        rows = fill_returns(rows, closes)
    save_rows(path, rows)
    return rows
