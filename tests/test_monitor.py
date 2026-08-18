from __future__ import annotations

from dataclasses import replace
import json

from cninfo_monitor import monitor
from cninfo_monitor.models import Report
from cninfo_monitor.monitor import (
    JsonStateStore,
    format_digest,
    run_monitor,
)


def report(
    code: str,
    name: str,
    announcement_id: str,
    market: str = "mainland",
) -> Report:
    return Report(
        sec_code=code,
        sec_name=name,
        report_year=2026,
        report_type="interim",
        title="2026年半年度报告",
        disclosure_date="2026-08-05",
        announcement_id=announcement_id,
        pdf_url=f"https://static.cninfo.com.cn/{announcement_id}.PDF",
        market=market,
    )


def test_first_run_builds_silent_baseline(tmp_path):
    state = JsonStateStore(tmp_path / "state.json")
    calls = []

    result = run_monitor(
        [report("000001", "平安银行", "1"), report("000002", "万科A", "2")],
        state,
        sender=lambda message: calls.append(message) or True,
        bootstrap_silently=True,
    )

    assert result.baselined == 2
    assert result.new_reports == ()
    assert calls == []
    assert state.known_keys() == {
        "2026:interim:000001",
        "2026:interim:000002",
    }


def test_later_run_sends_only_new_company_and_is_idempotent(tmp_path):
    state = JsonStateStore(tmp_path / "state.json")
    first = report("000001", "平安银行", "1")
    second = report("000002", "万科A", "2")
    run_monitor([first], state, sender=lambda _: True, bootstrap_silently=True)
    calls = []

    result = run_monitor(
        [first, second],
        state,
        sender=lambda message: calls.append(message) or True,
        bootstrap_silently=True,
    )
    repeated = run_monitor(
        [first, second],
        state,
        sender=lambda message: calls.append(message) or True,
        bootstrap_silently=True,
    )

    assert result.new_reports == (second,)
    assert result.notification_sent is True
    assert len(calls) == 1
    assert "万科A（000002）" in calls[0]
    assert repeated.new_reports == ()


def test_failed_notification_is_not_marked_as_known_so_it_retries(tmp_path):
    state = JsonStateStore(tmp_path / "state.json")
    first = report("000001", "平安银行", "1")
    run_monitor([], state, sender=lambda _: True, bootstrap_silently=True)

    failed = run_monitor(
        [first],
        state,
        sender=lambda _: False,
        bootstrap_silently=True,
    )
    assert state.known_keys() == set()

    retried = run_monitor(
        [first],
        state,
        sender=lambda _: True,
        bootstrap_silently=True,
    )

    assert failed.notification_sent is False
    assert state.known_keys() == {"2026:interim:000001"}
    assert retried.notification_sent is True
    assert retried.new_reports == (first,)


def test_format_digest_contains_summary_type_date_and_pdf_link():
    first = replace(
        report("000001", "平安银行", "1"),
        total_market_cap="2159.88亿元",
        index_names=("沪深300", "机器人产业"),
    )
    message = format_digest([first, report("000002", "万科A", "2")])

    assert message.startswith("巨潮财报监控：新发布 2 家")
    assert "2026年半年度报告" in message
    assert "披露日期：2026-08-05" in message
    assert "总市值：2159.88亿元" in message
    assert "总市值：暂无数据" in message
    assert "指数：沪深300、机器人产业" in message
    assert message.count("指数：") == 1
    assert "https://static.cninfo.com.cn/1.PDF" in message


def test_format_digest_pages_are_byte_bounded_numbered_and_complete():
    reports = [
        report(f"{index:06d}", f"公司{index}", str(index))
        for index in range(1, 37)
    ]

    pages = monitor.format_digest_pages(reports, max_bytes=1900)

    assert len(pages) > 1
    for page_number, page in enumerate(pages, start=1):
        assert len(page.encode("utf-8")) <= 1900
        assert f"第 {page_number}/{len(pages)} 条" in page
        assert "本条" in page
    combined = "\n".join(pages)
    for item in reports:
        assert combined.count(f"{item.sec_name}（{item.sec_code}）") == 1


def test_run_monitor_sends_all_digest_pages_before_recording_state(tmp_path):
    state = JsonStateStore(tmp_path / "state.json")
    run_monitor([], state, sender=lambda _: True, bootstrap_silently=True)
    reports = [
        report(f"{index:06d}", f"公司{index}", str(index))
        for index in range(1, 37)
    ]
    calls = []

    result = run_monitor(
        reports,
        state,
        sender=lambda message: calls.append(message) or True,
        bootstrap_silently=True,
    )

    assert result.notification_sent is True
    assert len(calls) > 1
    assert all(len(message.encode("utf-8")) <= 1900 for message in calls)
    assert state.known_keys() == {item.company_report_key for item in reports}


def test_market_is_part_of_hong_kong_key_but_mainland_legacy_key_is_preserved():
    mainland = report("00700", "境内公司", "mainland")
    hong_kong = report("00700", "腾讯控股", "hong-kong", market="hong_kong")

    assert mainland.company_report_key == "2026:interim:00700"
    assert hong_kong.company_report_key == "hong_kong:2026:interim:00700"


def test_new_market_is_silently_baselined_without_hiding_mainland_updates(tmp_path):
    state = JsonStateStore(tmp_path / "state.json")
    old_mainland = report("000001", "平安银行", "old")
    run_monitor(
        [old_mainland],
        state,
        sender=lambda _: True,
        bootstrap_silently=True,
        markets={"mainland"},
    )
    new_mainland = report("000002", "万科A", "new")
    existing_hong_kong = report("00700", "腾讯控股", "hk", market="hong_kong")
    messages = []

    result = run_monitor(
        [old_mainland, new_mainland, existing_hong_kong],
        state,
        sender=lambda message: messages.append(message) or True,
        bootstrap_silently=True,
        markets={"mainland", "hong_kong"},
    )

    assert result.baselined == 1
    assert result.new_reports == (new_mainland,)
    assert "万科A" in messages[0]
    assert "腾讯控股" not in messages[0]
    assert state.initialized_markets() == {"mainland", "hong_kong"}


def test_version_one_state_is_treated_as_initialized_mainland(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "known_company_reports": ["2026:interim:000001"],
            }
        ),
        encoding="utf-8",
    )

    state = JsonStateStore(state_path)

    assert state.initialized_markets() == {"mainland"}
    assert state.known_keys() == {"2026:interim:000001"}
