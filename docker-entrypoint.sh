#!/usr/bin/env bash
set -euo pipefail

if [ "${RUN_TICK:-0}" = "1" ]; then
  echo "[entrypoint] tick 迴圈啟用（每 ${TICK_INTERVAL:-60}s）"
  (
    while true; do
      # tick 自己會判定該不該做事；什麼都不做時不印任何東西
      python -m jobs.tick || echo "[tick] 這一輪失敗，繼續下一輪"
      sleep "${TICK_INTERVAL:-60}"
    done
  ) &
else
  echo "[entrypoint] tick 迴圈未啟用（設 RUN_TICK=1 開啟）"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
