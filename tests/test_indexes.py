from __future__ import annotations

import json

import pytest

from cninfo_monitor.indexes import (
    INDEX_SPECS,
    build_index_snapshot,
    enrich_reports_with_index_memberships,
    load_index_memberships,
    parse_cni_payload,
    parse_csi_rows,
    parse_hsi_constituent_text,
)
from cninfo_monitor.models import Report


def report(code: str, market: str = "mainland") -> Report:
    return Report(
        sec_code=code,
        sec_name="测试公司",
        report_year=2026,
        report_type="interim",
        title="2026年半年度报告",
        disclosure_date="2026-08-18",
        announcement_id=f"{market}-{code}",
        pdf_url="https://example.com/report.pdf",
        market=market,
    )


def test_enriches_reports_with_multiple_indexes_and_separates_markets():
    memberships = {
        "mainland:00700": ("中证2000",),
        "hong_kong:00700": ("恒生科技",),
        "mainland:688017": ("中证1000", "机器人产业"),
    }

    enriched = enrich_reports_with_index_memberships(
        [
            report("00700"),
            report("00700", "hong_kong"),
            report("688017"),
            report("600674"),
        ],
        memberships,
    )

    assert [item.index_names for item in enriched] == [
        ("中证2000",),
        ("恒生科技",),
        ("中证1000", "机器人产业"),
        (),
    ]


def test_load_index_memberships_prefers_runtime_cache_and_falls_back_to_seed(
    tmp_path,
):
    runtime_path = tmp_path / "runtime.json"
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "version": 1,
                "memberships": {"mainland:000001": ["沪深300"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert load_index_memberships(runtime_path, seed_path=seed_path) == {
        "mainland:000001": ("沪深300",)
    }

    runtime_path.write_text(
        json.dumps(
            {
                "version": 1,
                "memberships": {"hong_kong:00700": ["恒生科技"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert load_index_memberships(runtime_path, seed_path=seed_path) == {
        "hong_kong:00700": ("恒生科技",)
    }


def test_invalid_runtime_cache_falls_back_to_seed(tmp_path):
    runtime_path = tmp_path / "runtime.json"
    seed_path = tmp_path / "seed.json"
    runtime_path.write_text("not-json", encoding="utf-8")
    seed_path.write_text(
        json.dumps(
            {
                "version": 1,
                "memberships": {"mainland:000001": ["沪深300"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert load_index_memberships(runtime_path, seed_path=seed_path) == {
        "mainland:000001": ("沪深300",)
    }


def test_hsi_text_parser_extracts_all_stock_codes_and_normalizes_to_five_digits():
    text = """
成份股
股票号码 国际证券号码 公司名称 行业分类 股份类别 比重(%)
3690 KYG596691041 美团 - W 非必需性消费 其他香港上市内地公司 9.32
0700 KYG875721634 腾讯控股 资讯科技业 其他香港上市内地公司 8.50
0100 KYG6181S1093 MINIMAX - W 资讯科技业 其他香港上市内地公司 0.12
合共 100.00
"""

    assert parse_hsi_constituent_text(text) == {"03690", "00700", "00100"}


def test_csi_row_parser_uses_official_date_and_rejects_wrong_index_code():
    rows = [
        ["日期Date", "指数代码 Index Code", "指数名称", "英文名", "成份券代码"],
        ["20260817", "000300", "沪深300", "CSI 300", "000001"],
        ["20260817", "000300", "沪深300", "CSI 300", "600519"],
    ]

    assert parse_csi_rows(rows, "000300") == (
        {"000001", "600519"},
        "2026-08-17",
    )

    rows[2][1] = "000905"
    with pytest.raises(ValueError, match="000905"):
        parse_csi_rows(rows, "000300")


def test_cni_payload_parser_reads_all_rows_and_as_of_date():
    payload = {
        "code": 200,
        "data": {
            "total": 2,
            "rows": [
                {"dateStr": "2026-08-17", "seccode": "688017"},
                {"dateStr": "2026-08-17", "seccode": "300024"},
            ],
        },
    }

    assert parse_cni_payload(payload) == (
        {"688017", "300024"},
        "2026-08-17",
    )


def test_build_snapshot_preserves_configured_index_order_and_validates_counts():
    constituents = {
        spec.code: {f"{number:06d}" for number in range(spec.expected_count)}
        for spec in INDEX_SPECS
    }
    constituents["HSTECH"] = {f"{number:05d}" for number in range(30)}

    snapshot = build_index_snapshot(
        constituents,
        as_of={spec.code: "2026-08-17" for spec in INDEX_SPECS},
        generated_at="2026-08-18T10:00:00+08:00",
    )

    assert snapshot["version"] == 1
    assert snapshot["memberships"]["mainland:000000"] == [
        "沪深300",
        "中证500",
        "中证1000",
        "中证2000",
        "科创50",
        "科创创业50",
        "机器人产业",
    ]
    assert snapshot["memberships"]["hong_kong:00000"] == ["恒生科技"]

    constituents["000300"].pop()
    with pytest.raises(ValueError, match="沪深300.*299.*300"):
        build_index_snapshot(
            constituents,
            as_of={spec.code: "2026-08-17" for spec in INDEX_SPECS},
            generated_at="2026-08-18T10:00:00+08:00",
        )
