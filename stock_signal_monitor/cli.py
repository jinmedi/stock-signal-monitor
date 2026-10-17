"""Command-line entry point.

    python -m stock_signal_monitor              # console only, latest bar
    python -m stock_signal_monitor --telegram   # also push to Telegram
    python -m stock_signal_monitor --lookback 10 --all   # review last 10 bars
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from .data import fetch_history
from .indicators import StochParams
from .notifier import ConsoleNotifier, TelegramNotifier, format_report
from .signals import Action, Signal, evaluate
from .state import State


def load_config(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def presets_from_config(cfg: dict) -> dict[str, StochParams]:
    return {name: StochParams(**vals) for name, vals in cfg["stochastic"].items()}


def run(cfg: dict, *, lookback: int, include_hold: bool, state: State | None) -> list[Signal]:
    presets = presets_from_config(cfg)
    th = cfg.get("thresholds", {})
    alerts = cfg.get("alerts", {})
    out: list[Signal] = []
    min_bars = max(p.min_bars for p in presets.values()) + lookback + 1

    for ticker in cfg["tickers"]:
        try:
            df = fetch_history(ticker, cfg.get("history_period", "1y"))
        except Exception as exc:  # network / bad ticker: report and continue
            print(f"[warn] {ticker}: {exc}", file=sys.stderr)
            continue
        if len(df) < min_bars:
            print(f"[warn] {ticker}: only {len(df)} bars, need {min_bars}", file=sys.stderr)
            continue

        for at in range(-lookback, 0):
            sig = evaluate(
                ticker,
                df,
                presets,
                oversold=th.get("oversold", 20),
                overbought=th.get("overbought", 80),
                require_mid_confirmation=alerts.get("require_mid_confirmation", False),
                at=at,
            )
            if state is not None and at == -1:
                if not state.is_new(ticker, sig.date):
                    continue
                state.mark(ticker, sig.date)
            interesting = sig.action != Action.HOLD or (
                alerts.get("notify_mid_long_crosses", True) and sig.crosses
            )
            if interesting or include_hold:
                out.append(sig)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Stochastic Slow cross monitor")
    p.add_argument("--config", type=Path, default=Path("config.toml"))
    p.add_argument("--state", type=Path, default=Path("state/last_signals.json"))
    p.add_argument("--no-state", action="store_true", help="ignore/skip the dedup state file")
    p.add_argument("--lookback", type=int, default=1, help="evaluate the last N bars")
    p.add_argument("--all", action="store_true", help="include HOLD rows (no signal)")
    p.add_argument("--telegram", action="store_true", help="send the report to Telegram")
    p.add_argument("--quiet-when-empty", action="store_true", help="send nothing when no signals")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    state = None if (args.no_state or args.lookback > 1) else State(args.state)
    signals = run(cfg, lookback=args.lookback, include_hold=args.all, state=state)

    report = format_report(signals)
    ConsoleNotifier().send(report)
    if args.telegram and (signals or not args.quiet_when_empty):
        TelegramNotifier().send(report)
    if state is not None:
        state.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
