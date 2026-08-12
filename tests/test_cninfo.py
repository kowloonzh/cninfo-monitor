from __future__ import annotations

from cninfo_monitor.cninfo import (
    query_all_announcements,
    query_hong_kong_announcements,
    select_formal_reports,
)


def announcement(
    code: str,
    name: str,
    title: str,
    announcement_id: str,
    timestamp: int = 1785859200000,
) -> dict:
    return {
        "secCode": code,
        "secName": name,
        "announcementTitle": title,
        "announcementId": announcement_id,
        "announcementTime": timestamp,
        "adjunctUrl": f"finalpage/2026-08-05/{announcement_id}.PDF",
    }


def test_selects_only_formal_reports_for_requested_year_and_type():
    rows = [
        announcement("000001", "平安银行", "2026年半年度报告", "1"),
        announcement(
            "600206",
            "有研新材",
            "有研新材料股份有限公司2026年半年度报告",
            "prefixed",
        ),
        announcement("000001", "平安银行", "2026年半年度报告摘要", "2"),
        announcement("000002", "万科A", "2025年半年度报告（更新后）", "3"),
        announcement("000003", "公司三", "2026年半年度报告（修订版）", "4"),
        announcement("000004", "公司四", "关于2026年半年度报告的更正公告", "5"),
    ]

    reports = select_formal_reports(rows, {"interim"})

    assert [(report.sec_code, report.title) for report in reports] == [
        ("000001", "2026年半年度报告"),
        ("600206", "有研新材料股份有限公司2026年半年度报告"),
    ]
    assert reports[0].disclosure_date == "2026-08-05"
    assert reports[0].pdf_url.endswith("/finalpage/2026-08-05/1.PDF")


def test_deduplicates_company_and_report_type_using_latest_formal_file():
    rows = [
        announcement("600000", "浦发银行", "2026年半年度报告", "old", 1785859200000),
        announcement("600000", "浦发银行", "2026年半年度报告", "new", 1785945600000),
        announcement("600000", "浦发银行", "2026年年度报告", "annual", 1786032000000),
    ]

    reports = select_formal_reports(rows, {"interim", "annual"})

    assert [(report.report_type, report.announcement_id) for report in reports] == [
        ("interim", "new"),
        ("annual", "annual"),
    ]


def test_infers_report_year_from_each_formal_report_title():
    rows = [
        announcement("000001", "公司一", "2025年年度报告", "2025"),
        announcement("000002", "公司二", "公司二2026年年度报告", "2026"),
    ]

    reports = select_formal_reports(rows, {"annual"})

    assert [(report.sec_code, report.report_year) for report in reports] == [
        ("000001", 2025),
        ("000002", 2026),
    ]


def test_queries_every_configured_category_and_follows_pagination():
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self):
            self.calls = []

        def post(self, url, data):
            self.calls.append((url, data.copy()))
            page = data["pageNum"]
            category = data["category"]
            return FakeResponse(
                {
                    "announcements": [{"announcementId": f"{category}-{page}"}],
                    "hasMore": page == 1 and category == "category_bndbg_szsh",
                }
            )

    client = FakeClient()
    rows = query_all_announcements(
        client,
        start_date="2026-07-01",
        end_date="2026-08-05",
        report_types={"interim", "annual"},
    )

    assert [row["announcementId"] for row in rows] == [
        "category_ndbg_szsh-1",
        "category_bndbg_szsh-1",
        "category_bndbg_szsh-2",
    ]
    assert all(call[1]["seDate"] == "2026-07-01~2026-08-05" for call in client.calls)


def test_queries_hong_kong_by_report_keywords_and_deduplicates_announcements():
    class FakeResponse:
        def __init__(self, keyword, page):
            self.keyword = keyword
            self.page = page

        def raise_for_status(self):
            return None

        def json(self):
            rows = [{"announcementId": f"{self.keyword}-{self.page}"}]
            if self.keyword == "中期" and self.page == 1:
                rows.append({"announcementId": "shared"})
            if self.keyword == "六个月" and self.page == 1:
                rows.append({"announcementId": "shared"})
            return {
                "announcements": rows,
                "hasMore": self.keyword == "中期" and self.page == 1,
            }

    class FakeClient:
        def __init__(self):
            self.calls = []

        def post(self, url, data):
            self.calls.append(data.copy())
            return FakeResponse(data["searchkey"], data["pageNum"])

    client = FakeClient()
    rows = query_hong_kong_announcements(
        client,
        start_date="2026-07-12",
        end_date="2026-08-12",
        report_types={"interim"},
    )

    assert len(rows) == len({row["announcementId"] for row in rows})
    assert {call["searchkey"] for call in client.calls} >= {
        "中期",
        "六个月",
        "6个月",
        "半年度",
    }
    assert all(call["column"] == "hke" for call in client.calls)
    assert all(call["category"] == "" for call in client.calls)


def test_selects_hong_kong_interim_results_and_excludes_related_notices():
    rows = [
        announcement(
            "00700",
            "腾讯控股",
            "截至二零二六年六月三十日止三个月及六个月业绩公布",
            "tencent",
        ),
        announcement("02314", "理文造纸", "2026 中期报告", "report"),
        announcement(
            "09863",
            "零跑汽车",
            "董事会会议召开日期及2026年中期业绩电话会议",
            "meeting",
        ),
        announcement(
            "00746",
            "理文化工",
            "致登记股东 - 刊发二零二六年中期报告之通知",
            "notice",
        ),
        announcement("00006", "电能实业", "截至2026年6月30日止6个月的中期股息", "dividend"),
        announcement("02600", "中国铝业", "2026年中期业绩预增公告", "forecast"),
        announcement("03618", "重庆农商行", "2026年中期业绩快报公告", "flash"),
        announcement("06656", "思格新能", "截至2026年6月30日止六个月之估计中期业绩", "estimate"),
        announcement("01866", "中国心连心化肥", "截至二零二六年六月三十日止六个月的业绩盈喜", "alert"),
        announcement("00220", "统一企业中国", "2026年中期业绩演示材料", "slides"),
        announcement("00902", "华能国际", "关于召开2026年中期业绩推介全球投资者电话会议的公告", "call"),
    ]

    reports = select_formal_reports(rows, {"interim"}, market="hong_kong")

    assert [(item.sec_code, item.report_year, item.market) for item in reports] == [
        ("00700", 2026, "hong_kong"),
        ("02314", 2026, "hong_kong"),
    ]


def test_classifies_other_hong_kong_report_types():
    rows = [
        announcement("00001", "长和", "截至二零二五年十二月三十一日止年度全年业绩公布", "annual"),
        announcement("00002", "中电控股", "截至二零二六年三月三十一日止三个月业绩公布", "q1"),
        announcement("00003", "香港中华煤气", "截至二零二六年九月三十日止九个月业绩公布", "q3"),
    ]

    reports = select_formal_reports(
        rows,
        {"annual", "first_quarter", "third_quarter"},
        market="hong_kong",
    )

    assert [(item.report_type, item.report_year) for item in reports] == [
        ("annual", 2025),
        ("first_quarter", 2026),
        ("third_quarter", 2026),
    ]
