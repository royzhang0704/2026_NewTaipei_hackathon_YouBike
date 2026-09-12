# ════════════════════════════════════════════════════════════
# alert_service —— 全市／同區風險告警清單（9/1）
#
# 「Job B 每半小時判完風險寫 risk_snapshot，查詢端只讀 DB」——
# 這支就是查詢端：純 SELECT + join 主檔，零計算、不觸發推論。
#
# ★ 錨點 origin 查**主檔**不查明細 —— 明細有列不代表風險判定完成。
#   而且一定要夾 effective_now()：demo 回放時表裡可能躺著整段未來的列，
#   不夾上界畫面永遠停在最末一輪（理由同 forecast_repo.latest_origin）。
#
# ★ 排序固定不開放參數：level_n DESC, streak_n DESC, bikes DESC, onset ASC。
#   streak 當第二鍵是它的用途 —— 連 6 輪（3 小時）沒改善的站，
#   要排在剛亮燈的站前面。9/12 起 streak 只計高風險，所以這一鍵實際上
#   只在高風險群組內起作用（中低的 streak_n 恆為 0，退化成以 bikes 排）。
#
# ★ 每個 item 有**兩個**持續時數，不要混用：
#     streak.hours —— 連續高風險幾小時（風險側，吃 algo_ver 換版重算）
#     stale.hours  —— 可借數停在同一個值幾小時（資料側，不吃 algo_ver）
#   後者用來辨識卡住／疑似斷線的站：水位一動也不動，比風險燈號更早
#   透露「這站的資料或車輛都沒在流動」。
# ════════════════════════════════════════════════════════════
from app.errors import AppError
from app.repository import forecast_run_repo, risk_repo, station_repo, sys_config_repo
from app.service.risk_service import LEVEL

_TS = "%Y-%m-%d %H:%M:%S"
_NAME_N = {v: k for k, v in LEVEL.items()}          # high/mid/low/none → 3/2/1/0


def _ts(v):
    return v.strftime(_TS) if v else None


def _hours(origin, since):
    """since → 已持續幾小時。streak 與水位停滯共用同一套換算。

    ★ +0.5 是補回「本輪自己代表的那半小時」—— 剛亮燈（since = origin）
      顯示 0.5 而不是 0。用時間差而不是輪數，是為了耐漏批。
    """
    return round((origin - since).total_seconds() / 3600 + 0.5, 1) if since else None


def _item(r: dict, origin) -> dict:
    cap, avail = r["capacity"], r["now_avail"]
    return {
        "station_uid": r["station_uid"], "name": r["station_name"],
        "town_code": r["town_code"], "town": r["town"], "capacity": cap,
        "level": LEVEL[r["level_n"]], "side": r["side"],
        "threshold": r["threshold"], "confidence": r["confidence"],
        "conflict": r["conflict"], "onset": _ts(r["onset"]),
        "now": {"avail": avail,
                "free": (cap - avail) if (cap is not None and avail is not None) else None,
                "carried": r["now_carried"], "crossed": r["now_crossed"]},
        "baseline": round(r["baseline"], 2) if r["baseline"] is not None else None,
        # 持續時數用 streak_since 換算，比「連續 N 輪」耐漏批
        # ★ 9/12 起只計高風險：中低風險的 n 恆為 0、hours 恆為 null
        "streak": {"n": r["streak_n"], "since": _ts(r["streak_since"]),
                   "hours": _hours(origin, r["streak_since"])},
        # 水位停滯：可借數停在同一個值多久 —— 卡住／疑似斷線的站
        # ★ 與 dispatch.action 的 hold（預計自行消退）無關，刻意不共用字眼
        "stale": {"avail": r["stale_avail"], "since": _ts(r["stale_since"]),
                  "hours": _hours(origin, r["stale_since"])},
        "dispatch": ({"action": r["action"], "bikes": r["bikes"],
                      "basis": r["basis"]} if r["action"] else None),
    }


def alerts(town_code: str | None = None, level: str | None = None,
           side: str | None = None, action: str | None = None,
           limit: int = 100, offset: int = 0) -> dict:
    origin = forecast_run_repo.latest_risk_origin(sys_config_repo.effective_now())
    if origin is None:
        raise AppError("NO_RISK_SNAPSHOT", 422,
                       "還沒有任何一輪風險判定 —— 排程（tick）還沒跑，"
                       "或需要先跑 jobs.batch_predict --risk-only")

    levels = tuple(_NAME_N[x] for x in (level or "high,mid,low").split(",")
                   if x in _NAME_N) or (3, 2, 1)
    scope = None
    if town_code:
        scope = next((t for t in station_repo.towns() if t["town_code"] == town_code), None)
        if scope is None:
            raise AppError("TOWN_NOT_FOUND", 404, f"沒有行政區 {town_code}")

    rows = risk_repo.rank(origin, town_code, levels, side, action, limit, offset)
    s = risk_repo.summary(origin, town_code)
    st = forecast_run_repo.round_stats(origin)

    return {
        "origin": _ts(origin),
        "algo_ver": st["algo_ver"],
        "model_job": st["model_job"],
        "scope": ({"town_code": scope["town_code"], "town": scope["town"]}
                  if scope else None),
        "summary": {
            "stations": s["stations"], "high": s["risk_high"], "mid": s["risk_mid"],
            "low": s["risk_low"], "none": s["risk_none"],
            "no_forecast": s["risk_no_forecast"],
            "refill": {"stations": s["refill_stations"], "bikes": s["refill_bikes"]},
            "remove": {"stations": s["remove_stations"], "bikes": s["remove_bikes"]},
            "hold": s["hold_stations"],
        },
        "items": [_item(r, origin) for r in rows],
        "total": risk_repo.count(origin, town_code, levels, side, action),
        "limit": limit, "offset": offset,
        "source": ("hackathon_backend_risk_snapshot（Job B 每 30 分判定）；"
                   "本查詢不觸發推論"),
    }


def station_risk(uid: str, n: int = 48) -> dict:
    """單站風險歷程 —— 「這站連續亮了幾輪」的下鑽。"""
    st = station_repo.find(uid)
    if st is None:
        raise AppError("STATION_NOT_FOUND", 404,
                       f"hackathon_backend_station 沒有 {uid}")
    rows = risk_repo.station_history(uid, n)
    return {
        "station": {"uid": uid, "name": st["station_name"], "town": st["town"],
                    "capacity": st["capacity"]},
        "history": [{"origin": _ts(r["origin"]), "status": r["status"],
                     "level": LEVEL[r["level_n"]] if r["level_n"] is not None else None,
                     "side": r["side"], "streak_n": r["streak_n"],
                     "streak_since": _ts(r["streak_since"]),
                     "stale_avail": r["stale_avail"],
                     "stale_since": _ts(r["stale_since"]),
                     "now_avail": r["now_avail"], "threshold": r["threshold"],
                     "action": r["action"], "bikes": r["bikes"]} for r in rows],
    }
