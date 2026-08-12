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
HONG_KONG_REPORT_KEYWORDS = {
    "annual": ("年度报告", "年报", "全年业绩", "年度业绩", "十二个月", "12个月"),
    "first_quarter": ("第一季度", "首季度", "三个月", "3个月"),
    "interim": ("中期", "六个月", "6个月", "半年度"),
    "third_quarter": ("第三季度", "九个月", "9个月"),
}
EXCLUDED_TITLE_PARTS = ("摘要", "英文", "更正", "修订", "更新", "取消")
HONG_KONG_EXCLUDED_TITLE_PARTS = (
    "通知",
    "说明会",
    "說明會",
    "董事会",
    "董事會",
    "延迟",
    "延遲",
    "盈利警告",
    "盈喜",
    "盈利预警",
    "盈利預警",
    "业绩预告",
    "業績預告",
    "业绩预增",
    "業績預增",
    "业绩快报",
    "業績快報",
    "估计",
    "估計",
    "预计",
    "預計",
    "演示材料",
    "推介",
    "电话会议",
    "電話會議",
    "更正",
    "修订",
    "修訂",
    "补充公告",
    "補充公告",
    "澄清公告",
)
_HTML_TAG = re.compile(r"<[^>]+>")
_YEAR_TOKEN = r"(?:20\d{2}|[二〇零○Ｏ一二三四五六七八九]{4})"
_CHINESE_DIGITS = str.maketrans("二〇零○Ｏ一二三四五六七八九", "20000123456789")


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


def query_hong_kong_announcements(
    client: Any,
    *,
    start_date: str,
    end_date: str,
    report_types: Iterable[str],
) -> list[dict[str, Any]]:
    requested_types = set(report_types)
    _validate_report_types(requested_types)
    keywords = {
        keyword
        for report_type in requested_types
        for keyword in HONG_KONG_REPORT_KEYWORDS[report_type]
    }
    announcements: dict[str, dict[str, Any]] = {}

    for keyword in sorted(keywords):
        page = 1
        while True:
            response = client.post(
                QUERY_URL,
                data={
                    "pageNum": page,
                    "pageSize": 30,
                    "column": "hke",
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": "",
                    "searchkey": keyword,
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": f"{start_date}~{end_date}",
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "false",
                },
            )
            response.raise_for_status()
            result = response.json()
            for announcement in result.get("announcements") or []:
                announcement_id = announcement.get("announcementId")
                if announcement_id is not None:
                    announcements[str(announcement_id)] = announcement
            if not result.get("hasMore"):
                break
            page += 1

    return list(announcements.values())


def select_formal_reports(
    announcements: Iterable[dict[str, Any]],
    report_types: Iterable[str],
    *,
    market: str = "mainland",
) -> list[Report]:
    requested_types = set(report_types)
    _validate_report_types(requested_types)
    selected: dict[str, tuple[int, Report]] = {}

    for announcement in announcements:
        raw_title = str(announcement.get("announcementTitle", ""))
        title = _normalize_title(raw_title)
        if market == "hong_kong":
            report_identity = _match_hong_kong_report_type(title, requested_types)
        else:
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
            market=market,
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


def _match_hong_kong_report_type(
    title: str,
    requested_types: set[str],
) -> tuple[str, int] | None:
    if any(part in title for part in HONG_KONG_EXCLUDED_TITLE_PARTS):
        return None
    year_match = re.search(_YEAR_TOKEN, title)
    if year_match is None:
        return None
    year = int(year_match.group().translate(_CHINESE_DIGITS))

    result_words = r"(?:报告|報告|业绩|業績|年报|年報)"
    six_months = r"(?:(?:六|6)个?月|(?:六|6)個月)"
    patterns = {
        "interim": rf"(?:中期.*{result_words}|{six_months}.*{result_words})",
        "annual": rf"(?:(?:年度|全年|十二个月|十二個月|12个?月).*(?:{result_words})|年报|年報)",
        "third_quarter": rf"(?:第三季度|九个月|九個月|9个?月).*(?:{result_words})",
        "first_quarter": rf"(?:第一季度|首季度|三个月|三個月|3个?月).*(?:{result_words})",
    }
    for report_type in ("interim", "annual", "third_quarter", "first_quarter"):
        if report_type in requested_types and re.search(patterns[report_type], title):
            return report_type, year
    return None


def _normalize_title(title: str) -> str:
    return html.unescape(_HTML_TAG.sub("", title)).strip()


def _validate_report_types(report_types: set[str]) -> None:
    unknown = report_types - REPORT_TYPE_DEFINITIONS.keys()
    if unknown:
        raise ValueError(f"unsupported report types: {', '.join(sorted(unknown))}")
