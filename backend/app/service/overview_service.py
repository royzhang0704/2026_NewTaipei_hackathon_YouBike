# ════════════════════════════════════════════════════════════
# overview_service —— 單站 24 小時視圖（8/31 使用者定案）
#
# 「每半小時批次預測寫 forecast_history（Job B），查詢端只讀 DB」——
# 這支就是查詢端：以該站**最新批次 origin** 為錨點，
#   往前 9 小時實況（level30，18 格）＋ 往後 3 小時預測（6 格）
# 拼成 24 格 = 12 小時的車位實況與預覽（8/31 使用者定案 9+3）。
#
# ★ 全程不打 SageMaker。即時推論只剩 /predict 一條路（demo 特殊用途）。
# ★ 缺格 carry-forward（8/31 使用者定案）：無觀測格延用前一個實測值，
#   標 carried=True 讓前端能誠實標示；視窗開頭就缺（無前值可延）維持 null。
# ★ 風險判定已抽到 app/service/risk_service.py（9/1）—— 單站頁與全市告警
#   清單共用同一份判定，不得各自複製。
# ★ 該站還沒被批到（新站、無歷史、排程未跑）時：實況錨在**虛擬的現在**
#   （sys_config_repo.effective_now），forecast 給空陣列 + forecast_missing。
#   ★ 錨點絕不可退回「該站 level30 全表最末格」—— 那會脫離虛擬時鐘、
#     錨到 demo 世界的未來（9/4 回報 → 9/7 修）。舊註解寫的就是這個 bug，
#     照著它改會把 bug 修回來。詳見
#     meet/20260907/計劃-修虛擬時鐘錨點繞過.md
# ★ 虛擬現在之前一格水位都沒有的站（實測 11 站，都在停站期間）：
#   不報 422，回 200 軟狀態（9/7 使用者定案）—— station／capacity 照給，
#   origin／now／risk 為 null，actual 空陣列 + actual_missing「歷史資料不足」。
#   與 forecast_missing 同一條原則：缺件不報錯，照給 + 說明原因。
#   ★ 前端本次不改（9/7 使用者決定）：meet/20260901/單站檢視.html 的
#     render() 仍假設 actual 非空（act[0].at），點到這 11 站會在前端炸。
#     這是已知的待補項，不是後端回錯。
# ════════════════════════════════════════════════════════════

from app import config
from app.errors import AppError
from app.repository import (baseline_repo, forecast_repo, history_repo,
                            slot_average_repo, station_repo, sys_config_repo)
from app.service import risk_service

_TS = "%Y-%m-%d %H:%M:%S"
ACTUAL_SLOTS = 18                     # 9 小時；加預測 6 格共 24 格 = 12 小時視圖

# ★ 兩條回傳路徑（正常／無實況軟狀態）共用同一份字串，別各寫一次。
# ★ 這三句都會直接顯示給使用者（前端單站檢視），所以**不寫內部名詞**
#   （9/7 使用者要求）：不提 tick／Job B／forecast_history／level30 ——
#   調度員不需要知道排程與資料表叫什麼，看到只會困惑。
#   要查內部原因請看 job_run / forecast_run 的紀錄，不要塞進 API 文案。
_SOURCE = "每 30 分鐘批次預測 ＋ 實況水位；本頁不觸發即時預測"
# 有實況、但這站沒被預測到 —— 使用者能看到水位圖，只是沒有預測線
_FORECAST_MISSING = "此站尚無預測 —— 歷史資料不足"
# 連實況都沒有（虛擬現在之前該站一格水位都沒有）—— 整站在這個時刻查不到東西
_ACTUAL_MISSING = "查無該站歷史"


def day_view(uid: str) -> dict:
    st = station_repo.find(uid)
    if st is None:
        raise AppError("STATION_NOT_FOUND", 404,
                       f"hackathon_backend_station 沒有 {uid}")

    origin = forecast_repo.latest_origin(uid)
    rows = forecast_repo.at_origin(uid, origin) if origin else []

    cap = st["capacity"]
    station = {"uid": st["station_uid"], "name": st["station_name"],
               "town_code": st["town_code"], "town": st["town"],
               "capacity": cap}
    forecast = [{"at": r["at"].strftime(_TS), "q19": float(r["q19"]),
                 "q50": float(r["q50"]), "q90": float(r["q90"])}
                for r in rows]

    # 實況錨點 = 預測 origin；沒有預測時錨在虛擬的現在。
    # ★ 這裡明寫 effective_now 而不是靠 tail() 的預設（tail 也擋了，見那支的
    #   docstring）—— 讓讀這支的人在呼叫端就看到錨點是什麼，不必跳進 repo。
    #   絕不可傳 at=None 讓它去拿全表最末格：那是 9/4 那顆 bug。
    h = history_repo.tail(uid, at=origin or sys_config_repo.effective_now(),
                          n=ACTUAL_SLOTS)
    if h is None:
        # 虛擬現在之前該站一格水位都沒有（停站期間）。這是**事實**不是故障，
        # 所以不報 422：與時間無關的站況照給，時間相關的一律 null。
        out = {"station": station, "origin": None, "now": None, "risk": None,
               "actual": [], "forecast": forecast,
               "model_job": rows[0]["model_job"] if rows else None,
               "source": _SOURCE, "actual_missing": _ACTUAL_MISSING}
        return out

    from datetime import timedelta
    step = timedelta(minutes=config.FREQ_MIN)
    slots = [h["start"] + step * i for i in range(ACTUAL_SLOTS)]

    # 缺格 carry-forward：延用前一個實測值並標 carried
    actual, prev = [], None
    for s, v in zip(slots, h["values"]):
        rec = {"at": s.strftime(_TS), "avail": v if v is not None else prev}
        if v is None and prev is not None:
            rec["carried"] = True
        if v is not None:
            prev = v
        actual.append(rec)

    cur = actual[-1]["avail"] if actual else None

    # 調度台數的基準：現況格 + 預測窗逐格的歷史同時段平均。
    # 樣本不足的桶直接剔掉 —— 寧可退回門檻算法，也不要拿 n=3 的平均當目標。
    anchor = h["anchor"]
    avg = {}
    if rows:
        avg = {ts: v for ts, v in
               slot_average_repo.series(uid, [r["at"] for r in rows] + [anchor]).items()
               if v["n"] >= config.SLOT_AVG_MIN_N}

    out = {
        "station": station,
        "origin": h["anchor"].strftime(_TS),
        # 現況（錨點格）：可借 / 可還 —— 前端頭條數字
        "now": {"at": h["anchor"].strftime(_TS), "avail": cur,
                "free": (cap - cur) if (cap is not None and cur is not None) else None,
                "carried": bool(actual and actual[-1].get("carried"))},
        "risk": (risk_service.risk(rows, cap, cur, anchor.strftime(_TS), avg, anchor)
                 if rows else None),
        "actual": actual,
        "forecast": forecast,
        "model_job": rows[0]["model_job"] if rows else None,
        "source": _SOURCE,
    }
    # 對答案（8/31）：預測視窗的真值 —— 回放世界的「未來」在 baseline_grid
    # 是已知歷史。只在有預測時給，前端以黃色疊加比較。
    if rows:
        tr = baseline_repo.avail_between(uid, rows[0]["at"], rows[-1]["at"])
        out["truth"] = [{"at": r["slot"].strftime(_TS), "avail": r["avail"]}
                        for r in tr]
    if not rows:
        out["forecast_missing"] = _FORECAST_MISSING
    return out
