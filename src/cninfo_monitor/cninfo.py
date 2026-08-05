from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Iterable
from typing import Any

from cninfo_monitor.models import Report


QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_BASE_URL = "https://static.cninfo.com.cn/"
CNINFO_TIMEZONE = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
REPORT_TYPE_DEFINITIONS = {
    "annual": ("category_ndbg_szsh", "年度报告"),
    "first_quarter": ("category_yjdbg_szsh", "第一季度报告"),
    "interim": ("category_bndbg_szsh", "半年度报告"),
    "third_quarter": ("category_sjdbg_szsh", "第三季度报告"),
}
EXCLUDED_TITLE_PARTS = ("摘要", "英文", "更正", "修订", "更新", "取消")
_HTML_TAG = re.compile(r"<[^>]+>")


def query_all_announcements(
    client: Any,
    *,
    start_date: str,
    end_date: str,
    report_types: Iterable[str],
) -> list[dict[str, Any]]:
    requested_types = set(report_types)
    _validate_report_types(requested_types)
    announcements: list[dict[str, Any]] = []

    for report_type, (category, _) in REPORT_TYPE_DEFINITIONS.items():
        if report_type not in requested_types:
            continue
        page = 1
        while True:
            response = client.post(
                QUERY_URL,
                data={
                    "pageNum": page,
                    "pageSize": 30,
                    "column": "szse",
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": "",
                    "searchkey": "",
                    "secid": "",
                    "category": category,
                    "trade": "",
                    "seDate": f"{start_date}~{end_date}",
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "false",
                },
            )
            response.raise_for_status()
            result = response.json()
            announcements.extend(result.get("announcements") or [])
            if not result.get("hasMore"):
                break
            page += 1

    return announcements


def select_formal_reports(
    announcements: Iterable[dict[str, Any]],
    report_types: Iterable[str],
) -> list[Report]:
    requested_types = set(report_types)
    _validate_report_types(requested_types)
    selected: dict[str, tuple[int, Report]] = {}

    for announcement in announcements:
        raw_title = str(announcement.get("announcementTitle", ""))
        title = _normalize_title(raw_title)
        report_identity = _match_report_type(title, requested_types)
        if report_identity is None:
            continue
        report_type, report_year = report_identity

        try:
            sec_code = str(announcement["secCode"])
            timestamp = int(announcement["announcementTime"])
            adjunct_url = str(announcement["adjunctUrl"]).lstrip("/")
            announcement_id = str(announcement["announcementId"])
        except (KeyError, TypeError, ValueError):
            continue

        disclosure_date = dt.datetime.fromtimestamp(
            timestamp / 1000,
            CNINFO_TIMEZONE,
        ).date().isoformat()
        report = Report(
            sec_code=sec_code,
            sec_name=str(announcement.get("secName", "")),
            report_year=report_year,
            report_type=report_type,
            title=title,
            disclosure_date=disclosure_date,
            announcement_id=announcement_id,
            pdf_url=PDF_BASE_URL + adjunct_url,
        )
        current = selected.get(report.company_report_key)
        if current is None or timestamp > current[0]:
            selected[report.company_report_key] = (timestamp, report)

    return [
        report
        for _, report in sorted(
            selected.values(),
            key=lambda item: (item[0], item[1].sec_code),
        )
    ]


def _match_report_type(
    title: str,
    requested_types: set[str],
) -> tuple[str, int] | None:
    if any(part in title for part in EXCLUDED_TITLE_PARTS):
        return None
    for report_type, (_, title_suffix) in REPORT_TYPE_DEFINITIONS.items():
        if report_type not in requested_types:
            continue
        match = re.search(rf"(?P<year>\d{{4}})年{re.escape(title_suffix)}", title)
        if match:
            return report_type, int(match.group("year"))
    return None


def _normalize_title(title: str) -> str:
    return html.unescape(_HTML_TAG.sub("", title)).strip()


def _validate_report_types(report_types: set[str]) -> None:
    unknown = report_types - REPORT_TYPE_DEFINITIONS.keys()
    if unknown:
        raise ValueError(f"unsupported report types: {', '.join(sorted(unknown))}")
