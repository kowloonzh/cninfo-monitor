from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from cninfo_monitor.cninfo import REPORT_TYPE_DEFINITIONS


@dataclass(frozen=True)
class MonitorConfig:
    report_types: frozenset[str]
    state_path: Path
    request_timeout: float
    bootstrap_silently: bool
    notify_when_no_updates: bool


def load_monitor_config(path: str | Path) -> MonitorConfig:
    config_path = Path(path).resolve()
    with config_path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}
    monitor = raw.get("monitor", {})
    report_types = frozenset(
        str(value).strip()
        for value in monitor.get("report_types", ["interim"])
        if str(value).strip()
    )
    unknown = report_types - REPORT_TYPE_DEFINITIONS.keys()
    if unknown:
        raise ValueError(f"unsupported report types: {', '.join(sorted(unknown))}")
    if not report_types:
        raise ValueError("monitor.report_types cannot be empty")

    state_path = Path(str(monitor.get("state_path", "../data/state.json")))
    if not state_path.is_absolute():
        state_path = config_path.parent / state_path

    return MonitorConfig(
        report_types=report_types,
        state_path=state_path.resolve(),
        request_timeout=float(monitor.get("request_timeout", 60)),
        bootstrap_silently=bool(monitor.get("bootstrap_silently", True)),
        notify_when_no_updates=bool(monitor.get("notify_when_no_updates", True)),
    )
