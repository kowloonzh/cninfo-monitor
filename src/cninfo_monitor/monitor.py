from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from cninfo_monitor.models import Report


class JsonStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def known_keys(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"cannot read monitor state {self.path}: {exc}") from exc
        keys = raw.get("known_company_reports", [])
        if not isinstance(keys, list):
            raise RuntimeError(f"invalid monitor state {self.path}")
        return {str(key) for key in keys}

    def add_reports(self, reports: Iterable[Report]) -> None:
        keys = self.known_keys()
        keys.update(report.company_report_key for report in reports)
        self._write(keys)

    def _write(self, keys: set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": 1, "known_company_reports": sorted(keys)},
            ensure_ascii=False,
            indent=2,
        )
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            dir=self.path.parent,
            text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.write("\n")
            os.replace(temporary_path, self.path)
        except BaseException:
            Path(temporary_path).unlink(missing_ok=True)
            raise


@dataclass(frozen=True)
class RunResult:
    new_reports: tuple[Report, ...]
    baselined: int = 0
    notification_sent: bool | None = None


def format_digest(reports: Iterable[Report]) -> str:
    rows = list(reports)
    lines = [f"巨潮财报监控：新发布 {len(rows)} 家"]
    for report in rows:
        lines.extend(
            [
                "",
                f"{report.sec_name}（{report.sec_code}）",
                report.title,
                f"披露日期：{report.disclosure_date}",
                report.pdf_url,
            ]
        )
    return "\n".join(lines)


def run_monitor(
    reports: Iterable[Report],
    state: JsonStateStore,
    *,
    sender: Callable[[str], bool],
    bootstrap_silently: bool,
) -> RunResult:
    current_reports = tuple(reports)
    if not state.exists and bootstrap_silently:
        state.add_reports(current_reports)
        return RunResult(new_reports=(), baselined=len(current_reports))

    known = state.known_keys()
    new_reports = tuple(
        report for report in current_reports if report.company_report_key not in known
    )
    if not new_reports:
        if not state.exists:
            state.add_reports(())
        return RunResult(new_reports=())

    sent = sender(format_digest(new_reports))
    if sent:
        state.add_reports(new_reports)
    return RunResult(new_reports=new_reports, notification_sent=sent)
