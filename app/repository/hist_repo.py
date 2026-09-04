# ════════════════════════════════════════════════════════════
# hist_repo —— /predict 的資料品質標註（level30 的 is_imputed 計數）
#
# ★★ 2026-09-04：本檔從 560 行縮到現在這樣。
#   原本是「歷史 API CSV 的落地：守門 → 30 分重採樣 → 補進兩張表」，
#   隨 TDX 拉取邏輯一起移除的有：
#     stage_csv / ingest        歷史 API CSV → staging → actual_history + level30
#     gap_report                缺格率（Job C 的判定 d）
#     impute_weekly             週期補值（Job A 每輪呼叫）
#     rebuild_level30           從 actual_history 重建 level30
#     _fill_level30             上面兩者共用的重採樣（carry ≤6 格）
#     STAGE / OBS 兩張 staging 表與三道守門的正規式
#   出處：meet/20260904/計劃-移除TDX拉取邏輯.md §1-4。
#   ⚠ 那些邏輯的權威出處仍在 ml-deepar/sql/baseline_create_import.sql 與
#     baseline_resample_export.sh —— 要重建 level30 請走
#     backend/sql/43_level30_carry.sql（baseline_grid → level30，冪等）。
#
# ★ 檔名沒改：predict_service 依這個名字 import。名不符實但改名會動到
#   呼叫端，換來的只有觀感。剩下的兩個函式做的是同一件事 ——
#   回答「/predict 讀到的 48 格裡，有幾格不是實測」。
#
# ★ 補值不在這裡做：值是落表的（level30.is_imputed），由 43 那支灌。
#   服務層再補一次就是第二份邏輯，兩邊遲早長歪。
#   本檔只負責「說清楚讀到的是什麼」。
#
# 用法：
#   n = hist_repo.imputed_count(uid, t0, t1)   # is_imputed = 1（週期補值）
#   n = hist_repo.carried_count(uid, t0, t1)   # is_imputed = 2（無限 carry）
# ════════════════════════════════════════════════════════════
from app.repository.db import get_conn


def imputed_count(station_uid: str, t0, t1) -> int:
    """某站某區間（含頭含尾）有幾格是週期補值 —— 給 /predict 誠實標註用。

    ★ 只算 is_imputed = 1。無限 carry（=2）走 carried_count()，兩個數字
      刻意分開報：週期補值還有日內節律，長 carry 沒有 —— 07-11 那天
      全市幾乎整天是同一個數字，合報會把這個可信度差異碾掉。
    """
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM hackathon_backend_level30 "
            " WHERE station_uid = %s AND slot BETWEEN %s AND %s AND is_imputed = 1",
            (station_uid, t0, t1))
        return cur.fetchone()["n"]


def carried_count(station_uid: str, t0, t1) -> int:
    """某站某區間（含頭含尾）有幾格是無限 carry（is_imputed = 2）。

    ★ 2026-09-02 新增。43_level30_carry.sql 把 4~7 月的洞全部 carry 掉之後，
      /predict 的 48 格 context 不再有 null（missing_slots 恆為 0），
      「這格是延用來的」變成唯一還說得出口的可信度訊號。
      出處：meet/20260902/計劃-level30灌歷史與無限carry.md 決策 11。
    """
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM hackathon_backend_level30 "
            " WHERE station_uid = %s AND slot BETWEEN %s AND %s AND is_imputed = 2",
            (station_uid, t0, t1))
        return cur.fetchone()["n"]
