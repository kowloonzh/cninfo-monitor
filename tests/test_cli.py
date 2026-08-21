from __future__ import annotations

from cninfo_monitor import cli
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
    monkeypatch.setattr(cli, "fetch_reports", lambda config, end_date: [sample_report()])

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


def test_notify_existing_sends_initial_results_and_records_state(
    tmp_path, monkeypatch, capsys
):
    config_path = write_config(tmp_path)
    messages = []
    monkeypatch.setattr(cli, "fetch_reports", lambda config, end_date: [sample_report()])
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


def test_rolling_month_start_uses_previous_calendar_month_and_clamps_day():
    assert cli.rolling_month_start("2026-08-05") == "2026-07-05"
    assert cli.rolling_month_start("2026-03-31") == "2026-02-28"


def test_fetch_reports_combines_mainland_and_hong_kong(tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        cli,
        "query_all_announcements",
        lambda *args, **kwargs: [mainland_row, small_cap_row],
    )
    monkeypatch.setattr(cli, "query_hong_kong_announcements", lambda *args, **kwargs: [hong_kong_row])
    monkeypatch.setattr(
        cli,
        "load_index_memberships",
        lambda: {
            "mainland:000001": ("沪深300",),
            "hong_kong:00700": ("恒生科技",),
        },
    )

    reports = cli.fetch_reports(cli.load_monitor_config(config_path), "2026-08-12")

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


def test_no_new_reports_sends_daily_heartbeat(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path)
    messages = []
    monkeypatch.setattr(cli, "fetch_reports", lambda config, end_date: [sample_report()])
    monkeypatch.setattr(
        cli,
        "send_workwechat_text",
        lambda message, config: messages.append(message) or True,
    )
    args = [
        "run",
        "--config",
        str(config_path),
        "--end-date",
        "2026-08-06",
    ]

    assert cli.main(args) == 0
    assert messages == []
    assert cli.main(args) == 0

    assert len(messages) == 1
    assert messages[0] == "今日无新增财报"
    assert "心跳消息已发送" in capsys.readouterr().out


def test_failed_daily_heartbeat_returns_failure(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path)
    monkeypatch.setattr(cli, "fetch_reports", lambda config, end_date: [sample_report()])
    monkeypatch.setattr(cli, "send_workwechat_text", lambda message, config: False)
    args = [
        "run",
        "--config",
        str(config_path),
        "--end-date",
        "2026-08-06",
    ]

    assert cli.main(args) == 0
    assert cli.main(args) == 1

    assert "心跳消息发送失败" in capsys.readouterr().err
