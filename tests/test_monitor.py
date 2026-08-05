from __future__ import annotations

from cninfo_monitor.models import Report
from cninfo_monitor.monitor import JsonStateStore, format_digest, run_monitor


def report(code: str, name: str, announcement_id: str) -> Report:
    return Report(
        sec_code=code,
        sec_name=name,
        report_year=2026,
        report_type="interim",
        title="2026年半年度报告",
        disclosure_date="2026-08-05",
        announcement_id=announcement_id,
        pdf_url=f"https://static.cninfo.com.cn/{announcement_id}.PDF",
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
    message = format_digest(
        [
            report("000001", "平安银行", "1"),
            report("000002", "万科A", "2"),
        ]
    )

    assert message.startswith("巨潮财报监控：新发布 2 家")
    assert "2026年半年度报告" in message
    assert "披露日期：2026-08-05" in message
    assert "https://static.cninfo.com.cn/1.PDF" in message
