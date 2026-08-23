from __future__ import annotations

import os
import subprocess
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_installs_hourly_monitor_run_and_preserves_other_jobs(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_path = tmp_path / "crontab"
    state_path.write_text("15 7 * * * /other/job\n", encoding="utf-8")
    fake_crontab = fake_bin / "crontab"
    fake_crontab.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ ${1:-} == -l ]]; then\n"
        "  cat \"$FAKE_CRONTAB_STATE\"\n"
        "else\n"
        "  cp \"$1\" \"$FAKE_CRONTAB_STATE\"\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_crontab.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_CRONTAB_STATE"] = str(state_path)

    subprocess.run(
        [str(PROJECT_DIR / "scripts/install_cron.sh")],
        cwd=PROJECT_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    installed = state_path.read_text(encoding="utf-8")
    installed_lines = installed.splitlines()
    daily_script = PROJECT_DIR / "scripts/run_daily.sh"
    assert "15 7 * * * /other/job" in installed
    assert f"0 * * * * TZ=Asia/Shanghai {daily_script}" in installed_lines


def test_daily_script_does_not_replace_current_time_with_end_of_day():
    script = (PROJECT_DIR / "scripts/run_daily.sh").read_text(encoding="utf-8")

    assert '--end-date "$RUN_DATE"' not in script
    assert 'COMMAND+=(--end-time "$RUN_END_TIME")' in script
