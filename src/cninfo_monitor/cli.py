from __future__ import annotations

import argparse
import calendar
import datetime as dt
import sys
from pathlib import Path

import httpx

from cninfo_monitor.cninfo import (
    CNINFO_TIMEZONE,
    query_all_announcements,
    select_formal_reports,
)
from cninfo_monitor.config import MonitorConfig, load_monitor_config
from cninfo_monitor.models import Report
from cninfo_monitor.monitor import JsonStateStore, format_digest, run_monitor
from cninfo_monitor.notifications import (
    load_workwechat_config,
    send_workwechat_text,
)


DEFAULT_CONFIG_PATH = Path("config/config.yaml")
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


def fetch_reports(config: MonitorConfig, end_date: str) -> list[Report]:
    end = dt.date.fromisoformat(end_date)
    start_date = rolling_month_start(end_date)
    with httpx.Client(
        headers=CNINFO_HEADERS,
        timeout=config.request_timeout,
        follow_redirects=True,
    ) as client:
        announcements = query_all_announcements(
            client,
            start_date=start_date,
            end_date=end.isoformat(),
            report_types=config.report_types,
        )
    return select_formal_reports(
        announcements,
        config.report_types,
    )


def rolling_month_start(end_date: str) -> str:
    end = dt.date.fromisoformat(end_date)
    if end.month == 1:
        year, month = end.year - 1, 12
    else:
        year, month = end.year, end.month - 1
    day = min(end.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
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
        end_date = args.end_date or dt.datetime.now(
            CNINFO_TIMEZONE
        ).date().isoformat()
        reports = fetch_reports(config, end_date)
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
        )
        if result.baselined:
            print(f"首次运行已建立基线：{result.baselined} 家，本次不推送")
            return 0
        if not result.new_reports:
            print(f"查询完成：当前 {len(reports)} 家，无新增财报")
            return 0
        if result.notification_sent:
            print(f"查询完成：已推送 {len(result.new_reports)} 家新财报")
            return 0
        print(
            f"查询完成：发现 {len(result.new_reports)} 家新财报，但企业微信发送失败；"
            "状态未确认，下次将重试",
            file=sys.stderr,
        )
        return 1
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"cninfo-monitor 执行失败：{exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cninfo-monitor",
        description="探测巨潮资讯新发布的正式财报并推送到企业微信",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="查询并推送新增财报")
    run_parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    run_parser.add_argument("--end-date", help="查询截止日期（YYYY-MM-DD）")
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
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
