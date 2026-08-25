from __future__ import annotations

import datetime as dt
import html
import re
import time
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
SUPPORTED_REPORT_TYPES = frozenset({*REPORT_TYPE_DEFINITIONS, "quarterly"})
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
    "业绩发布会",
    "業績發布會",
    "摘要",
    "评估报告",
    "評估報告",
    "风险持续评估",
    "風險持續評估",
    "附属公司",
    "附屬公司",
    "控股股东",
    "控股股東",
    "控股子公司",
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


def query_announcements_by_time(
    client: Any,
    *,
    market: str,
    plate: str = "",
    category: str = "",
    start_time: dt.datetime,
    end_time: dt.datetime,
    known_announcement_ids: set[str] | frozenset[str] = frozenset(),
    retry_delays: tuple[float, ...] = (1.0, 3.0),
) -> list[dict[str, Any]]:
    columns = {"mainland": "szse", "hong_kong": "hke"}
    try:
        column = columns[market]
    except KeyError as exc:
        raise ValueError(f"unsupported market: {market}") from exc
    date_range = (
        f"{start_time.astimezone(CNINFO_TIMEZONE):%Y-%m-%d %H:%M:%S}~"
        f"{end_time.astimezone(CNINFO_TIMEZONE):%Y-%m-%d %H:%M:%S}"
    )
    announcements: dict[str, dict[str, Any]] = {}
    seen_page_signatures: set[tuple[str, ...]] = set()
    page = 1
    while True:
        data = {
            "pageNum": page,
            "pageSize": 30,
            "column": column,
            "tabName": "fulltext",
            "plate": plate,
            "stock": "",
            "searchkey": "",
            "secid": "",
            "category": category,
            "trade": "",
            "seDate": date_range,
            "sortName": "",
            "sortType": "",
            "isHLtitle": "false",
        }
        delays = iter((*retry_delays, None))
        while True:
            try:
                response = client.post(QUERY_URL, data=data)
                response.raise_for_status()
                result = response.json()
                break
            except Exception:
                delay = next(delays)
                if delay is None:
                    raise
                time.sleep(delay)
        page_announcements = result.get("announcements") or []
        page_ids: set[str] = set()
        for announcement in page_announcements:
            announcement_id = announcement.get("announcementId")
            if announcement_id is not None:
                normalized_id = str(announcement_id)
                page_ids.add(normalized_id)
                announcements[normalized_id] = announcement
        page_signature = tuple(
            str(item.get("announcementId")) for item in page_announcements
        )
        if page_signature and page_signature in seen_page_signatures:
            raise RuntimeError(
                "巨潮分页出现重复页："
                f"market={market}, plate={plate or '-'}, "
                f"category={category or '-'}, page={page}"
            )
        if page_signature:
            seen_page_signatures.add(page_signature)
        if (
            known_announcement_ids
            and page_ids
            and page_ids <= known_announcement_ids
        ):
            break
        if not result.get("hasMore"):
            break
        if page >= 100:
            raise RuntimeError(
                "巨潮分页超过 100 页："
                f"market={market}, plate={plate or '-'}, "
                f"category={category or '-'}"
            )
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
    performance_words = r"(?:业绩|業績)"
    six_months = r"(?:六|6)(?:个|個)月"
    patterns = {
        "interim": rf"(?:(?:中期|半年度).*{result_words}|{six_months}.*{performance_words})",
        "annual": (
            rf"(?:(?<!半)年度(?:报告|報告)|年报|年報|全年.*(?:业绩|業績)|"
            r"(?:年度|十二个月|十二個月|12(?:个|個)月)"
            r"(?:全年)?(?:的|之)?(?:未经审核|未經審核|未经审计|未經審計)?"
            r"(?:综合|綜合|财务|財務)?(?:业绩|業績))"
        ),
        "third_quarter": (
            rf"(?:(?:第三季度|第三季).*(?:{result_words})|"
            rf"(?:九个月|九個月|9(?:个|個)月).*{performance_words})"
        ),
        "first_quarter": (
            rf"(?:(?:第一季度|第一季|首季度|首季).*(?:{result_words})|"
            rf"(?:三个月|三個月|3(?:个|個)月).*{performance_words})"
        ),
        "quarterly": r"(?:季度(?:财务|財務)?(?:报告|報告|业绩|業績)|季报|季報)",
    }
    for report_type in (
        "interim",
        "third_quarter",
        "first_quarter",
        "quarterly",
        "annual",
    ):
        if report_type in requested_types and re.search(patterns[report_type], title):
            if report_type == "quarterly":
                return _quarterly_period_type(title), year
            return report_type, year
    return None


def _quarterly_period_type(title: str) -> str:
    quarter_match = re.search(r"第(?P<quarter>[一二三四1234])季度", title)
    if quarter_match is not None:
        raw_quarter = quarter_match.group("quarter")
        chinese_quarters = {"一": 1, "二": 2, "三": 3, "四": 4}
        quarter = chinese_quarters.get(
            raw_quarter,
            int(raw_quarter) if raw_quarter.isdigit() else 0,
        )
        if quarter:
            return f"quarterly_q{quarter}"

    match = re.search(
        rf"{_YEAR_TOKEN}年(?P<month>1[0-2]|0?[1-9]|十[一二]?|[一二三四五六七八九])"
        r"月份?.*?季度",
        title,
    )
    if match is None:
        return "quarterly"
    raw_month = match.group("month")
    chinese_months = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "十一": 11,
        "十二": 12,
    }
    month = chinese_months.get(raw_month, int(raw_month) if raw_month.isdigit() else 0)
    return f"quarterly_{month:02d}" if month else "quarterly"


def _normalize_title(title: str) -> str:
    return html.unescape(_HTML_TAG.sub("", title)).strip()


def _validate_report_types(report_types: set[str]) -> None:
    unknown = report_types - SUPPORTED_REPORT_TYPES
    if unknown:
        raise ValueError(f"unsupported report types: {', '.join(sorted(unknown))}")
