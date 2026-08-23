from __future__ import annotations

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
    minimum_market_cap_yi: Decimal
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

    try:
        minimum_market_cap_yi = Decimal(
            str(monitor.get("minimum_market_cap_yi", 100))
        )
    except InvalidOperation as exc:
        raise ValueError("monitor.minimum_market_cap_yi must be a number") from exc
    if minimum_market_cap_yi < 0:
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
        minimum_market_cap_yi=minimum_market_cap_yi,
        bootstrap_silently=bool(monitor.get("bootstrap_silently", True)),
        notify_when_no_updates=bool(monitor.get("notify_when_no_updates", True)),
    )
