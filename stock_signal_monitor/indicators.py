"""Technical indicators. Stochastic Slow uses simple moving averages,
matching the Korean HTS convention (단기 5-3-3 / 중기 10-6-6 / 장기 20-12-12)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class StochParams:
    n: int  # lookback for highest high / lowest low
    m: int  # SMA length for Slow %K
    t: int  # SMA length for Slow %D

    @property
    def label(self) -> str:
        return f"{self.n}-{self.m}-{self.t}"

    @property
    def min_bars(self) -> int:
        return self.n + self.m + self.t - 2


def stoch_slow(df: pd.DataFrame, params: StochParams) -> pd.DataFrame:
    """Return a DataFrame with columns ``k`` (Slow %K) and ``d`` (Slow %D).

    ``df`` must have ``High``, ``Low`` and ``Close`` columns.
    """
    lo = df["Low"].rolling(params.n).min()
    hi = df["High"].rolling(params.n).max()
    rng = hi - lo
    fast_k = 100 * (df["Close"] - lo) / rng.where(rng != 0)
    slow_k = fast_k.rolling(params.m).mean()
    slow_d = slow_k.rolling(params.t).mean()
    return pd.DataFrame({"k": slow_k, "d": slow_d}, index=df.index)
