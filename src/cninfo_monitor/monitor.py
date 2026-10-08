from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from cninfo_monitor.models import Report


DIGEST_PAGE_MAX_BYTES = 1900


class JsonStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def known_keys(self) -> set[str]:
        raw = self._read()
        if raw is None:
            return set()
        keys = raw.get("known_company_reports", [])
        if not isinstance(keys, list):
            raise RuntimeError(f"invalid monitor state {self.path}")
        return {str(key) for key in keys}

    def initialized_markets(self) -> set[str]:
        raw = self._read()
        if raw is None:
            return set()
        markets = raw.get("initialized_markets")
        if markets is None:
            return {"mainland"}
        if not isinstance(markets, list):
            raise RuntimeError(f"invalid monitor state {self.path}")
        return {str(market) for market in markets}

    def _read(self) -> dict[str, object] | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"cannot read monitor state {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"invalid monitor state {self.path}")
        return raw

    def add_reports(
        self,
        reports: Iterable[Report],
        *,
        initialized_markets: Iterable[str] = (),
    ) -> None:
        keys = self.known_keys()
        keys.update(report.company_report_key for report in reports)
        markets = self.initialized_markets()
        markets.update(initialized_markets)
        self._write(keys, markets)

    def _write(self, keys: set[str], markets: set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "version": 2,
                "initialized_markets": sorted(markets),
                "known_company_reports": sorted(keys),
            },
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
    lines = [
        f"巨潮财报监控：新披露 {len(rows)} 份"
        if any(row.market == "us" for row in rows)
        else f"巨潮财报监控：新发布 {len(rows)} 家"
    ]
    for report in rows:
        lines.extend(["", _format_report(report)])
    return "\n".join(lines)


def format_digest_pages(
    reports: Iterable[Report],
    *,
    max_bytes: int = DIGEST_PAGE_MAX_BYTES,
) -> tuple[str, ...]:
    rows = list(reports)
    if not rows:
        return (format_digest(rows),)

    total = len(rows)
    disclosures = any(row.market == "us" for row in rows)
    longest_header = _format_digest_page_header(total, total, total, total, disclosures)
    body_limit = max_bytes - len(f"{longest_header}\n\n".encode("utf-8"))
    if body_limit <= 0:
        raise ValueError("digest page byte limit is too small for its header")

    grouped_blocks: list[list[str]] = []
    current: list[str] = []
    for report in rows:
        block = _format_report(report)
        candidate = "\n\n".join([*current, block])
        if len(candidate.encode("utf-8")) <= body_limit:
            current.append(block)
            continue
        if not current:
            raise ValueError(
                f"report {report.sec_code} is too large for one digest page"
            )
        grouped_blocks.append(current)
        current = [block]
    grouped_blocks.append(current)

    page_count = len(grouped_blocks)
    return tuple(
        "\n\n".join(
            [
                _format_digest_page_header(
                    total,
                    page_number,
                    page_count,
                    len(blocks),
                    disclosures,
                ),
                *blocks,
            ]
        )
        for page_number, blocks in enumerate(grouped_blocks, start=1)
    )


def _format_digest_page_header(
    total: int,
    page_number: int,
    page_count: int,
    page_size: int,
    disclosures: bool = False,
) -> str:
    if disclosures:
        return (
            f"巨潮财报监控：新披露 {total} 份"
            f"（第 {page_number}/{page_count} 条，本条 {page_size} 份）"
        )
    return (
        f"巨潮财报监控：新发布 {total} 家"
        f"（第 {page_number}/{page_count} 条，本条 {page_size} 家）"
    )


def _format_report(report: Report) -> str:
    market_name = {"hong_kong": "港股", "us": "美股"}.get(report.market, "沪深京")
    lines = [
        f"{report.sec_name}（{report.sec_code}）",
        f"市场：{market_name}",
    ]
    if report.market != "us":
        lines.append(f"总市值：{report.total_market_cap or '暂无数据'}")
    if report.index_names:
        lines.append(f"指数：{'、'.join(report.index_names)}")
    lines.extend(
        [
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
    markets: Iterable[str] = ("mainland",),
) -> RunResult:
    current_reports = tuple(reports)
    configured_markets = set(markets)
    state_existed = state.exists
    if not state_existed and bootstrap_silently:
        state.add_reports(current_reports, initialized_markets=configured_markets)
        return RunResult(new_reports=(), baselined=len(current_reports))

    if not state_existed:
        state.add_reports((), initialized_markets=configured_markets)

    uninitialized_markets = configured_markets - state.initialized_markets()
    baselined_reports = tuple(
        report for report in current_reports if report.market in uninitialized_markets
    )
    if uninitialized_markets:
        state.add_reports(
            baselined_reports,
            initialized_markets=uninitialized_markets,
        )

    known = state.known_keys()
    new_reports = tuple(
        report for report in current_reports if report.company_report_key not in known
    )
    if not new_reports:
        return RunResult(new_reports=(), baselined=len(baselined_reports))

    sent = all(sender(page) for page in format_digest_pages(new_reports))
    if sent:
        state.add_reports(new_reports)
    return RunResult(
        new_reports=new_reports,
        baselined=len(baselined_reports),
        notification_sent=sent,
    )
