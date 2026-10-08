from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import httpx

from cninfo_monitor.cache import AnnouncementCache
from cninfo_monitor.cninfo import (
    CNINFO_TIMEZONE,
    REPORT_TYPE_DEFINITIONS,
    query_announcements_by_time,
    select_formal_reports,
)
from cninfo_monitor.config import MonitorConfig, load_monitor_config
from cninfo_monitor.indexes import (
    enrich_reports_with_index_memberships,
    load_index_memberships,
    refresh_official_index_cache,
)
from cninfo_monitor.models import Report
from cninfo_monitor.sec import fetch_us_announcements
from cninfo_monitor.monitor import JsonStateStore, format_digest, run_monitor
from cninfo_monitor.notifications import (
    load_workwechat_config,
    send_workwechat_text,
)
from cninfo_monitor.quotes import (
    enrich_reports_with_market_caps,
    filter_reports_by_minimum_market_cap,
)


DEFAULT_CONFIG_PATH = Path("config/config.yaml")
MARKET_CAP_RECHECK_DAYS = 7
CNINFO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.cninfo.com.cn",
    "Referer": "https://www.cninfo.com.cn/",
}


def fetch_reports(
    config: MonitorConfig,
    end_time: dt.datetime,
    cache: AnnouncementCache,
) -> list[Report]:
    if end_time.tzinfo is None:
        raise ValueError("end_time must be timezone-aware")
    end_time = end_time.astimezone(CNINFO_TIMEZONE).replace(microsecond=0)
    with httpx.Client(
        headers=CNINFO_HEADERS,
        timeout=config.request_timeout,
        follow_redirects=True,
    ) as client:
        scan_failures: list[str] = []
        for market in sorted(config.markets):
            fetched_through = cache.fetched_through(market)
            if fetched_through is None:
                start_time = end_time - dt.timedelta(
                    hours=config.initial_lookback_hours
                )
            else:
                start_time = fetched_through - dt.timedelta(
                    seconds=config.overlap_seconds
                )
            try:
                if market == "us":
                    rows = fetch_us_announcements(config, start_time, end_time)
                    cache.store_scans(
                        {market: rows}, scan_starts={market: start_time},
                        scanned_through=end_time,
                    )
                    continue
                snapshot_date = start_time.date()
                while snapshot_date <= end_time.date():
                    day_start = dt.datetime.combine(
                        snapshot_date,
                        dt.time(),
                        tzinfo=CNINFO_TIMEZONE,
                    )
                    day_end = min(
                        day_start + dt.timedelta(days=1, seconds=-1),
                        end_time,
                    )
                    known_ids = (
                        cache.announcement_ids_for_date(market, snapshot_date)
                        if cache.has_date_snapshot(market, snapshot_date)
                        else set()
                    )
                    rows = []
                    if market == "mainland":
                        categories = tuple(
                            category
                            for report_type, (category, _) in (
                                REPORT_TYPE_DEFINITIONS.items()
                            )
                            if report_type in config.report_types
                        )
                        query_partitions = (
                            (plate, category)
                            for plate in ("sz", "sh", "bj")
                            for category in categories
                        )
                    else:
                        query_partitions = (("", ""),)
                    for plate, category in query_partitions:
                        rows.extend(
                            query_announcements_by_time(
                                client,
                                market=market,
                                plate=plate,
                                category=category,
                                start_time=day_start,
                                end_time=day_end,
                                known_announcement_ids=known_ids,
                            )
                        )
                    cache.store_scans(
                        {market: rows},
                        scan_starts={market: start_time},
                        scanned_through=day_end,
                        snapshot_dates={market: [snapshot_date]},
                    )
                    snapshot_date += dt.timedelta(days=1)
            except Exception as exc:
                scan_failures.append(f"{market}: {exc}")

        if scan_failures:
            raise RuntimeError("; ".join(scan_failures))

        cache.expire_market_cap_pending(
            before=end_time - dt.timedelta(days=MARKET_CAP_RECHECK_DAYS)
        )
        reports: list[Report] = []
        us_reports: list[Report] = []
        for market in sorted(config.markets):
            announcements = cache.load_unprocessed(
                market,
                overlap_seconds=config.overlap_seconds,
            )
            announcements.extend(cache.load_market_cap_pending(market))
            if market == "us":
                for row in announcements:
                    payload = dict(row["report"])
                    payload["index_names"] = tuple(payload.get("index_names", ()))
                    us_reports.append(Report(**payload))
                continue
            reports.extend(
                select_formal_reports(
                    announcements,
                    config.report_types,
                    market=market,
                )
            )
        memberships = load_index_memberships()
        hstech_codes = {
            key.split(":", 1)[1]
            for key, names in memberships.items()
            if key.startswith("hong_kong:") and "恒生科技" in names
        }
        csi_300_500_codes = {
            key.split(":", 1)[1]
            for key, names in memberships.items()
            if key.startswith("mainland:")
            and {"沪深300", "中证500"}.intersection(names)
        }
        if "hong_kong" in config.markets and not hstech_codes:
            raise RuntimeError("恒生科技成份股缓存为空；请先运行 refresh-indexes 刷新指数缓存")
        if "mainland" in config.markets and not csi_300_500_codes:
            raise RuntimeError("沪深300/中证500成份股缓存为空；请先运行 refresh-indexes 刷新指数缓存")
        reports = [
            report for report in reports
            if (
                report.market != "hong_kong"
                or report.sec_code.zfill(5) in hstech_codes
            )
            and (
                report.market != "mainland"
                or report.sec_code.zfill(6) in csi_300_500_codes
            )
        ]
        enriched_reports = list(enrich_reports_with_market_caps(client, reports))
        reports = list(
            filter_reports_by_minimum_market_cap(
                enriched_reports,
                config.minimum_market_cap_yi_by_market,
            )
        )
        eligible_references = {
            (report.market, report.announcement_id) for report in reports
        }
        cache.defer_market_cap_reports(
            (
                (report.market, report.announcement_id)
                for report in enriched_reports
                if (report.market, report.announcement_id)
                not in eligible_references
            ),
            deferred_at=end_time,
        )
        reports = list(
            enrich_reports_with_index_memberships(
                reports,
                memberships,
            )
        )
        reports.extend(us_reports)
    return sorted(
        reports,
        key=lambda report: (report.disclosure_date, report.market, report.sec_code),
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "refresh-indexes":
            output_path = Path(args.output)
            snapshot = refresh_official_index_cache(output_path)
            print(
                f"指数缓存已刷新：{len(snapshot['sources'])} 个指数，"
                f"{len(snapshot['memberships'])} 只证券"
            )
            return 0

        if args.command == "test-notification":
            workwechat = load_workwechat_config(args.config)
            message = (
                args.message
                or "巨潮财报监控：企业微信通知测试成功"
            )
            if send_workwechat_text(message, workwechat):
                print("企业微信测试消息已发送")
                return 0
            print("企业微信测试消息发送失败，请检查配置和应用可见范围", file=sys.stderr)
            return 1

        config = load_monitor_config(args.config)
        end_time = _resolve_end_time(args)
        cache = AnnouncementCache(
            ":memory:" if args.dry_run else config.cache_path
        )
        reports = fetch_reports(config, end_time, cache)
        if args.dry_run:
            print(format_digest(reports))
            return 0

        workwechat = load_workwechat_config(args.config)
        result = run_monitor(
            reports,
            JsonStateStore(config.state_path),
            sender=lambda message: send_workwechat_text(message, workwechat),
            bootstrap_silently=(
                config.bootstrap_silently and not args.notify_existing
            ),
            markets=config.markets,
        )
        if result.baselined and not result.new_reports:
            cache.clear_market_cap_pending(_report_references(reports))
            cache.mark_processed(config.markets, end_time)
            print(f"已建立新市场基线：{result.baselined} 家，本次不推送")
            return 0
        if not result.new_reports:
            cache.clear_market_cap_pending(_report_references(reports))
            cache.mark_processed(config.markets, end_time)
            if (
                config.notify_when_no_updates
                and end_time.hour >= config.heartbeat_hour
                and not cache.has_daily_activity(end_time.date())
            ):
                heartbeat = format_heartbeat()
                if send_workwechat_text(heartbeat, workwechat):
                    cache.record_daily_activity(end_time.date(), end_time)
                    print("查询完成：今日无新增财报，心跳消息已发送")
                    return 0
                print(
                    "查询完成：今日无新增财报，但心跳消息发送失败",
                    file=sys.stderr,
                )
                return 1
            print(f"查询完成：当前 {len(reports)} 家，无新增财报")
            return 0
        if result.notification_sent:
            cache.clear_market_cap_pending(_report_references(reports))
            cache.mark_processed(config.markets, end_time)
            cache.record_daily_activity(end_time.date(), end_time)
            print(f"查询完成：已推送 {len(result.new_reports)} 家新财报")
            return 0
        print(
            f"查询完成：发现 {len(result.new_reports)} 家新财报，但企业微信发送失败；"
            "状态未确认，下次将重试",
            file=sys.stderr,
        )
        return 1
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        if args.command == "run" and not args.dry_run:
            try:
                workwechat = load_workwechat_config(args.config)
                send_workwechat_text(
                    "巨潮财报监控执行失败："
                    f"{exc}\n下一小时自动重试，扫描游标未推进。",
                    workwechat,
                )
            except (OSError, RuntimeError, ValueError):
                pass
        print(f"cninfo-monitor 执行失败：{exc}", file=sys.stderr)
        return 1


def format_heartbeat() -> str:
    return "今日无新增财报"


def _report_references(reports: list[Report]) -> list[tuple[str, str]]:
    return [(report.market, report.announcement_id) for report in reports]


def _resolve_end_time(args: argparse.Namespace) -> dt.datetime:
    if args.end_time:
        parsed = dt.datetime.strptime(args.end_time, "%Y-%m-%d %H:%M:%S")
        return parsed.replace(tzinfo=CNINFO_TIMEZONE)
    if args.end_date:
        parsed_date = dt.date.fromisoformat(args.end_date)
        return dt.datetime.combine(
            parsed_date,
            dt.time(23, 59, 59),
            tzinfo=CNINFO_TIMEZONE,
        )
    return dt.datetime.now(CNINFO_TIMEZONE).replace(microsecond=0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cninfo-monitor",
        description="探测巨潮资讯新发布的正式财报并推送到企业微信",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="查询并推送新增财报")
    run_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    end_group = run_parser.add_mutually_exclusive_group()
    end_group.add_argument("--end-date", help="查询截止日期（YYYY-MM-DD）")
    end_group.add_argument(
        "--end-time",
        help="查询截止时间（YYYY-MM-DD HH:MM:SS，北京时间）",
    )
    run_parser.add_argument(
        "--notify-existing",
        action="store_true",
        help="首次运行也推送查询到的已有财报",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只显示查询结果，不推送也不写入状态",
    )

    test_parser = subparsers.add_parser(
        "test-notification",
        help="发送一条企业微信测试消息",
    )
    test_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    test_parser.add_argument("--message", default="")

    refresh_parser = subparsers.add_parser(
        "refresh-indexes",
        help="从指数公司官方来源刷新本地成份股缓存",
    )
    refresh_parser.add_argument(
        "--output",
        default="data/index_memberships.json",
        help="缓存输出路径（默认 data/index_memberships.json）",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
