"""Cross detection and buy/sell signal evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from .indicators import StochParams, stoch_slow


class Cross(str, Enum):
    GOLDEN = "golden"  # %K crosses above %D
    DEAD = "dead"  # %K crosses below %D
    NONE = "none"


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class TimeframeState:
    name: str
    params: StochParams
    k: float
    d: float
    prev_k: float
    prev_d: float
    cross: Cross

    @property
    def bullish(self) -> bool:
        return self.k > self.d


@dataclass
class Signal:
    ticker: str
    date: pd.Timestamp
    close: float
    action: Action
    score: int
    reasons: list[str] = field(default_factory=list)
    timeframes: dict[str, TimeframeState] = field(default_factory=dict)

    @property
    def crosses(self) -> dict[str, Cross]:
        return {name: tf.cross for name, tf in self.timeframes.items() if tf.cross != Cross.NONE}


def detect_cross(prev_k: float, prev_d: float, k: float, d: float) -> Cross:
    if any(pd.isna(v) for v in (prev_k, prev_d, k, d)):
        return Cross.NONE
    if prev_k <= prev_d and k > d:
        return Cross.GOLDEN
    if prev_k >= prev_d and k < d:
        return Cross.DEAD
    return Cross.NONE


def evaluate(
    ticker: str,
    df: pd.DataFrame,
    presets: dict[str, StochParams],
    *,
    oversold: float = 20,
    overbought: float = 80,
    require_mid_confirmation: bool = False,
    at: int = -1,
) -> Signal:
    """Evaluate the bar at integer position ``at`` (default: latest bar).

    Buy/sell decisions are driven by the *short* timeframe cross; the mid and
    long timeframes and the oversold/overbought zone add to the score.
    """
    timeframes: dict[str, TimeframeState] = {}
    for name, params in presets.items():
        st = stoch_slow(df, params)
        cur = st.iloc[at]
        prev = st.iloc[at - 1]
        timeframes[name] = TimeframeState(
            name=name,
            params=params,
            k=float(cur["k"]),
            d=float(cur["d"]),
            prev_k=float(prev["k"]),
            prev_d=float(prev["d"]),
            cross=detect_cross(prev["k"], prev["d"], cur["k"], cur["d"]),
        )

    short = timeframes["short"]
    mid = timeframes.get("mid")
    long_ = timeframes.get("long")

    action = Action.HOLD
    score = 0
    reasons: list[str] = []

    if short.cross == Cross.GOLDEN:
        action = Action.BUY
        score = 1
        reasons.append(f"단기 {short.params.label} 골든크로스 (%K {short.k:.1f} > %D {short.d:.1f})")
        if min(short.prev_k, short.k) <= oversold:
            score += 1
            reasons.append(f"과매도권({oversold} 이하)에서 발생")
        if mid is not None and mid.bullish:
            score += 1
            reasons.append(f"중기 {mid.params.label} 상승 배열")
        elif mid is not None and require_mid_confirmation:
            action = Action.HOLD
            reasons.append("중기 하락 배열이라 매수 보류")
        if long_ is not None and long_.bullish:
            score += 1
            reasons.append(f"장기 {long_.params.label} 상승 배열")

    elif short.cross == Cross.DEAD:
        action = Action.SELL
        score = 1
        reasons.append(f"단기 {short.params.label} 데드크로스 (%K {short.k:.1f} < %D {short.d:.1f})")
        if max(short.prev_k, short.k) >= overbought:
            score += 1
            reasons.append(f"과매수권({overbought} 이상)에서 발생")
        if mid is not None and not mid.bullish:
            score += 1
            reasons.append(f"중기 {mid.params.label} 하락 배열")
        elif mid is not None and require_mid_confirmation:
            action = Action.HOLD
            reasons.append("중기 상승 배열이라 매도 보류")
        if long_ is not None and not long_.bullish:
            score += 1
            reasons.append(f"장기 {long_.params.label} 하락 배열")

    return Signal(
        ticker=ticker,
        date=df.index[at],
        close=float(df["Close"].iloc[at]),
        action=action,
        score=score,
        reasons=reasons,
        timeframes=timeframes,
    )
