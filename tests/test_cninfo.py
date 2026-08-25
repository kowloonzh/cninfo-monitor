from __future__ import annotations

import datetime as dt

import pytest

from cninfo_monitor.cninfo import (
    CNINFO_TIMEZONE,
    query_announcements_by_time,
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


def test_queries_all_announcements_by_exact_time_and_follows_pagination():
    class FakeResponse:
        def __init__(self, page):
            self.page = page

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "announcements": [{"announcementId": str(self.page)}],
                "hasMore": self.page == 1,
            }

    class FakeClient:
        def __init__(self):
            self.calls = []

        def post(self, url, data):
            self.calls.append(data.copy())
            return FakeResponse(data["pageNum"])

    client = FakeClient()
    start = dt.datetime(2026, 8, 23, 8, 0, 0, tzinfo=CNINFO_TIMEZONE)
    end = dt.datetime(2026, 8, 23, 9, 0, 0, tzinfo=CNINFO_TIMEZONE)

    rows = query_announcements_by_time(
        client,
        market="hong_kong",
        start_time=start,
        end_time=end,
        retry_delays=(),
    )

    assert [row["announcementId"] for row in rows] == ["1", "2"]
    assert [call["pageNum"] for call in client.calls] == [1, 2]
    assert all(call["column"] == "hke" for call in client.calls)
    assert all(call["searchkey"] == "" and call["category"] == "" for call in client.calls)
    assert all(
        call["seDate"] == "2026-08-23 08:00:00~2026-08-23 09:00:00"
        for call in client.calls
    )


def test_time_query_retries_a_failed_page():
    class FakeResponse:
        def __init__(self, should_fail):
            self.should_fail = should_fail

        def raise_for_status(self):
            if self.should_fail:
                raise RuntimeError("temporary 504")

        def json(self):
            return {"announcements": [], "hasMore": False}

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def post(self, url, data):
            self.calls += 1
            return FakeResponse(self.calls == 1)

    client = FakeClient()
    now = dt.datetime(2026, 8, 23, 9, 0, 0, tzinfo=CNINFO_TIMEZONE)

    query_announcements_by_time(
        client,
        market="mainland",
        start_time=now - dt.timedelta(hours=1),
        end_time=now,
        retry_delays=(0,),
    )

    assert client.calls == 2


def test_time_query_passes_a_mainland_report_category():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"announcements": [], "hasMore": False}

    class FakeClient:
        def __init__(self):
            self.calls = []

        def post(self, url, data):
            self.calls.append(data.copy())
            return FakeResponse()

    client = FakeClient()
    now = dt.datetime(2026, 8, 25, 9, 0, 0, tzinfo=CNINFO_TIMEZONE)

    query_announcements_by_time(
        client,
        market="mainland",
        plate="sz",
        category="category_bndbg_szsh",
        start_time=now.replace(hour=0),
        end_time=now,
        retry_delays=(),
    )

    assert client.calls[0]["category"] == "category_bndbg_szsh"


def test_time_query_stops_after_a_page_is_already_cached():
    class FakeResponse:
        def __init__(self, page):
            self.page = page

        def raise_for_status(self):
            return None

        def json(self):
            rows = {
                1: [{"announcementId": "new"}, {"announcementId": "known-1"}],
                2: [{"announcementId": "known-2"}, {"announcementId": "known-3"}],
                3: [{"announcementId": "older"}],
            }[self.page]
            return {"announcements": rows, "hasMore": self.page < 3}

    class FakeClient:
        def __init__(self):
            self.calls = []

        def post(self, url, data):
            self.calls.append(data.copy())
            return FakeResponse(data["pageNum"])

    client = FakeClient()
    now = dt.datetime(2026, 8, 23, 9, 0, 0, tzinfo=CNINFO_TIMEZONE)

    rows = query_announcements_by_time(
        client,
        market="mainland",
        start_time=now.replace(hour=0),
        end_time=now,
        known_announcement_ids={"known-1", "known-2", "known-3"},
        retry_delays=(),
    )

    assert [call["pageNum"] for call in client.calls] == [1, 2]
    assert [row["announcementId"] for row in rows] == [
        "new",
        "known-1",
        "known-2",
        "known-3",
    ]


def test_time_query_rejects_a_repeated_page_instead_of_looping_forever():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "announcements": [{"announcementId": "same-page"}],
                "hasMore": True,
            }

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def post(self, url, data):
            self.calls += 1
            return FakeResponse()

    client = FakeClient()
    now = dt.datetime(2026, 8, 23, 9, 0, 0, tzinfo=CNINFO_TIMEZONE)

    with pytest.raises(RuntimeError, match="重复页"):
        query_announcements_by_time(
            client,
            market="mainland",
            plate="sz",
            start_time=now.replace(hour=0),
            end_time=now,
            retry_delays=(),
        )

    assert client.calls == 2


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


def test_classifies_hong_kong_monthly_named_quarterly_results_separately():
    rows = [
        announcement(
            "09988",
            "阿里巴巴-W",
            "2026年六月底止季度业绩公告",
            "june",
        ),
        announcement(
            "09988",
            "阿里巴巴-W",
            "阿里巴巴集团2026年9月份季度业绩公告",
            "september",
        ),
        announcement(
            "09988",
            "阿里巴巴-W",
            "阿里巴巴集团2026年9月份季度业绩预告",
            "forecast",
        ),
    ]

    reports = select_formal_reports(
        rows,
        {"interim", "quarterly"},
        market="hong_kong",
    )

    assert [(item.report_type, item.announcement_id) for item in reports] == [
        ("quarterly_06", "june"),
        ("quarterly_09", "september"),
    ]
    assert len({item.company_report_key for item in reports}) == 2


def test_classifies_numbered_hong_kong_quarters_and_excludes_operating_reports():
    rows = [
        announcement("09987", "百胜中国", "2026年第二季度财务业绩公告", "q2"),
        announcement("09901", "新东方-S", "2026年第四季度业绩报告", "q4"),
        announcement("01164", "中广核矿业", "2026年第二季度运营报告", "operations"),
    ]

    reports = select_formal_reports(rows, {"quarterly"}, market="hong_kong")

    assert [(item.report_type, item.announcement_id) for item in reports] == [
        ("quarterly_q4", "q4"),
        ("quarterly_q2", "q2"),
    ]


def test_hong_kong_all_types_keep_issuer_reports_and_exclude_related_documents():
    rows = [
        announcement("00941", "中国移动", "海外监管公告 2026年半年度报告", "interim"),
        announcement(
            "00992",
            "联想集团",
            "二零二六/二七年财政年度第一季业绩公布",
            "q1",
        ),
        announcement("03606", "福耀玻璃", "2026年半年度报告", "formal"),
        announcement("00998", "中信银行", "关于召开2026年半年度业绩发布会的公告", "meeting"),
        announcement("02692", "兆威机电", "海外监管公告 - 2026年半年度报告摘要", "summary"),
        announcement(
            "01385",
            "上海复旦",
            "2026年度提质增效重回报专项行动方案半年度评估报告",
            "assessment",
        ),
        announcement(
            "00728",
            "中国电信",
            "中国电信集团财务有限公司关联交易2026年半年度风险持续评估报告",
            "risk",
        ),
        announcement(
            "00053",
            "国浩集团",
            "附属公司截至二零二六年六月三十日止年度之业绩公告",
            "subsidiary",
        ),
        announcement(
            "01651",
            "津上机床中国",
            "内幕消息 控股股东截至二零二六年六月三十日止三个月之财务业绩",
            "shareholder",
        ),
        announcement(
            "01208",
            "五矿资源",
            "截至二零二六年六月三十日止三个月之第二季度生产报告",
            "production",
        ),
        announcement(
            "03958",
            "东方证券",
            "2025年度及截至2026年3月31日止三个月期间备考合并财务报表审阅报告",
            "pro-forma",
        ),
    ]

    reports = select_formal_reports(
        rows,
        {"annual", "first_quarter", "interim", "third_quarter", "quarterly"},
        market="hong_kong",
    )

    assert [(item.report_type, item.announcement_id) for item in reports] == [
        ("interim", "interim"),
        ("first_quarter", "q1"),
        ("interim", "formal"),
    ]
