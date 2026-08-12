from __future__ import annotations

import pytest

from cninfo_monitor.config import load_monitor_config


def test_load_monitor_config_resolves_paths_and_report_types(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
monitor:
  markets: [mainland, hong_kong]
  report_types:
    - interim
    - annual
  state_path: "../data/state.json"
  request_timeout: 20
""",
        encoding="utf-8",
    )

    config = load_monitor_config(config_path)

    assert config.report_types == frozenset({"interim", "annual"})
    assert config.markets == frozenset({"mainland", "hong_kong"})
    assert config.state_path == (tmp_path / "data/state.json").resolve()
    assert config.request_timeout == 20.0
    assert config.notify_when_no_updates is True


def test_load_monitor_config_rejects_unknown_report_type(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
monitor:
  report_types: [interim, mystery]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="mystery"):
        load_monitor_config(config_path)


def test_load_monitor_config_rejects_unknown_market(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("monitor:\n  markets: [mainland, mystery]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="mystery"):
        load_monitor_config(config_path)
