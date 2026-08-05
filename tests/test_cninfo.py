from __future__ import annotations

from cninfo_monitor.cninfo import query_all_announcements, select_formal_reports


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
