from __future__ import annotations

import datetime as dt

import pytest

from cninfo_monitor import cli
from cninfo_monitor.cache import AnnouncementCache
from cninfo_monitor.cninfo import CNINFO_TIMEZONE
from cninfo_monitor.models import Report


def sample_report() -> Report:
    return Report(
        sec_code="000001",
        sec_name="平安银行",
        report_year=2026,
        report_type="interim",
        title="2026年半年度报告",
        disclosure_date="2026-08-05",
        announcement_id="1",
        pdf_url="https://static.cninfo.com.cn/1.PDF",
        market="mainland",
    )


def write_config(tmp_path) -> object:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
monitor:
  report_types: [interim]
  state_path: "state.json"
  cache_path: "announcements.db"
notifications:
  workwechat:
    enabled: true
    corp_id: corp
    corp_secret: secret
    agent_id: agent
""",
        encoding="utf-8",
    )
    return config_path


def test_run_dry_run_prints_reports_without_creating_state(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path)
    monkeypatch.setattr(
        cli,
        "fetch_reports",
        lambda config, end_time, cache: [sample_report()],
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--end-date",
            "2026-08-05",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert "平安银行（000001）" in capsys.readouterr().out
    assert not (tmp_path / "state.json").exists()
    assert not (tmp_path / "announcements.db").exists()


def test_notify_existing_sends_initial_results_and_records_state(
    tmp_path, monkeypatch, capsys
):
    config_path = write_config(tmp_path)
    messages = []
    monkeypatch.setattr(
        cli,
        "fetch_reports",
        lambda config, end_time, cache: [sample_report()],
    )
    monkeypatch.setattr(
        cli,
        "send_workwechat_text",
        lambda message, config: messages.append(message) or True,
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--end-date",
            "2026-08-05",
            "--notify-existing",
        ]
    )

    assert exit_code == 0
    assert len(messages) == 1
    assert (tmp_path / "state.json").exists()
    assert "已推送 1 家新财报" in capsys.readouterr().out


def test_fetch_reports_combines_cached_mainland_and_hong_kong(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "monitor:\n",
            "monitor:\n  markets: [mainland, hong_kong]\n",
        ),
        encoding="utf-8",
    )
    mainland_row = {
        "secCode": "000001",
        "secName": "平安银行",
        "announcementTitle": "2026年半年度报告",
        "announcementId": "mainland",
        "announcementTime": 1786464000000,
        "adjunctUrl": "mainland.PDF",
    }
    small_cap_row = {
        "secCode": "000002",
        "secName": "小市值公司",
        "announcementTitle": "2026年半年度报告",
        "announcementId": "small-cap",
        "announcementTime": 1786464000000,
        "adjunctUrl": "small-cap.PDF",
    }
    hong_kong_row = {
        "secCode": "00700",
        "secName": "腾讯控股",
        "announcementTitle": "截至二零二六年六月三十日止六个月业绩公布",
        "announcementId": "hong-kong",
        "announcementTime": 1786464000000,
        "adjunctUrl": "hong-kong.PDF",
    }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            mainland_fields = [""] * 46
            mainland_fields[45] = "2159.88"
            hong_kong_fields = [""] * 46
            hong_kong_fields[45] = "40508.85"
            small_cap_fields = [""] * 46
            small_cap_fields[45] = "99.99"
            body = (
                f'v_sz000001="{"~".join(mainland_fields)}";\n'
                f'v_sz000002="{"~".join(small_cap_fields)}";\n'
                f'v_hk00700="{"~".join(hong_kong_fields)}";\n'
            ).encode("gbk")

            class FakeResponse:
                content = body

                def raise_for_status(self):
                    return None

            return FakeResponse()

    monkeypatch.setattr(cli.httpx, "Client", FakeClient)
    query_calls = []

    def query_announcements(*args, **kwargs):
        query_calls.append(kwargs.copy())
        if kwargs["market"] == "mainland":
            return [mainland_row, small_cap_row]
        return [hong_kong_row]

    monkeypatch.setattr(cli, "query_announcements_by_time", query_announcements)
    monkeypatch.setattr(
        cli,
        "load_index_memberships",
        lambda: {
            "mainland:000001": ("沪深300",),
            "hong_kong:00700": ("恒生科技",),
        },
    )

    config = cli.load_monitor_config(config_path)
    cache = AnnouncementCache(config.cache_path)
    end_time = dt.datetime(2026, 8, 12, 9, 0, tzinfo=CNINFO_TIMEZONE)
    reports = cli.fetch_reports(config, end_time, cache)

    assert [(report.sec_code, report.market) for report in reports] == [
        ("00700", "hong_kong"),
        ("000001", "mainland"),
    ]
    assert [report.total_market_cap for report in reports] == [
        "40508.85亿港元",
        "2159.88亿元",
    ]
    assert [report.index_names for report in reports] == [
        ("恒生科技",),
        ("沪深300",),
    ]
    assert {call["market"] for call in query_calls} == {"mainland", "hong_kong"}
    for market in ("mainland", "hong_kong"):
        market_calls = [call for call in query_calls if call["market"] == market]
        assert len(market_calls) == (9 if market == "mainland" else 3)
        assert market_calls[0]["start_time"] == dt.datetime(
            2026, 8, 10, 0, 0, tzinfo=CNINFO_TIMEZONE
        )
        assert market_calls[-1]["end_time"] == end_time
        assert all(call["known_announcement_ids"] == set() for call in market_calls)
        assert {call["plate"] for call in market_calls} == (
            {"sz", "sh", "bj"} if market == "mainland" else {""}
        )
    assert cache.fetched_through("mainland") == end_time
    assert cache.processed_through("mainland") == end_time - dt.timedelta(hours=48)


def test_fetch_reports_uses_saved_cursor_with_overlap(tmp_path, monkeypatch):
    config = cli.load_monitor_config(write_config(tmp_path))
    cache = AnnouncementCache(config.cache_path)
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(cli.httpx, "Client", FakeClient)
    monkeypatch.setattr(
        cli,
        "query_announcements_by_time",
        lambda *args, **kwargs: calls.append(kwargs.copy()) or [],
    )
    first_end = dt.datetime(2026, 8, 23, 9, 0, tzinfo=CNINFO_TIMEZONE)

    assert cli.fetch_reports(config, first_end, cache) == []
    cache.mark_processed(config.markets, first_end)
    assert cli.fetch_reports(config, first_end + dt.timedelta(hours=1), cache) == []

    assert len(calls) == 12
    assert calls[0]["start_time"] == dt.datetime(
        2026, 8, 21, 0, 0, tzinfo=CNINFO_TIMEZONE
    )
    assert calls[9]["start_time"] == dt.datetime(
        2026, 8, 23, 0, 0, tzinfo=CNINFO_TIMEZONE
    )
    assert calls[-1]["end_time"] == first_end + dt.timedelta(hours=1)


def test_failed_market_scan_preserves_its_cursor_after_other_market_succeeds(
    tmp_path, monkeypatch
):
    config_path = write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "monitor:\n",
            "monitor:\n  markets: [mainland, hong_kong]\n",
        ),
        encoding="utf-8",
    )
    config = cli.load_monitor_config(config_path)
    cache = AnnouncementCache(config.cache_path)

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def query(*args, **kwargs):
        if kwargs["market"] == "hong_kong":
            raise RuntimeError("temporary 504")
        return []

    monkeypatch.setattr(cli.httpx, "Client", FakeClient)
    monkeypatch.setattr(cli, "query_announcements_by_time", query)

    with pytest.raises(RuntimeError, match="504"):
        cli.fetch_reports(
            config,
            dt.datetime(2026, 8, 23, 9, 0, tzinfo=CNINFO_TIMEZONE),
            cache,
        )

    assert cache.fetched_through("mainland") == dt.datetime(
        2026, 8, 23, 9, 0, tzinfo=CNINFO_TIMEZONE
    )
    assert cache.fetched_through("hong_kong") is None


def test_refresh_indexes_command_writes_requested_cache(tmp_path, monkeypatch, capsys):
    output_path = tmp_path / "index-memberships.json"
    calls = []
    monkeypatch.setattr(
        cli,
        "refresh_official_index_cache",
        lambda path: calls.append(path)
        or {
            "sources": [
                {"constituent_count": count}
                for count in (300, 500, 1000, 2000, 30, 50, 50, 50)
            ],
            "memberships": {"mainland:000001": ["沪深300"]},
        },
    )

    exit_code = cli.main(
        ["refresh-indexes", "--output", str(output_path)]
    )

    assert exit_code == 0
    assert calls == [output_path]
    assert "8 个指数" in capsys.readouterr().out


def test_no_new_reports_sends_one_daily_heartbeat_at_configured_hour(
    tmp_path, monkeypatch, capsys
):
    config_path = write_config(tmp_path)
    messages = []
    monkeypatch.setattr(
        cli,
        "fetch_reports",
        lambda config, end_time, cache: [sample_report()],
    )
    monkeypatch.setattr(
        cli,
        "send_workwechat_text",
        lambda message, config: messages.append(message) or True,
    )
    args = [
        "run",
        "--config",
        str(config_path),
        "--end-time",
        "2026-08-06 20:00:00",
    ]

    assert cli.main(args) == 0
    assert messages == []
    assert cli.main(args) == 0
    assert messages == []

    args[-1] = "2026-08-06 21:00:00"
    assert cli.main(args) == 0
    assert cli.main(args) == 0

    assert len(messages) == 1
    assert messages[0] == "今日无新增财报"
    assert "心跳消息已发送" in capsys.readouterr().out


def test_failed_daily_heartbeat_returns_failure(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path)
    monkeypatch.setattr(
        cli,
        "fetch_reports",
        lambda config, end_time, cache: [sample_report()],
    )
    monkeypatch.setattr(cli, "send_workwechat_text", lambda message, config: False)
    args = [
        "run",
        "--config",
        str(config_path),
        "--end-time",
        "2026-08-06 21:00:00",
    ]

    assert cli.main(args) == 0
    assert cli.main(args) == 1

    assert "心跳消息发送失败" in capsys.readouterr().err


def test_fetch_failure_sends_workwechat_alert(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path)
    messages = []

    def fail_fetch(config, end_time, cache):
        raise RuntimeError("temporary 504")

    monkeypatch.setattr(cli, "fetch_reports", fail_fetch)
    monkeypatch.setattr(
        cli,
        "send_workwechat_text",
        lambda message, config: messages.append(message) or True,
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--end-time",
            "2026-08-23 09:00:00",
        ]
    )

    assert exit_code == 1
    assert len(messages) == 1
    assert "巨潮财报监控执行失败" in messages[0]
    assert "下一小时自动重试" in messages[0]
    assert "temporary 504" in capsys.readouterr().err


def test_failed_report_notification_does_not_advance_processing_cursor(
    tmp_path, monkeypatch
):
    config_path = write_config(tmp_path)
    start = dt.datetime(2026, 8, 23, 8, 0, tzinfo=CNINFO_TIMEZONE)
    end = dt.datetime(2026, 8, 23, 9, 0, tzinfo=CNINFO_TIMEZONE)

    def fetch_with_cursor(config, end_time, cache):
        cache.store_scans(
            {"mainland": []},
            scan_starts={"mainland": start},
            scanned_through=end_time,
        )
        return [sample_report()]

    monkeypatch.setattr(cli, "fetch_reports", fetch_with_cursor)
    monkeypatch.setattr(cli, "send_workwechat_text", lambda message, config: False)

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--end-time",
            "2026-08-23 09:00:00",
            "--notify-existing",
        ]
    )

    cache = AnnouncementCache(tmp_path / "announcements.db")
    assert exit_code == 1
    assert cache.fetched_through("mainland") == end
    assert cache.processed_through("mainland") == start
