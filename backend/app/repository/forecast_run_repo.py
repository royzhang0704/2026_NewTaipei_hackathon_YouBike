# ════════════════════════════════════════════════════════════
# forecast_run_repo —— hackathon_backend_forecast_run 的讀寫
#
#   預測主檔：**一站一個半小時（= 一次預測）一列**，冪等判定的唯一依據。
#   forecast_history（run_id）是它的明細、risk_snapshot（run_id）是它的
#   風險判定結果。
#
# ★ 與 job_run_repo 的分工（兩張表都要寫）：
#     job_run       執行過程 —— 每次執行一列，含失敗與 running 殘列
#     forecast_run  完成狀態 —— 一站一時段一列，回答「做完了沒」
#
# ★ 兩階段分開：predict_status='done' 但 risk_done_at IS NULL
#   = 預測有了、風險還沒判。TRUNCATE risk_snapshot + 清 risk_done_at
#   就能只重算風險不打 SageMaker，主檔的預測那半邊一列不動。
#
# ★ skipped 不擋重試（只有 done 擋）：STALE_ANCHOR 是「本輪這站沒有新鮮
#   資料」，下一次重跑同 origin 時資料可能已經補到了，該再試一次。
#   skip_reason 留著是為了讓「處理過但沒打」看得見。
#
# 冒煙：uv run python -m app.repository.forecast_run_repo
# ════════════════════════════════════════════════════════════
from datetime import datetime

from app.repository.db import get_conn

# 只認這四個狀態（打錯字不報錯，只會讓冪等安靜失效）
STATUSES = ("running", "done", "skipped", "failed")

_UPSERT_COLS = ("station_uid", "origin", "predict_status", "predicted_at",
                "model_job", "skip_reason", "slots")


def get(station_uid: str, origin: datetime) -> dict | None:
    with get_conn().cursor() as cur:
        cur.execute("SELECT * FROM hackathon_backend_forecast_run "
                    " WHERE station_uid = %s AND origin = %s", (station_uid, origin))
        return cur.fetchone()


def done_uids(origin: datetime, model_job: str) -> set[str]:
    """站層冪等：這個 origin 已經打完、且是**同一個模型**打的站。

    ★ model_job 一起比 —— 換了模型的預測不能跟舊模型的混在同一個 origin
      （cat 對照是訓練時的字典序決定的，混了不會報錯只會全錯）。
    """
    with get_conn().cursor() as cur:
        cur.execute("SELECT station_uid FROM hackathon_backend_forecast_run "
                    " WHERE origin = %s AND predict_status = 'done' "
                    "   AND model_job = %s", (origin, model_job))
        return {r["station_uid"] for r in cur.fetchall()}


def upsert_many(rows: list[tuple]) -> dict[str, int]:
    """整輪寫主檔（含 skipped 的站），回 {station_uid: id} 供明細掛 run_id。

    rows 逐筆＝ _UPSERT_COLS 的順序。同 (站, origin) 重跑走 upsert 覆蓋。
    """
    if not rows:
        return {}
    ph = ", ".join(["%s"] * len(_UPSERT_COLS))
    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in _UPSERT_COLS[2:])
    origin = rows[0][1]
    with get_conn().transaction(), get_conn().cursor() as cur:
        cur.executemany(
            f"INSERT INTO hackathon_backend_forecast_run ({', '.join(_UPSERT_COLS)}) "
            f"VALUES ({ph}) "
            f"ON CONFLICT (station_uid, origin) DO UPDATE SET {upd}, updated_at = now()",
            rows)
    with get_conn().cursor() as cur:
        cur.execute("SELECT id, station_uid FROM hackathon_backend_forecast_run "
                    " WHERE origin = %s", (origin,))
        return {r["station_uid"]: r["id"] for r in cur.fetchall()}


def mark_risk(origin: datetime, algo_ver: str) -> int:
    """階段二收尾：把該 origin 全部主檔列蓋上 risk_done_at（= 判過的標記）。"""
    with get_conn().cursor() as cur:
        cur.execute("UPDATE hackathon_backend_forecast_run "
                    "   SET risk_done_at = now(), risk_algo_ver = %s, "
                    "       updated_at = now() WHERE origin = %s", (algo_ver, origin))
        return cur.rowcount


def risk_done(origin: datetime, algo_ver: str) -> bool:
    """冪等判定第二層：這一輪風險判過了嗎？

    定義成「該 origin 沒有任何一列還沒判（或版本過期）」——
    主檔沒有 batch 層的列了，整輪狀態一律用聚合表達。
    """
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT count(*) AS total, "
            "       count(*) FILTER (WHERE risk_done_at IS NULL "
            "                          OR risk_algo_ver IS DISTINCT FROM %s) AS pending "
            "  FROM hackathon_backend_forecast_run WHERE origin = %s", (algo_ver, origin))
        r = cur.fetchone()
        return r["total"] > 0 and r["pending"] == 0


def latest_risk_origin(at: datetime) -> datetime | None:
    """/alerts 的錨點：最新一輪「風險判過」的 origin。

    ★ 必須夾 at（= effective_now）：demo 回放時表裡可能躺著整段未來的列，
      不夾上界畫面永遠停在最末一輪，虛擬時鐘怎麼走都不動
      （理由同 forecast_repo.latest_origin 的 9/1 修正）。
    """
    with get_conn().cursor() as cur:
        cur.execute("SELECT max(origin) AS o FROM hackathon_backend_forecast_run "
                    " WHERE risk_done_at IS NOT NULL AND origin <= %s", (at,))
        return cur.fetchone()["o"]


def pending_origins(algo_ver: str, limit: int = 100) -> list[datetime]:
    """預測完成但風險還沒判（或版本過期）的 origin，**由舊到新**。

    ★ 由舊到新是必要的 —— streak 靠上一輪遞推，跳著補會斷。
      給 jobs/batch_predict --risk-only 的回填用。
    """
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT origin FROM hackathon_backend_forecast_run "
            " WHERE predict_status = 'done' "
            "   AND (risk_done_at IS NULL OR risk_algo_ver IS DISTINCT FROM %s) "
            " GROUP BY origin ORDER BY origin LIMIT %s", (algo_ver, limit))
        return [r["origin"] for r in cur.fetchall()]


def round_stats(origin: datetime) -> dict:
    """該 origin 的整輪狀態（主檔沒有 batch 列，用聚合表達）。"""
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT count(*) AS stations, "
            "  count(*) FILTER (WHERE predict_status = 'done') AS done, "
            "  count(*) FILTER (WHERE predict_status = 'skipped') AS skipped, "
            "  count(*) FILTER (WHERE risk_done_at IS NOT NULL) AS risk_done, "
            "  min(model_job) AS model_job, max(risk_algo_ver) AS algo_ver, "
            "  COALESCE(sum(slots), 0) AS slots "
            "  FROM hackathon_backend_forecast_run WHERE origin = %s", (origin,))
        return cur.fetchone()


# ════════════════════════════════════════════════════════════
# 冒煙：uv run python -m app.repository.forecast_run_repo
#   用 1999 年的假 origin + 真站號，跑完自己清掉。
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    O = datetime(1999, 1, 1, 0, 0)
    JOB, VER = "__smoke_job__", "__smoke_ver__"
    with get_conn().cursor() as c:
        c.execute("SELECT station_uid FROM hackathon_backend_station "
                  "ORDER BY station_uid LIMIT 3")
        u = [r["station_uid"] for r in c.fetchall()]

    print("── ① upsert 三站：兩站 done、一站 skipped，回 id map")
    ids = upsert_many([(u[0], O, "done", datetime.now(), JOB, None, 6),
                       (u[1], O, "done", datetime.now(), JOB, None, 6),
                       (u[2], O, "skipped", None, JOB, "STALE_ANCHOR", 0)])
    assert set(ids) == set(u) and all(isinstance(v, int) for v in ids.values())
    print(f"   id map {ids} ✓")

    print("── ② 站層冪等：done 的擋、skipped 的不擋（資料補到了要再試）")
    d = done_uids(O, JOB)
    assert d == {u[0], u[1]}, d
    assert done_uids(O, "other-model") == set(), "換模型必須重打"
    print(f"   done_uids={len(d)} 站（不含 skipped 的 {u[2]}）／換模型=0 ✓")

    print("── ③ 風險未判：risk_done=False，pending_origins 撈得到")
    assert risk_done(O, VER) is False
    assert O in pending_origins(VER)
    assert latest_risk_origin(O) != O
    print("   risk_done=False／在待判清單／不是 latest_risk_origin ✓")

    print("── ④ mark_risk → 判過了，換版本又變回未判")
    n = mark_risk(O, VER)
    assert n == 3 and risk_done(O, VER) is True
    assert risk_done(O, "time-v2/pct20") is False, "換演算法版本必須重判"
    assert latest_risk_origin(O) == O
    print(f"   蓋章 {n} 列／risk_done=True／換版=False ✓")

    print("── ⑤ round_stats（整輪狀態用聚合表達）")
    s = round_stats(O)
    assert s["stations"] == 3 and s["done"] == 2 and s["skipped"] == 1
    assert s["risk_done"] == 3 and s["slots"] == 12
    print(f"   {s['stations']} 站／done {s['done']}／skipped {s['skipped']}／"
          f"已判 {s['risk_done']}／格數 {s['slots']} ✓")

    print("── ⑥ upsert 覆蓋（同 (站,origin) 重寫不新增列、id 不變）")
    ids2 = upsert_many([(u[2], O, "done", datetime.now(), JOB, None, 6)])
    assert ids2[u[2]] == ids[u[2]] and round_stats(O)["stations"] == 3
    print(f"   id 仍是 {ids[u[2]]}、列數仍 3 ✓")

    with get_conn().cursor() as cur:
        cur.execute("DELETE FROM hackathon_backend_forecast_run WHERE origin = %s", (O,))
        print(f"\n✓ 全部通過，已清除 {cur.rowcount} 筆測試資料")
