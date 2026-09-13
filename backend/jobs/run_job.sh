#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════
# run_job.sh —— cron 與人工共用的 job wrapper
#
# 為什麼要這層而不是直接把 uv 指令寫進 crontab：
#   1. ★ cron 的 PATH 只有 /usr/bin:/bin —— 直接寫 `uv` 會 command not found，
#      而且 cron 不會告訴你，只會安靜地什麼都沒發生。這裡用絕對路徑。
#   2. cron 的工作目錄是 $HOME，不是專案目錄。uv 要在 backend/ 才找得到
#      pyproject.toml 與 .venv。
#   3. stdout/stderr 要落檔才查得到「上一輪到底發生什麼事」——
#      job_run 表記結果，log 記過程，兩者互補。
#   4. 上一輪還沒跑完就再開一輪 = 同 slot 兩列 job_run。加一道鎖擋掉。
#
# 用法：
#   bash backend/jobs/run_job.sh pull_realtime            # 一輪 Job A（會觸發 Job B）
#   bash backend/jobs/run_job.sh batch_predict --limit 50 # 額外參數原樣往後傳
#   bash backend/jobs/run_job.sh sync_stations
#   bash backend/jobs/run_job.sh backfill --dry-run   # Job C（不真打）
#
# log：backend/logs/<job>-YYYY-MM-DD.log（當日累積，保留 14 天）
# ════════════════════════════════════════════════════════════
set -uo pipefail

JOB="${1:-}"
[ -n "$JOB" ] || { echo "用法：run_job.sh <job 名稱> [額外參數…]"; exit 2; }
shift

BACKEND="$(cd "$(dirname "$0")/.." && pwd)"     # backend/
LOG_DIR="$BACKEND/logs"
LOG="$LOG_DIR/${JOB}-$(date +%F).log"
LOCK="$LOG_DIR/.${JOB}.lock"
KEEP_DAYS=14

# ★ uv 絕對路徑：cron 的 PATH 不含 /opt/homebrew/bin。
#   允許用環境變數覆寫，換機器時不必改腳本。
UV="${UV_BIN:-/opt/homebrew/bin/uv}"
[ -x "$UV" ] || UV="$(command -v uv 2>/dev/null || echo "$UV")"

mkdir -p "$LOG_DIR"

# ── 鎖：mkdir 是原子操作，比 [ -f ] + touch 可靠 ──
#   殘留鎖（上次被 kill）用 PID 判斷：那個 PID 不在了就接手。
if ! mkdir "$LOCK" 2>/dev/null; then
  OLD="$(cat "$LOCK/pid" 2>/dev/null || echo "")"
  if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
    echo "$(date '+%F %T') [$JOB] 上一輪 (pid $OLD) 還在跑，本輪跳過" >> "$LOG"
    exit 0
  fi
  echo "$(date '+%F %T') [$JOB] 清除殘留鎖 (pid ${OLD:-?} 已不存在)" >> "$LOG"
  rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || exit 1
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

# ── 執行 ──
# ★ 先收到暫存檔，「有輸出或失敗」才寫 log。
#   tick 每分鐘跑一次，什麼都沒做時完全安靜 —— 否則 log 一天多 2,880 行
#   「開始/結束」的廢話，真正有事的那幾行就被淹掉了。
#   「排程還活著嗎」看 sys_config.last_tick，不看 log。
OUT="$(mktemp "${TMPDIR:-/tmp}/run_job.XXXXXX")"
trap 'rm -rf "$LOCK"; rm -f "$OUT"' EXIT

cd "$BACKEND" || exit 1
"$UV" run python -m "jobs.$JOB" "$@" > "$OUT" 2>&1
RC=$?

if [ -s "$OUT" ] || [ "$RC" -ne 0 ]; then
  {
    echo "════ $(date '+%F %T %Z') 開始 $JOB $* ════"
    cat "$OUT"
    echo "════ $(date '+%F %T') 結束 $JOB rc=$RC ════"
    echo
  } >> "$LOG"
fi

# ── 清舊 log（14 天前）──
find "$LOG_DIR" -name '*.log' -type f -mtime "+$KEEP_DAYS" -delete 2>/dev/null

exit $RC
