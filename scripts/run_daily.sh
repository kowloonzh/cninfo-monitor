#!/usr/bin/env bash
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
CONFIG_PATH="${CNINFO_CONFIG_PATH:-$PROJECT_DIR/config/config.yaml}"
LOCK_PATH="${CNINFO_LOCK_PATH:-/tmp/cninfo-monitor.lock}"
RUN_DATE="${1:-$(TZ=Asia/Shanghai date +%F)}"
LOG_DIR="$PROJECT_DIR/logs"
LOG_PATH="$LOG_DIR/monitor-$RUN_DATE.log"

mkdir -p "$LOG_DIR"
exec >>"$LOG_PATH" 2>&1

echo "[$(TZ=Asia/Shanghai date '+%F %T %Z')] cninfo-monitor start end_date=$RUN_DATE"

cd "$PROJECT_DIR" || exit 1
exec 9>"$LOCK_PATH"
if ! flock -n 9; then
  echo "[$(TZ=Asia/Shanghai date '+%F %T %Z')] another monitor job is running"
  exit 0
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "virtual environment missing: $PYTHON_BIN"
  exit 1
fi

"$PYTHON_BIN" -m cninfo_monitor.cli run \
  --config "$CONFIG_PATH" \
  --end-date "$RUN_DATE"
status=$?

echo "[$(TZ=Asia/Shanghai date '+%F %T %Z')] cninfo-monitor exit=$status"
exit "$status"
