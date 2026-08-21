from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import importlib

from cninfo_monitor.models import Report


def report(code: str, market: str = "mainland") -> Report:
    return Report(
        sec_code=code,
        sec_name="测试公司",
        report_year=2026,
        report_type="interim",
        title="2026年半年度报告",
        disclosure_date="2026-08-18",
        announcement_id=code,
        pdf_url=f"https://static.cninfo.com.cn/{code}.PDF",
        market=market,
    )


def test_maps_mainland_and_hong_kong_quote_symbols():
    quotes = importlib.import_module("cninfo_monitor.quotes")

    assert quotes.quote_symbol(report("600674")) == "sh600674"
    assert quotes.quote_symbol(report("000001")) == "sz000001"
    assert quotes.quote_symbol(report("920001")) == "bj920001"
    assert quotes.quote_symbol(report("00700", "hong_kong")) == "hk00700"


def test_enriches_reports_with_total_market_cap_from_field_45():
    quotes = importlib.import_module("cninfo_monitor.quotes")
    mainland_fields = [""] * 74
    mainland_fields[1] = "川投能源"
    mainland_fields[2] = "600674"
    mainland_fields[45] = "732.65"
    hong_kong_fields = [""] * 74
    hong_kong_fields[1] = "腾讯控股"
    hong_kong_fields[2] = "00700"
    hong_kong_fields[45] = "40508.8519"
    b_share_fields = [""] * 83
    b_share_fields[1] = "闽灿坤B"
    b_share_fields[2] = "200512"
    b_share_fields[45] = "4.34"
    b_share_fields[82] = "HKD"
    body = (
        f'v_sh600674="{"~".join(mainland_fields)}";\n'
        f'v_hk00700="{"~".join(hong_kong_fields)}";\n'
        f'v_sz200512="{"~".join(b_share_fields)}";\n'
    ).encode("gbk")

    class FakeResponse:
        content = body

        def raise_for_status(self):
            return None

    class FakeClient:
        def get(self, url):
            return FakeResponse()

    enriched = quotes.enrich_reports_with_market_caps(
        FakeClient(),
        [
            report("600674"),
            report("00700", "hong_kong"),
            report("200512"),
        ],
    )

    assert [item.total_market_cap for item in enriched] == [
        "732.65亿元",
        "40508.85亿港元",
        "4.34亿港元",
    ]


def test_quote_failure_keeps_reports_with_unavailable_market_cap():
    quotes = importlib.import_module("cninfo_monitor.quotes")

    class FailingClient:
        def get(self, url):
            raise RuntimeError("quote service unavailable")

    enriched = quotes.enrich_reports_with_market_caps(
        FailingClient(),
        [report("600674")],
    )

    assert len(enriched) == 1
    assert enriched[0].total_market_cap is None


def test_filters_only_known_market_caps_below_the_configured_threshold():
    quotes = importlib.import_module("cninfo_monitor.quotes")
    reports = [
        replace(report("000001"), total_market_cap="99.99亿元"),
        replace(report("000002"), total_market_cap="100.00亿元"),
        replace(
            report("00700", "hong_kong"),
            total_market_cap="100.01亿港元",
        ),
        report("600674"),
    ]

    filtered = quotes.filter_reports_by_minimum_market_cap(
        reports,
        Decimal("100"),
    )

    assert [item.sec_code for item in filtered] == ["000002", "00700", "600674"]
