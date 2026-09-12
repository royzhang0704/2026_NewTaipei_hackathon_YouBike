# ════════════════════════════════════════════════════════════
# dispatch_service —— 調度候選站的選取與排序
#
#   風險判定（risk_service）說「板橋國中站要補 6 台」，這支回答下一題：
#   **那 6 台從哪裡來**。
#
# ★ 這裡是「從哪調車」的唯一實作。兩條路徑共用：
#     ① 按鈕：StationDetail 行動卡 → intent='dispatch' → dispatch_events
#     ② 打字：「XX 站從哪調車」→ ASK_SOURCE_RE → datapkg._attach_donors
#   兩條路必須拿到同一組候選（9/12 定案 1），所以 _attach_donors 內部
#   也是呼叫這支，不得各自複製一份規則。
#
# ★ 候選定義 = 「現況 vs 該時段常態平均」，不是舊的「side=full 且
#   action=remove」。舊規則只找得到「已經滿到該取車」的站，但一個站
#   比常態多 7 台、還遠不到滿站風險時，那 7 台照樣可以調 —— 它才是
#   調度實務上最常用的來源。
#
# 出處：meet/20260912/計劃-調度確認.md §4
# 冒煙：uv run python -m app.service.dispatch_service
# ════════════════════════════════════════════════════════════
from datetime import datetime

from app import config
from app.errors import AppError
from app.geo import haversine_m
from app.repository import (dispatch_repo, forecast_run_repo, sys_config_repo)
# ★ DONOR_MAX_M 刻意留在 assistant.common（環境變數 ASSISTANT_DONOR_MAX_KM）
#   而不搬進 config：搬動會連帶碰 .env 與既有的 donor 路徑，兩邊各讀一份
#   環境變數才是真正的口徑分叉。這裡 import 過來用，只有一個來源。
from app.service.assistant.common import DONOR_MAX_M

# 給前端的候選上限。多了勾選介面會變成一張表，調度員不會逐筆讀。
MAX_ITEMS = 10

_LEVEL_WORD = {3: "high", 2: "mid", 1: "low", 0: "none"}

# anchor 的 action → (方向, 候選要排除的 action, 候選扮演的角色)
#   refill：anchor 缺車，候選當取車來源 —— 候選自己若也 refill 就是自身難保
#   remove：anchor 滿站，候選當補車目標 —— 候選自己若也 remove 就塞不下了
_DIR = {"refill": (1, "refill"), "remove": (-1, "remove")}


def _coverage_walk(pool: list[dict], need: int) -> list[dict]:
    """由近而遠逐站累加，湊滿 need 就停（湊不滿就是全拿，差額由 shortfall 講）。"""
    picked, acc = [], 0
    for c in pool:
        if acc >= need:
            break
        picked.append(c)
        acc += c["supply"]
    return picked


def _shape(c: dict, anchor: dict, take: int, selected: bool) -> dict:
    """候選 row → 給前端／LLM 的項目。take = 建議從這站搬幾台。"""
    note = None
    # 候選自己被判成反方向 → 一趟車解決兩站（決策 1 的加分情境）
    if c["action"] and c["action"] != anchor["action"]:
        note = "本站同時建議取車（一趟解決兩站）" if anchor["action"] == "refill" \
            else "本站同時建議補車（一趟解決兩站）"
    return {"uid": c["station_uid"], "name": c["station_name"], "town": c["town"],
            "cross_town": c["town_code"] != anchor["town_code"],
            "supply": int(c["supply"]),
            "distance_m": None if c["dist"] is None else round(c["dist"]),
            "bikes": take, "selected": selected,
            "level": _LEVEL_WORD.get(c["level_n"], "none"), "note": note}


def candidates(anchor_uid: str, action: str | None = None,
               origin: datetime | None = None) -> dict:
    """某個風險站的調度候選（排序案丙：一趟搬得完優先，其次距離）。

    action 傳入時只做一致性檢查（前端按鈕上帶的那個），實際一律以
    anchor 在最新一輪的 action 為準 —— 風險會跨輪翻面，前端帶的可能過期。
    """
    if origin is None:
        origin = forecast_run_repo.latest_risk_origin(sys_config_repo.effective_now())
    if origin is None:
        raise AppError("NO_RISK_SNAPSHOT", 422,
                       "還沒有任何一輪風險判定 —— 排程（tick）還沒跑，"
                       "或需要先跑 jobs.batch_predict --risk-only")

    anchor = dispatch_repo.anchor_row(origin, anchor_uid)
    if anchor is None:
        raise AppError("STATION_NOT_FOUND", 404,
                       f"最新一輪（{origin:%Y-%m-%d %H:%M}）查無站點 {anchor_uid} 的風險判定")

    act = anchor["action"]
    if act not in _DIR:
        # hold（預計自行消退）與 NULL（無風險）都沒有調度可發起。
        # ★ 判準用 action 不用 level：level 掉一階不代表車就不用來了。
        raise AppError("DISPATCH_NOT_APPLICABLE", 422,
                       f"{anchor['station_name']} 目前不需要調度"
                       f"（action={act or '無風險'}）")
    if action and action != act:
        raise AppError("DISPATCH_STALE", 400,
                       "資料已更新或來源站餘裕已被其他調度佔用，請重新詢問")

    direction, exclude = _DIR[act]

    # need：還缺幾台 = 本輪建議台數 − 已經派出去的 active 承諾
    already = sum(o["bikes"] for o in dispatch_repo.by_anchor(anchor_uid))
    need = max(0, (anchor["bikes"] or 0) - already)

    rows = dispatch_repo.candidate_rows(origin, anchor_uid, direction,
                                        exclude, config.DISPATCH_MIN_BIKES)
    here = (anchor["lat"], anchor["lon"])
    for c in rows:
        c["dist"] = haversine_m(here, (c["lat"], c["lon"]))
        c["supply"] = int(c["supply"])

    # ① 同區優先：調度車隊按區劃分，跨區要協調
    pool = [c for c in rows if c["town_code"] == anchor["town_code"]]
    # ② 同區的餘裕加起來都湊不滿，才擴到 5 km 內的跨區站
    if sum(c["supply"] for c in pool) < need:
        pool += [c for c in rows
                 if c["town_code"] != anchor["town_code"]
                 and c["dist"] is not None and c["dist"] <= DONOR_MAX_M]

    by_dist = sorted(pool, key=lambda c: (c["dist"] is None, c["dist"] or 0))

    # ③ 案丙：先看有沒有「一趟就搬得完」的站，有就取最近的那個
    #    ★ 不是「台數 DESC、距離 ASC」—— 台數幾乎不會相等，第二鍵永遠輪不到，
    #      實際效果是「8 台 4.8 km」贏過「7 台 300 m」，叫車跑 4.8 km 載 6 台。
    #      調度成本是距離，台數只是夠不夠的門檻。
    solo = [c for c in by_dist if c["supply"] >= need] if need > 0 else []
    picked = [solo[0]] if solo else _coverage_walk(by_dist, need)

    # ④ 分配台數：由近而遠吃掉 need，最後一站只搬需要的那幾台
    take, left = {}, need
    for c in picked:
        t = min(c["supply"], left) if left > 0 else 0
        take[c["station_uid"]] = max(t, 0)
        left -= t
    shortfall = max(0, left)

    picked_uids = {c["station_uid"] for c in picked}
    # ⑤ 勾選的排前面，其餘依距離補到 10 筆供調度員自行換站
    items = [_shape(c, anchor, take[c["station_uid"]], True)
             for c in picked if take[c["station_uid"]] > 0]
    items += [_shape(c, anchor, min(c["supply"], need or c["supply"]), False)
              for c in by_dist if c["station_uid"] not in picked_uids]

    return {
        "origin": f"{origin:%Y-%m-%d %H:%M:%S}",
        "anchor": {"uid": anchor["station_uid"], "name": anchor["station_name"],
                   "town": anchor["town"], "action": act,
                   "need": need, "already": already},
        "items": items[:MAX_ITEMS],
        "shortfall": shortfall,
    }


# ── 寫入端 ──────────────────────────────────────────────────
STALE_MSG = "資料已更新或來源站餘裕已被其他調度佔用，請重新詢問"


def _order_item(o: dict) -> dict:
    """DB 列 → API 形狀。兩端都攤成 from/to 物件，前端畫線直接取座標。"""
    return {
        "id": o["id"], "action": o["action"], "bikes": o["bikes"],
        "anchor_uid": o["anchor_uid"], "status": o["status"],
        "from": {"uid": o["from_uid"], "name": o["from_name"], "town": o["from_town"],
                 "lat": None if o["from_lat"] is None else float(o["from_lat"]),
                 "lon": None if o["from_lon"] is None else float(o["from_lon"])},
        "to": {"uid": o["to_uid"], "name": o["to_name"], "town": o["to_town"],
               "lat": None if o["to_lat"] is None else float(o["to_lat"]),
               "lon": None if o["to_lon"] is None else float(o["to_lon"])},
        "distance_m": o["distance_m"],
        "created_slot": f"{o['created_slot']:%Y-%m-%d %H:%M:%S}",
        "operator": o["operator"],
    }


def list_orders(status: str = "active") -> dict:
    """整批撈單。★ 不吃 town_code —— 見 dispatch_repo.list_by_status 的理由。"""
    origin = forecast_run_repo.latest_risk_origin(sys_config_repo.effective_now())
    return {"origin": None if origin is None else f"{origin:%Y-%m-%d %H:%M:%S}",
            "items": [_order_item(o) for o in dispatch_repo.list_by_status(status)]}


def create_orders(anchor_uid: str, action: str, items: list[dict]) -> dict:
    """使用者按下「確認調度」。★ 驗證失敗整批 400，不做 clamp。

    ★ 為什麼不用「origin 必須等於最新一輪」當門檻：60x 回放下一格 = 30 真實
      秒，使用者讀完文案再勾選早已跨輪 → 幾乎必定失敗。改成前端只在 1x 開放
      調度（按鈕層級禁用），這裡用**最新一輪重驗**：候選名單當場重算一次，
      使用者選的站必須仍在名單內、台數仍在餘裕內。

    ★ 為什麼不 clamp：把 6 台自動改成 4 台送出去，調度員會以為派了 6 台。
      寧可整批退回讓他重問，也不要默默改掉他按下的數字。
    """
    if not items:
        raise AppError("DISPATCH_EMPTY", 400, "沒有選擇任何調度來源")

    origin = forecast_run_repo.latest_risk_origin(sys_config_repo.effective_now())
    if origin is None:
        raise AppError("NO_RISK_SNAPSHOT", 422, "還沒有任何一輪風險判定")

    anchor = dispatch_repo.anchor_row(origin, anchor_uid)
    if anchor is None:
        raise AppError("STATION_NOT_FOUND", 404, f"查無站點 {anchor_uid}")

    act = anchor["action"]
    if act not in _DIR:
        raise AppError("DISPATCH_STALE", 400, STALE_MSG)
    if action != act:
        # 風險跨輪翻面（本來缺車、現在滿站）—— 前端帶的 action 已過期
        raise AppError("DISPATCH_STALE", 400, STALE_MSG)

    direction, exclude = _DIR[act]
    rows = {c["station_uid"]: c for c in
            dispatch_repo.candidate_rows(origin, anchor_uid, direction,
                                         exclude, config.DISPATCH_MIN_BIKES)}
    # 本 anchor 已經佔走的量要加回上界，否則「3 台改成 5 台」會被自己擋掉
    #   —— promised 已把那 3 台從 supply 扣掉了，這筆 upsert 是覆蓋不是疊加。
    mine = {}
    for o in dispatch_repo.by_anchor(anchor_uid):
        other = o["from_uid"] if direction == 1 else o["to_uid"]
        mine[other] = mine.get(other, 0) + o["bikes"]

    here = (anchor["lat"], anchor["lon"])
    rows_to_write = []
    for it in items:
        uid, bikes = it.get("uid"), it.get("bikes")
        if not isinstance(bikes, int) or bikes < config.DISPATCH_MIN_BIKES:
            raise AppError("DISPATCH_STALE", 400,
                           f"每筆台數至少 {config.DISPATCH_MIN_BIKES} 台")
        c = rows.get(uid)
        if c is None:
            # 三種可能：站不在最新一輪／已轉成反向 action／餘裕已被別人佔光。
            # 對使用者是同一件事「這站現在不能調了」，不必分辨。
            raise AppError("DISPATCH_STALE", 400, STALE_MSG)
        if bikes > int(c["supply"]) + mine.get(uid, 0):
            raise AppError("DISPATCH_STALE", 400, STALE_MSG)

        dist = haversine_m(here, (c["lat"], c["lon"]))
        frm, to = (uid, anchor_uid) if direction == 1 else (anchor_uid, uid)
        rows_to_write.append({
            "from_uid": frm, "to_uid": to, "bikes": bikes,
            "distance_m": None if dist is None else round(dist),
            "anchor_uid": anchor_uid, "action": act,
            "created_slot": sys_config_repo.effective_now(), "origin": origin})

    ids = dispatch_repo.insert_many(rows_to_write)
    return {"written": len(ids), "origin": f"{origin:%Y-%m-%d %H:%M:%S}", "ids": ids}


def cancel(order_id: int) -> dict:
    """人工撤銷 → invalid（軟刪，不 DELETE）。"""
    o = dispatch_repo.get(order_id)
    if o is None:
        raise AppError("DISPATCH_NOT_FOUND", 404, f"查無調度單 {order_id}")
    if o["status"] != "active":
        raise AppError("DISPATCH_NOT_ACTIVE", 409,
                       f"調度單 {order_id} 已是 {o['status']}，不能再撤銷")
    n = dispatch_repo.close(order_id, "invalid", "人工撤銷（IM_TEST）",
                            sys_config_repo.effective_now())
    return {"cancelled": n, "id": order_id}


# ════════════════════════════════════════════════════════════
# 冒煙：uv run python -m app.service.dispatch_service
#   拿最新一輪真的有 action 的站來跑，逐條斷言 §4-3 的分層規則。
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    from app.repository.db import get_conn

    o = forecast_run_repo.latest_risk_origin(sys_config_repo.effective_now())
    print(f"── 最新一輪 origin = {o}")

    with get_conn().cursor() as c:
        c.execute("SELECT station_uid, action, bikes FROM hackathon_backend_risk_snapshot "
                  " WHERE origin = %s AND action IN ('refill','remove') AND bikes > 0 "
                  " ORDER BY bikes DESC LIMIT 4", (o,))
        targets = c.fetchall()
    assert targets, "最新一輪沒有任何需要調度的站，無法冒煙"

    for t in targets:
        r = candidates(t["station_uid"], origin=o)
        a = r["anchor"]
        print(f"\n── {a['name']}（{a['town']}）{a['action']} need={a['need']} "
              f"already={a['already']} shortfall={r['shortfall']}")

        sel = [i for i in r["items"] if i["selected"]]
        print(f"   候選 {len(r['items'])} 筆，勾選 {len(sel)} 筆，"
              f"合計 {sum(i['bikes'] for i in sel)} 台")
        for i in r["items"][:4]:
            mark = "✔" if i["selected"] else " "
            xt = "［跨區］" if i["cross_town"] else ""
            print(f"   {mark} {i['name']}（{i['town']}）{xt}"
                  f" supply={i['supply']} 搬 {i['bikes']} 台 "
                  f"{i['distance_m']}m {i['note'] or ''}")

        # 驗收 7：三個排除條件
        assert all(i["supply"] >= config.DISPATCH_MIN_BIKES for i in r["items"]), \
            "出現 supply < 2 的候選"
        assert all(i["distance_m"] is not None for i in r["items"]), \
            "出現無座標的候選"
        # 勾選的合計 + shortfall 必須等於 need
        assert sum(i["bikes"] for i in sel) + r["shortfall"] == a["need"], \
            (sum(i['bikes'] for i in sel), r["shortfall"], a["need"])
        # 未勾選的排在勾選之後
        flags = [i["selected"] for i in r["items"]]
        assert flags == sorted(flags, reverse=True), "勾選的沒有排在前面"
        # 案丙：若有一趟搬得完的站被選中，就只該選一站
        if len(sel) == 1 and a["need"] > 0:
            assert sel[0]["supply"] >= a["need"] or r["shortfall"] > 0
        # 未勾選那段依距離遞增
        rest = [i["distance_m"] for i in r["items"] if not i["selected"]]
        assert rest == sorted(rest), f"未勾選段沒有依距離遞增：{rest}"
        print("   ✓ 排除條件／台數守恆／排序 全部通過")

    print("\n── 邊界：對 action=hold 或無風險的站發起 → 應被擋")
    with get_conn().cursor() as c:
        c.execute("SELECT station_uid FROM hackathon_backend_risk_snapshot "
                  " WHERE origin = %s AND (action IS NULL OR action = 'hold') LIMIT 1", (o,))
        quiet = c.fetchone()
    if quiet:
        try:
            candidates(quiet["station_uid"], origin=o)
            raise SystemExit("✗ 無風險站竟然發得出調度")
        except AppError as e:
            print(f"   {e.code}：{e.message} ✓")
