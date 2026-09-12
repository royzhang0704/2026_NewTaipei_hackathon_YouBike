# ════════════════════════════════════════════════════════════
# jobs/dispatch_sweep.py —— 調度單收尾（batch_predict 階段三）
#
#   調度單寫進去之後沒有人會回來按「完成」—— demo 沒有車隊 App，
#   調度員也不會回頭清單子。這支代勞：每輪風險判定完，拿最新現況
#   回頭看每一張 active 單還成不成立。
#
#   ① anchor 不再有**同方向** action      → fulfilled
#   ② **非 anchor 那端**出現**反方向** action → invalid
#   ①優先於② —— anchor 已達成就不必在乎來源端的狀態。
#
# ★ 判準一律用 action，不用 level（§13）。目標站從 high 掉到 mid，按
#   level 就算「離開高風險」→ 標 fulfilled，但它其實還缺 5 台、車還在
#   路上。用 action 也讓三處（選候選／發起資格／排程收單）同一個欄位，
#   不會出現「選得進來、按不下去」或「剛按完就被刪」。
#
# ★ fulfilled **不代表車真的到了**，只代表 anchor 已無同方向需求。
#   對外說法要誠實，不要講成「調度成功率」。
#
# ★ 同程序由 batch_predict.run() 在 _maybe_risk() 之後呼叫（階段三），
#   順序天然保證 —— 不會發生「風險還沒寫完就先檢查調度」。
#   ⚠ 沿用 _maybe_risk 的紀律：**sweep 失敗不可讓整輪變 failed**，
#     預測與風險才是主產出。
#
# 單獨執行：
#   uv run python -m jobs.dispatch_sweep
#   uv run python -m jobs.dispatch_sweep --origin '2026-05-01 10:00:00'
#
# 出處：meet/20260912/計劃-調度確認.md §7
# ════════════════════════════════════════════════════════════
import argparse
import sys
from datetime import datetime

from app.repository import (dispatch_repo, forecast_run_repo, job_run_repo,
                            sys_config_repo)
from app.repository.db import get_conn

JOB_NAME = "dispatch_sweep"

_ACT_WORD = {"refill": "補車", "remove": "取車"}
_SIDE_WORD = {"refill": "缺車", "remove": "滿站"}
_LV_WORD = {3: "high", 2: "mid", 1: "low", 0: "none"}


def _actions(origin: datetime, uids: set[str]) -> dict[str, dict]:
    """一次撈齊本輪所有相關站的 action（逐筆查會變成 N 次往返）。"""
    if not uids:
        return {}
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT r.station_uid, r.status, r.action, r.level_n, s.station_name "
            "  FROM hackathon_backend_risk_snapshot r "
            "  JOIN hackathon_backend_station s USING (station_uid) "
            " WHERE r.origin = %s AND r.station_uid = ANY(%s)",
            (origin, list(uids)))
        return {r["station_uid"]: r for r in cur.fetchall()}


def _judged(r: dict | None) -> dict | None:
    """只有「本輪判定有效」的列能拿來收單。

    ★ action IS NULL 有兩種意思，必須分開（risk_snapshot 的 status 就是
      為此而存在）：
        status='ok'          判定有效、這站沒事      → 可以收單
        status='no_forecast' 本輪沒打這站、不知道     → **不收單**
      兩者的 action 都是 NULL。把 no_forecast 當成「沒事了」，會在漏打
      一站的那一輪把還在路上的車單標成 fulfilled —— 「不知道」不等於
      「沒事了」，維持 active 下一輪有資料再判。
    """
    return r if r is not None and r["status"] == "ok" else None


def judge(order: dict, snap: dict[str, dict]) -> tuple[str, str] | None:
    """一張單的判定 → (status, closed_reason)；None = 維持 active。

    方向對照（anchor_uid 的用處 —— 光看 from/to 分不出哪端是風險站）：
      refill 單：anchor = to_uid，  非 anchor 端 = from_uid（供給站）
      remove 單：anchor = from_uid，非 anchor 端 = to_uid  （接收站）
    """
    act = order["action"]
    anchor_uid = order["anchor_uid"]
    other_uid = order["from_uid"] if act == "refill" else order["to_uid"]
    if other_uid == anchor_uid:                      # 理論上不會，防呆
        other_uid = order["to_uid"] if act == "refill" else order["from_uid"]

    a = _judged(snap.get(anchor_uid))
    o = _judged(snap.get(other_uid))

    # ① anchor 不再有同方向需求 → 目的達成
    if a is not None and a["action"] != act:
        who = a["station_name"] or anchor_uid
        return "fulfilled", f"目標站 {who} 已無{_ACT_WORD[act]}需求"

    # ② 非 anchor 那端轉成反方向 → 這趟車的另一端已經不成立
    #   反方向 = 與單同一個 action：refill 單的供給站自己也缺車了，
    #   remove 單的接收站自己也滿了。
    if o is not None and o["action"] == act:
        who = o["station_name"] or other_uid
        role = "供給站" if act == "refill" else "接收站"
        lv = _LV_WORD.get(o["level_n"], "none")
        return "invalid", f"{role} {who} 轉為{_SIDE_WORD[act]}風險（{lv}）"

    return None


def sweep(origin: datetime | None = None, write_job_run: bool = True) -> dict:
    """掃一輪。回傳計數，四項加總 = checked。"""
    if origin is None:
        origin = forecast_run_repo.latest_risk_origin(sys_config_repo.effective_now())
    if origin is None:
        return {"checked": 0, "fulfilled": 0, "invalid": 0, "active_remain": 0,
                "note": "沒有任何一輪風險判定"}

    run_id = job_run_repo.start(JOB_NAME, origin) if write_job_run else None
    try:
        orders = dispatch_repo.list_by_status("active")
        uids = {o["anchor_uid"] for o in orders} | {o["from_uid"] for o in orders} \
            | {o["to_uid"] for o in orders}
        snap = _actions(origin, uids)

        todo = []
        for o in orders:
            v = judge(o, snap)
            if v is not None:
                todo.append((o["id"], v[0], v[1]))

        # 一輪收單共用同一個虛擬時刻 —— 逐筆各自取會得到 N 個不同的時間
        closed_slot = sys_config_repo.effective_now()
        dispatch_repo.close_many(todo, closed_slot)

        c = {"checked": len(orders),
             "fulfilled": sum(1 for t in todo if t[1] == "fulfilled"),
             "invalid": sum(1 for t in todo if t[1] == "invalid")}
        c["active_remain"] = c["checked"] - c["fulfilled"] - c["invalid"]

        if run_id:
            job_run_repo.finish(run_id, "success",
                                rows_written=len(todo), detail=c)
        return c
    except Exception as e:                            # noqa: BLE001
        if run_id:
            job_run_repo.finish(run_id, "failed", error=f"{type(e).__name__}: {e}")
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description="調度單收尾（batch_predict 階段三）")
    ap.add_argument("--origin", help="用哪一輪的風險現況判（預設最新一輪）")
    a = ap.parse_args()
    origin = datetime.fromisoformat(a.origin) if a.origin else None

    c = sweep(origin)
    if c.get("note"):
        print(f"   {c['note']}")
        return 0
    print(f"   調度收尾：檢查 {c['checked']} 筆 → 完成 {c['fulfilled']}／"
          f"失效 {c['invalid']}／續留 {c['active_remain']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
