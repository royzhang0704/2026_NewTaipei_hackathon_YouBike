# ════════════════════════════════════════════════════════════
# predict_service —— 唯一知道「怎麼組 payload」的地方。
# 逐步規格：meet/20260828/計劃-後端服務.md §3；
# 鄰站 cat 代理：meet/20260828/計劃-鄰站cat代理.md §3。
#
# payload 四條規則（每條都踩過，見 meet/20260827/回到DeepAR與H6重訓.md §1）：
#   1  target 給滿 48 格，不是只給當下 1 格（否則降級成查表，安靜錯）
#   2  start 是序列起點，不是預測起點（偏移了也安靜錯）
#   3  dynamic_feat 推論時長度 = len(target) + H = 54（錯了 endpoint 會報）
#   4  缺格填 null，不是 0（0 = 真的沒車，安靜錯）
#      └ 8/28 擴充（計劃-排程自癒 §2「週期補值」）：level30 已做完
#        carry-forward ≤6 格，剩下的洞由 Job A 用「一週前同 slot」補進
#        level30 並標 is_imputed=1（值落表，使用者定案改的）；
#        上週也缺就保持 null。本層只負責把 imputed_slots / missing_slots
#        誠實寫進回應，不自己再補一次。
# ════════════════════════════════════════════════════════════
from datetime import datetime, timedelta

from app import config
from app.errors import AppError
from app.repository import (calendar_repo, endpoint_repo, hist_repo,
                            history_repo, station_repo)

_TS = "%Y-%m-%d %H:%M:%S"


def build_payload(station_uid: str, at: datetime | None = None,
                  is_holiday: int | None = None) -> dict:
    """組 endpoint payload（predict_one 的 1~4 步）。
    抽成獨立函式的原因：冒煙測試的 smoke_payload.json 必須跟後端
    真正送出去的是同一份 —— 同一段程式碼，不是第二份組裝邏輯。
    回傳 {"payload", "station", "history", "cat", "proxy",
          "imputed_slots"(週期補值格數), "missing_slots"(仍為 null 的格數)}。"""
    # 1. 站與 cat（含鄰站代理）
    st = station_repo.find(station_uid)
    if st is None:
        raise AppError("STATION_NOT_FOUND", 404,
                       f"hackathon_backend_station 沒有 {station_uid}")
    cat, proxy = st["cat"], None
    if cat is None:
        if config.PROXY_CAT_ENABLED and st["proxy_station_uid"]:
            src = station_repo.find(st["proxy_station_uid"])
            if src is not None and src["cat"] is not None:
                # 做法 A：只借鄰站的 cat；歷史與 capacity 一律用本站的
                cat = src["cat"]
                proxy = {"cat_from": st["proxy_station_uid"],
                         "distance_m": st["proxy_distance_m"]}
        if cat is None:
            raise AppError("STATION_UNKNOWN", 422,
                           f"{station_uid} 存在但訓練後才新增，模型沒有它的 embedding")

    # 2. 48 格水位歷程（絕不補 0；缺格為 None）
    h = history_repo.tail(station_uid, at)
    if h is None or h["first_slot"] > h["start"]:
        raise AppError("INSUFFICIENT_HISTORY", 422,
                       f"{station_uid} 在指定時刻之前不足 {config.CONTEXT} 格歷史")

    # 2b. 誠實標註：這 48 格裡有幾格不是實測（規則第 4 條擴充）
    #
    # ★ 補值本身**不在這裡做** —— 8/28 定案改成值落表，由 Job A 每輪
    #   呼叫 hist_repo.impute_weekly() 寫進 level30（標 is_imputed=1）。
    #   服務層再補一次就是第二份邏輯，兩邊遲早長歪。
    #   這裡只負責「說清楚讀到的是什麼」：
    #     imputed_slots  取一週前同 slot 補的格（有值，但不是實測）
    #     missing_slots  連上週也沒有，仍是 null 的格
    values = h["values"]
    imputed = hist_repo.imputed_count(station_uid, h["start"], h["anchor"])
    n_missing = sum(v is None for v in values)

    # 3. 54 格 is_holiday（覆寫 = what-if）
    seq, missing = calendar_repo.holiday_seq(h["start"], config.CONTEXT + config.H)
    if missing:
        raise AppError("CALENDAR_OUT_OF_RANGE", 422,
                       f"預測區間超出日曆涵蓋（缺 {missing[0]} 等 {len(missing)} 日）")
    if is_holiday is not None:
        seq = [is_holiday] * len(seq)

    # 4. 組 payload → invoke
    payload = {
        "instances": [{
            "start": h["start"].strftime(_TS),
            "target": values,
            "cat": [cat],
            "dynamic_feat": [seq],
        }],
        "configuration": {"num_samples": config.NUM_SAMPLES,
                          "output_types": ["quantiles"],
                          "quantiles": [config.Q_LO, config.Q_MID, config.Q_HI]},
    }
    return {"payload": payload, "station": st, "history": h, "cat": cat,
            "proxy": proxy, "imputed_slots": imputed,
            "missing_slots": n_missing}


def predict_one(station_uid: str, at: datetime | None = None,
                is_holiday: int | None = None) -> dict:
    b = build_payload(station_uid, at, is_holiday)
    st, h, proxy = b["station"], b["history"], b["proxy"]
    res = endpoint_repo.invoke(b["payload"])

    # 5. 後處理（ml-deepar/predict.py 的物理上限：負二項支撐 0..∞，
    #    小站會預測出超過車樁的台數；capacity 缺值時只夾下界）
    cap = st["capacity"]

    def clip(v: float) -> float:
        v = max(float(v), 0.0)
        return round(min(v, cap) if cap is not None else v, 2)

    lo = [clip(v) for v in res["q19"]]
    mid = [clip(v) for v in res["q50"]]
    hi = [clip(v) for v in res["q90"]]

    anchor = h["anchor"]
    step = timedelta(minutes=config.FREQ_MIN)
    out = {
        "station": {"uid": st["station_uid"], "name": st["station_name"],
                    "town_code": st["town_code"], "town": st["town"],
                    "capacity": cap},
        "origin": anchor.strftime(_TS),
        "horizon": {"from": (anchor + step).strftime(_TS),
                    "to": (anchor + step * config.H).strftime(_TS),
                    "steps": config.H, "freq": f"{config.FREQ_MIN}min"},
        "forecast": [{"at": (anchor + step * (i + 1)).strftime(_TS),
                      "q19": lo[i], "q50": mid[i], "q90": hi[i]}
                     for i in range(config.H)],
        # 路徑極值不是終點值 —— 只看終點會漏掉 13.5% 的空車事件（8/27 實測）
        "path_min_lo": min(lo),
        "path_max_hi": max(hi),
        "model": config.MODEL_INFO,
        "caveats": list(config.CAVEATS),
    }
    # 誠實標註：這次預測的 48 格 context 裡有幾格不是真值、幾格仍是 null
    out["imputed_slots"] = b["imputed_slots"]
    out["missing_slots"] = b["missing_slots"]
    if b["imputed_slots"]:
        out["caveats"].append(
            f"context 48 格中 {b['imputed_slots']} 格為週期補值（取一週前同 slot），"
            f"非實測；當日缺格要等隔日 08:00 後歷史 API 回補才會變成真值")
    if b["missing_slots"]:
        out["caveats"].append(
            f"context 48 格中 {b['missing_slots']} 格仍為 null（上週同 slot 也缺）")
    if proxy is not None:
        out["proxy"] = proxy
        out["caveats"].append(
            f"cat 借用 {proxy['distance_m']} m 外鄰站的 embedding（本站為訓練後新增），"
            "水位歷程仍為本站實際資料")
    if res["mock"]:
        out["mock"] = True
        out["caveats"].append("ENDPOINT_MOCK=1：數字為假值，只驗鏈路不驗預測")
    return out


if __name__ == "__main__":            # 冒煙 payload：計劃-AWS_Endpoint.md §3-2
    import argparse, json, sys
    from app.errors import AppError
    p = argparse.ArgumentParser(description="輸出與 /predict 完全相同的 endpoint payload")
    p.add_argument("--station", required=True)
    p.add_argument("--at", default=None)
    a = p.parse_args()
    try:
        b = build_payload(a.station, datetime.fromisoformat(a.at) if a.at else None)
    except AppError as e:
        raise SystemExit(f"✗ {e.code}: {e.message}")
    inst = b["payload"]["instances"][0]
    print(f"station {a.station}  cat {b['cat']}"
          + (f"（代理自 {b['proxy']['cat_from']}）" if b["proxy"] else "")
          + f"  start {inst['start']}  target {len(inst['target'])} 格"
          f"（週期補值 {b['imputed_slots']}／仍缺 {b['missing_slots']}）"
          f"  dynamic_feat {len(inst['dynamic_feat'][0])} 格", file=sys.stderr)
    json.dump(b["payload"], sys.stdout, ensure_ascii=False)
