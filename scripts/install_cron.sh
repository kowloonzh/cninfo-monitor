#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DAILY_SCRIPT="$PROJECT_DIR/scripts/run_daily.sh"
SCHEDULE="${CNINFO_CRON_SCHEDULE:-30 18 * * *}"
BEGIN_MARKER="# BEGIN cninfo-monitor"
END_MARKER="# END cninfo-monitor"
TEMP_DIR="$(mktemp -d)"
CURRENT_CRON="$TEMP_DIR/current.cron"
NEW_CRON="$TEMP_DIR/new.cron"

cleanup() {
  rm -f "$CURRENT_CRON" "$NEW_CRON"
  rmdir "$TEMP_DIR"
}
trap cleanup EXIT

crontab -l >"$CURRENT_CRON" 2>/dev/null || true
awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
  $0 == begin { skip = 1; next }
  $0 == end { skip = 0; next }
  !skip { print }
' "$CURRENT_CRON" >"$NEW_CRON"

{
  echo "$BEGIN_MARKER"
  echo "$SCHEDULE TZ=Asia/Shanghai $DAILY_SCRIPT"
  echo "$END_MARKER"
} >>"$NEW_CRON"

crontab "$NEW_CRON"
echo "已安装 cninfo-monitor 定时任务：$SCHEDULE（cron 守护进程本地时区）"
