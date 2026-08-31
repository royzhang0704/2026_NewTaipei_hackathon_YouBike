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
# ★ 該站還沒被批到（新站、無歷史、排程未跑）時：實況照給
#   （錨點退回該站 level30 最末格），forecast 給空陣列 + 原因。
# ════════════════════════════════════════════════════════════
from app import config
from app.errors import AppError
from app.repository import baseline_repo, forecast_repo, history_repo, station_repo

_TS = "%Y-%m-%d %H:%M:%S"
ACTUAL_SLOTS = 18                     # 9 小時；加預測 6 格共 24 格 = 12 小時視圖


_LEVEL = {0: "none", 1: "mid", 2: "high"}


def threshold(cap: int | None) -> int | None:
    """風險門檻 T（config §風險門檻）：clamp(10% × 車柱, 2, 5)。"""
    if not cap:
        return None
    return min(config.RISK_MAX, max(config.RISK_MIN,
                                    round(config.RISK_PCT * cap)))


def _debounce(sevs: list[int]) -> list[int]:
    """孤立的單格「高」降為「中」——30 分鐘的一格抖動不該升成高風險。"""
    out = list(sevs)
    for i, s in enumerate(sevs):
        if s == 2:
            prev = sevs[i - 1] if i > 0 else 0
            nxt = sevs[i + 1] if i + 1 < len(sevs) else 0
            if prev < 2 and nxt < 2:
                out[i] = 1
    return out


def _side(sevs: list[int], rows: list[dict]) -> dict:
    """把逐格嚴重度收斂成一個判定：等級 + 最早發生時刻 + 持續格數。"""
    g = _debounce(sevs)
    lv = max(g) if g else 0
    onset = next((rows[i]["at"].strftime(_TS)
                  for i, s in enumerate(g) if s == lv), None) if lv else None
    return {"level": _LEVEL[lv], "slots": sum(1 for s in g if s == lv) if lv else 0,
            "onset": onset, "by_slot": g}


def _risk(rows: list[dict], cap: int | None, cur: int | None) -> dict | None:
    """風險判定（8/31 定案）—— 分級直接吃 DeepAR 的三分位，不再拉第二層門檻：
         高  q50 越線（中位路徑就出事）
         中  q19/q90 越線但 q50 沒有（帶的一端會出事）
         無  整條帶都安全
       滿站側用 q90（可借最多 = 可還最少）對稱判定。
       ★ 只看路徑最嚴重點與最早發生時刻，不看終點值。"""
    t = threshold(cap)
    if t is None:
        return None
    short = [2 if r["q50"] <= t else (1 if r["q19"] <= t else 0) for r in rows]
    full = [2 if cap - r["q50"] <= t else (1 if cap - r["q90"] <= t else 0)
            for r in rows]
    now_lv = 0
    if cur is not None:
        if cur <= t or cap - cur <= t:
            now_lv = 2
        elif cur <= 2 * t or cap - cur <= 2 * t:
            now_lv = 1
    out = {"threshold": t, "shortage": _side(short, rows), "full": _side(full, rows),
           "now": {"level": _LEVEL[now_lv],
                   "kind": None if cur is None else
                           ("shortage" if cur <= cap - cur else "full")}}
    lv = max(_LEVEL_N[out["shortage"]["level"]], _LEVEL_N[out["full"]["level"]])
    out["overall"] = _LEVEL[lv]
    return out


_LEVEL_N = {v: k for k, v in _LEVEL.items()}


def day_view(uid: str) -> dict:
    st = station_repo.find(uid)
    if st is None:
        raise AppError("STATION_NOT_FOUND", 404,
                       f"hackathon_backend_station 沒有 {uid}")

    origin = forecast_repo.latest_origin(uid)
    rows = forecast_repo.at_origin(uid, origin) if origin else []

    # 實況錨點 = 預測 origin；沒有預測時退回該站自己的最末格
    h = history_repo.tail(uid, at=origin, n=ACTUAL_SLOTS)
    if h is None:
        raise AppError("INSUFFICIENT_HISTORY", 422,
                       f"{uid} 在 level30 沒有任何資料")

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

    cap = st["capacity"]
    cur = actual[-1]["avail"] if actual else None
    out = {
        "station": {"uid": st["station_uid"], "name": st["station_name"],
                    "town_code": st["town_code"], "town": st["town"],
                    "capacity": cap},
        "origin": h["anchor"].strftime(_TS),
        # 現況（錨點格）：可借 / 可還 —— 前端頭條數字
        "now": {"at": h["anchor"].strftime(_TS), "avail": cur,
                "free": (cap - cur) if (cap is not None and cur is not None) else None,
                "carried": bool(actual and actual[-1].get("carried"))},
        "risk": _risk(rows, cap, cur) if rows else None,
        "actual": actual,
        "forecast": [{"at": r["at"].strftime(_TS), "q19": float(r["q19"]),
                      "q50": float(r["q50"]), "q90": float(r["q90"])}
                     for r in rows],
        "model_job": rows[0]["model_job"] if rows else None,
        "source": "forecast_history（每 30 分批次） + level30 實況；本查詢不觸發推論",
    }
    # 對答案（8/31）：預測視窗的真值 —— 回放世界的「未來」在 baseline_grid
    # 是已知歷史。只在有預測時給，前端以黃色疊加比較。
    if rows:
        tr = baseline_repo.avail_between(uid, rows[0]["at"], rows[-1]["at"])
        out["truth"] = [{"at": r["slot"].strftime(_TS), "avail": r["avail"]}
                        for r in tr]
    if not rows:
        out["forecast_missing"] = ("此站尚無批次預測 —— 排程（tick）還沒跑、"
                                   "或該站被 Job B 跳過（無 cat／歷史不足）")
    return out
