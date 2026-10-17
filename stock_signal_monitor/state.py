"""Persisted state so the same bar is never alerted twice."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


class State:
    def __init__(self, path: Path):
        self.path = path
        self.last_bar: dict[str, str] = {}
        if path.exists():
            self.last_bar = json.loads(path.read_text(encoding="utf-8")).get("last_bar", {})

    def is_new(self, ticker: str, date: pd.Timestamp) -> bool:
        return self.last_bar.get(ticker) != _key(date)

    def mark(self, ticker: str, date: pd.Timestamp) -> None:
        self.last_bar[ticker] = _key(date)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_bar": dict(sorted(self.last_bar.items()))}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _key(date: pd.Timestamp) -> str:
    return pd.Timestamp(date).strftime("%Y-%m-%d")
