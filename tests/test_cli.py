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
