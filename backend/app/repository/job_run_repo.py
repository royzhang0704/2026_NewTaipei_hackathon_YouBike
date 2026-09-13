# ════════════════════════════════════════════════════════════
# job_run_repo —— hackathon_backend_job_run 的讀寫（Job A′ / Job B 共用）
#
# 規則（計劃-TDX排程與初始化.md §3「Job 歷程落地」）：
#   開跑先 insert（status='running'）→ 結束 update。
#   ★ start() 必須「立刻可見」—— db.get_conn() 是 autocommit，
#     insert 完就 commit 了。所以 process 中途 crash 會留下一列
#     status='running' / finished_at IS NULL，下一輪看到即知上輪中斷。
#     這是刻意的診斷訊號，不要包進 Job 的交易裡（一起 rollback 就沒了），
#     也不要寫排程去清它。
#
# 用法：
#   rid = job_run_repo.start("pull_replay", slot)
#   try:
#       ... 做事 ...
#       job_run_repo.finish(rid, "success", stations_ok=n)
#   except Exception as e:
#       job_run_repo.finish(rid, "failed", error=str(e)); raise
#
# 冒煙：uv run python -m app.repository.job_run_repo
# ════════════════════════════════════════════════════════════
from datetime import date, datetime

from psycopg.types.json import Json

from app.repository.db import get_conn

# 只認這四個狀態。打錯字不會報錯只會讓對帳查詢安靜漏列，
# 所以在寫入端就擋掉 —— DB 沒設 CHECK（改狀態集要動 DDL 太重）。
STATUSES = ("running", "success", "skipped", "failed")

# error 只放一句話摘要，完整 traceback 留在 backend/logs/ 的 log 檔。
# 超長就截斷 —— 讓 psql 查歷程時一列還能看。
ERROR_MAX = 500


def start(job_name: str, slot: datetime | None = None) -> int:
    """開跑：insert 一列 status='running'，回傳 id（autocommit，立即可見）。"""
    with get_conn().cursor() as cur:
        cur.execute(
            "INSERT INTO hackathon_backend_job_run (job_name, slot) "
            "VALUES (%s, %s) RETURNING id", (job_name, slot))
        return cur.fetchone()["id"]


def finish(run_id: int, status: str,
           stations_ok: int | None = None,
           rows_written: int | None = None,
           bytes_in: int | None = None,
           error: str | None = None,
           detail: dict | None = None) -> None:
    """收尾：update 狀態與統計。未給的欄位保持 NULL，不覆寫成 0。

    status: success / skipped / failed（'running' 只有 start() 用）
    error : 一句話原因摘要，超過 ERROR_MAX 截斷
    detail: jsonb 雜項統計（重試次數／invoke 批數／http status…）
    """
    if status not in STATUSES:
        raise ValueError(f"status 只能是 {STATUSES}，收到 {status!r}")
    if error is not None and len(error) > ERROR_MAX:
        error = error[:ERROR_MAX] + "…"
    with get_conn().cursor() as cur:
        cur.execute(
            "UPDATE hackathon_backend_job_run "
            "   SET finished_at = now(), status = %s, stations_ok = %s, "
            "       rows_written = %s, bytes_in = %s, error = %s, detail = %s "
            " WHERE id = %s",
            (status, stations_ok, rows_written, bytes_in, error,
             Json(detail) if detail is not None else None, run_id))
        if cur.rowcount != 1:
            # id 不存在＝呼叫端拿錯 id，靜靜吞掉會讓歷程永遠停在 running
            raise ValueError(f"job_run id={run_id} 不存在，沒有列被更新")


def last(job_name: str, n: int = 5) -> list[dict]:
    """查某個 job 最近 n 輪（走 (job_name, started_at) 索引）。"""
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT id, job_name, slot, started_at, finished_at, status, "
            "       stations_ok, rows_written, bytes_in, error, detail "
            "  FROM hackathon_backend_job_run "
            " WHERE job_name = %s ORDER BY started_at DESC LIMIT %s", (job_name, n))
        return cur.fetchall()


def stale_running(job_name: str | None = None) -> list[dict]:
    """撈中斷殘列（status='running' 且 finished_at IS NULL）。

    下一輪開跑時呼叫，看到就知道上一輪 process 被 kill/crash。
    不自動清理 —— 清掉就沒有中斷的證據了。
    """
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT id, job_name, slot, started_at "
            "  FROM hackathon_backend_job_run "
            " WHERE status = 'running' AND finished_at IS NULL "
            "   AND (%s::text IS NULL OR job_name = %s) "
            " ORDER BY started_at", (job_name, job_name))
        return cur.fetchall()


def runs_on_day(job_name: str, day: "date") -> list[dict]:
    """某個 job 在指定「台北日」的所有執行列（新→舊）。

    ★ started_at 是 timestamptz，day 是台北日 —— 必須先 AT TIME ZONE
      轉台北再比。直接拿 date(started_at) 比會用 DB 的 TimeZone 設定，
      跨日那兩小時會安靜地歸錯天（Job C 的「今日只跑一次」就會失效）。

    給 Job C 的判定 a（今日尚未有 success）與 b（失敗退避）用。
    只掃 (job_name, started_at) 索引的一小段，是最便宜的一道判定。
    """
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT id, status, started_at, finished_at, error, detail "
            "  FROM hackathon_backend_job_run "
            " WHERE job_name = %s "
            "   AND (started_at AT TIME ZONE 'Asia/Taipei')::date = %s "
            # ★ id DESC 是必要的 tie-breaker：started_at 預設 now()，
            #   而 now() 在同一交易裡是常數 —— 同交易寫的兩列時間會完全相同，
            #   只靠 started_at 排序時「最後一次」是隨機的。
            " ORDER BY started_at DESC, id DESC", (job_name, day))
        return cur.fetchall()


# ★★ 2026-09-04：month_usage()（本月 TDX 用量與估算點數）已移除 ——
#   TDX 拉取邏輯全部清掉，沒有東西會呼叫它，config.TDX_RATE /
#   TDX_JOB_CATEGORY 也一併不在了。出處：
#   meet/20260904/計劃-移除TDX拉取邏輯.md §1-6。
#   ⚠ job_run 表本身保留：replay_pull 每輪記一列，bytes_in 留 NULL。


# ════════════════════════════════════════════════════════════
# 冒煙：uv run python -m app.repository.job_run_repo
#   ① insert→update 一輪  ② 故意不 finish 一筆，驗殘列查得到
#   跑完會自己清掉這兩筆測試資料（job_name='__smoke__'）。
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    from datetime import timedelta

    JOB = "__smoke__"
    slot = datetime.now().replace(minute=0 if datetime.now().minute < 30 else 30,
                                  second=0, microsecond=0)

    print("── ① 正常一輪 insert → update")
    rid = start(JOB, slot)
    print(f"   start() → id={rid}")
    row = last(JOB, 1)[0]
    assert row["status"] == "running" and row["finished_at"] is None, row
    print(f"   狀態 {row['status']} / finished_at {row['finished_at']} ✓")

    finish(rid, "success", stations_ok=1594, rows_written=3188, bytes_in=41234,
           detail={"retries": 0, "http": 200})
    row = last(JOB, 1)[0]
    assert row["status"] == "success" and row["finished_at"] is not None
    assert row["detail"] == {"retries": 0, "http": 200}, row["detail"]
    print(f"   finish() → status={row['status']} stations_ok={row['stations_ok']} "
          f"rows={row['rows_written']} bytes_in={row['bytes_in']} detail={row['detail']} ✓")

    print("\n── ② 故意不 finish：模擬 process 被 kill")
    orphan = start(JOB, slot + timedelta(minutes=30))
    stale = [r for r in stale_running(JOB)]
    assert any(r["id"] == orphan for r in stale), stale
    print(f"   stale_running() 查到 {len(stale)} 筆殘列，含 id={orphan} ✓")

    print("\n── ③ 防呆")
    try:
        finish(rid, "done")
    except ValueError as e:
        print(f"   狀態打錯字 → ValueError: {e}")
    try:
        finish(-1, "success")
    except ValueError as e:
        print(f"   id 不存在 → ValueError: {e}")
    finish(orphan, "failed", error="X" * 900)
    assert len(last(JOB, 1)[0]["error"]) == ERROR_MAX + 1
    print(f"   error 超長 → 截斷成 {ERROR_MAX} 字 + …")

    with get_conn().cursor() as cur:
        cur.execute("DELETE FROM hackathon_backend_job_run WHERE job_name = %s", (JOB,))
        print(f"\n✓ 全部通過，已清除 {cur.rowcount} 筆測試資料")
