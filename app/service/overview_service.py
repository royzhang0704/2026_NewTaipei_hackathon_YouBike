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


_LEVEL = {0: "none", 1: "low", 2: "mid", 3: "high"}
NEAR = 2                              # 「近 1 小時」= 2 格（30 分 × 2）


def threshold(cap: int | None) -> int | None:
    """風險門檻 T（config §風險門檻）：15% × 車柱，下限 2、不封頂。
    ★ 用 int(x+0.5) 不用 round()——Python 的 round 是銀行家捨入
      （2.5→2、4.5→4），跟 SQL 統計對不起來，25/45 柱的站會差 1 台。"""
    if not cap:
        return None
    t = max(config.RISK_MIN, int(config.RISK_PCT * cap + 0.5))
    return min(config.RISK_MAX, t) if config.RISK_MAX else t


def _side(cross: list[bool], now_crossed: bool, rows: list[dict],
          origin_ts: str) -> dict:
    """時間制分級（8/31 使用者定案，取代原本的分位制）——
       等級講「多快會發生」，不是「多確定會發生」：
         高  現況已越線，且近 1 小時仍越線（撐不過去）
         中  近 1 小時內會越線（含現在越線但 1 小時內回穩）
         低  1~3 小時內會越線
         無  整段預測都不越線
       ★ 越線一律看 q50（最可能路徑）。用 q19 太敏感（缺車觸發率會到 26%），
         而且 q50 判定讓「同一格同時缺車又滿站」在數學上不可能發生
         （q50 不能同時 <=T 又 >=cap-T，除非 cap<=2T，最小站 8 柱 > 4）。
       ★ 高風險的第一個條件是**實測現況**，不是預測 —— 逐格 q* 尚未校準
         （config.CAVEATS），最重的等級要靠最硬的證據。"""
    near = cross[:NEAR]
    if now_crossed and near and all(near):
        lv = 3
    elif any(near):
        lv = 2
    elif any(cross[NEAR:]):
        lv = 1
    else:
        lv = 0
    if lv == 3:
        onset = origin_ts                       # 現在就已經越線
    elif lv:
        onset = rows[next(i for i, c in enumerate(cross) if c)]["at"].strftime(_TS)
    else:
        onset = None
    return {"level": _LEVEL[lv], "onset": onset, "slots": sum(cross),
            "by_slot": [int(c) for c in cross], "now_crossed": now_crossed}


def _confidence(rows: list[dict], cap: int, t: int, lend: bool) -> str:
    """分位降級成「信心註記」—— 三分位本來就是嵌套的證據階梯。
       缺車側 q19<=q50<=q90：連 q90 都越線 = 運氣好也缺 = 幾乎確定。
       滿站側對稱（可還 = cap - avail，所以 q19 對應空位最多）。"""
    if lend:
        lo, mid, hi = "q90", "q50", "q19"
        ok = lambda k: any(r[k] <= t for r in rows)          # noqa: E731
    else:
        lo, mid, hi = "q19", "q50", "q90"
        ok = lambda k: any(cap - r[k] <= t for r in rows)    # noqa: E731
    if ok(lo):
        return "almost_certain"
    if ok(mid):
        return "likely"
    if ok(hi):
        return "possible"
    return "none"


def _risk(rows: list[dict], cap: int | None, cur: int | None,
          origin_ts: str) -> dict | None:
    t = threshold(cap)
    if t is None:
        return None
    sh = _side([r["q50"] <= t for r in rows],
               cur is not None and cur <= t, rows, origin_ts)
    fu = _side([cap - r["q50"] <= t for r in rows],
               cur is not None and cap - cur <= t, rows, origin_ts)
    sh["confidence"] = _confidence(rows, cap, t, True)
    fu["confidence"] = _confidence(rows, cap, t, False)
    out = {"threshold": t, "shortage": sh, "full": fu,
           "overall": _LEVEL[max(_LEVEL_N[sh["level"]], _LEVEL_N[fu["level"]])]}
    # 兩側都有事 = 路徑在 3 小時內從一端擺到另一端（潮汐站，調度看時機用）
    if sh["level"] != "none" and fu["level"] != "none":
        out["conflict"] = "swing"
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
        "risk": _risk(rows, cap, cur, h["anchor"].strftime(_TS)) if rows else None,
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
