from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from cninfo_monitor.cninfo import SUPPORTED_REPORT_TYPES


SUPPORTED_MARKETS = frozenset({"mainland", "hong_kong"})


@dataclass(frozen=True)
class MonitorConfig:
    markets: frozenset[str]
    report_types: frozenset[str]
    state_path: Path
    cache_path: Path
    request_timeout: float
    initial_lookback_hours: int
    overlap_seconds: int
    heartbeat_hour: int
    minimum_market_cap_yi_by_market: Mapping[str, Decimal]
    bootstrap_silently: bool
    notify_when_no_updates: bool


def load_monitor_config(path: str | Path) -> MonitorConfig:
    config_path = Path(path).resolve()
    with config_path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}
    monitor = raw.get("monitor", {})
    markets = frozenset(
        str(value).strip()
        for value in monitor.get("markets", ["mainland"])
        if str(value).strip()
    )
    unknown_markets = markets - SUPPORTED_MARKETS
    if unknown_markets:
        raise ValueError(f"unsupported markets: {', '.join(sorted(unknown_markets))}")
    if not markets:
        raise ValueError("monitor.markets cannot be empty")
    report_types = frozenset(
        str(value).strip()
        for value in monitor.get("report_types", ["interim"])
        if str(value).strip()
    )
    unknown = report_types - SUPPORTED_REPORT_TYPES
    if unknown:
        raise ValueError(f"unsupported report types: {', '.join(sorted(unknown))}")
    if not report_types:
        raise ValueError("monitor.report_types cannot be empty")

    raw_minimum_market_cap = monitor.get(
        "minimum_market_cap_yi",
        {"mainland": 100, "hong_kong": 500},
    )
    if isinstance(raw_minimum_market_cap, Mapping):
        unknown_threshold_markets = set(raw_minimum_market_cap) - SUPPORTED_MARKETS
        if unknown_threshold_markets:
            raise ValueError(
                "unsupported minimum_market_cap_yi markets: "
                + ", ".join(sorted(unknown_threshold_markets))
            )
        raw_thresholds = {
            "mainland": raw_minimum_market_cap.get("mainland", 100),
            "hong_kong": raw_minimum_market_cap.get("hong_kong", 500),
        }
    else:
        raw_thresholds = dict.fromkeys(SUPPORTED_MARKETS, raw_minimum_market_cap)
    try:
        minimum_market_cap_yi_by_market = {
            market: Decimal(str(value))
            for market, value in raw_thresholds.items()
        }
    except InvalidOperation as exc:
        raise ValueError("monitor.minimum_market_cap_yi must be a number") from exc
    if any(value < 0 for value in minimum_market_cap_yi_by_market.values()):
        raise ValueError("monitor.minimum_market_cap_yi cannot be negative")

    state_path = Path(str(monitor.get("state_path", "../data/state.json")))
    if not state_path.is_absolute():
        state_path = config_path.parent / state_path
    cache_path = Path(str(monitor.get("cache_path", "../data/announcements.db")))
    if not cache_path.is_absolute():
        cache_path = config_path.parent / cache_path
    initial_lookback_hours = int(monitor.get("initial_lookback_hours", 48))
    overlap_seconds = int(monitor.get("overlap_seconds", 300))
    heartbeat_hour = int(monitor.get("heartbeat_hour", 21))
    if initial_lookback_hours <= 0:
        raise ValueError("monitor.initial_lookback_hours must be positive")
    if overlap_seconds < 0:
        raise ValueError("monitor.overlap_seconds cannot be negative")
    if not 0 <= heartbeat_hour <= 23:
        raise ValueError("monitor.heartbeat_hour must be between 0 and 23")

    return MonitorConfig(
        markets=markets,
        report_types=report_types,
        state_path=state_path.resolve(),
        cache_path=cache_path.resolve(),
        request_timeout=float(monitor.get("request_timeout", 60)),
        initial_lookback_hours=initial_lookback_hours,
        overlap_seconds=overlap_seconds,
        heartbeat_hour=heartbeat_hour,
        minimum_market_cap_yi_by_market=minimum_market_cap_yi_by_market,
        bootstrap_silently=bool(monitor.get("bootstrap_silently", True)),
        notify_when_no_updates=bool(monitor.get("notify_when_no_updates", True)),
    )
