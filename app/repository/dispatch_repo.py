# ════════════════════════════════════════════════════════════
# dispatch_repo —— hackathon_backend_dispatch_order 的讀寫
#
#   寫：POST /dispatch/orders（使用者按「確認調度」）、jobs/dispatch_sweep 收單。
#   讀：GET /dispatch/orders（地圖線 + 清單）、行動卡「已調度 N 台」、
#       候選演算法的「已承諾量」扣除。
#
# ★ 與 risk_repo 的定位差別：那張表每輪可重算可 TRUNCATE，這張是使用者
#   按下確認的事實 —— 一律軟刪（status='invalid'），沒有 DELETE 函式。
#
# ★ 所有 *_slot 一律傳虛擬時鐘（sys_config_repo.effective_now()）。
#   本模組不自己呼叫 effective_now，由呼叫端傳入 —— sweep 一輪收 N 筆
#   要用同一個時刻，在這裡各自取會得到 N 個不同的虛擬時間。
#
# 冒煙：uv run python -m app.repository.dispatch_repo
# ════════════════════════════════════════════════════════════
from datetime import datetime

from app.repository.db import get_conn

TABLE = "hackathon_backend_dispatch_order"

# operator 不在此列 —— 一律吃 DB 預設值 IM_TEST，不接受呼叫端傳值（見 sql/70 欄註解）
COLS = ("from_uid", "to_uid", "bikes", "distance_m",
        "anchor_uid", "action", "created_slot", "origin")

# 兩端都要 join 主檔補站名與座標（地圖要畫線，清單要顯示名字）
_JOIN_SELECT = f"""
SELECT d.id, d.bikes, d.distance_m, d.anchor_uid, d.action, d.status,
       d.closed_reason, d.created_slot, d.closed_slot, d.origin, d.operator,
       d.from_uid, f.station_name AS from_name, f.town AS from_town,
       f.lat AS from_lat, f.lon AS from_lon,
       d.to_uid,   t.station_name AS to_name,   t.town AS to_town,
       t.lat AS to_lat,   t.lon AS to_lon
  FROM {TABLE} d
  LEFT JOIN hackathon_backend_station f ON f.station_uid = d.from_uid
  LEFT JOIN hackathon_backend_station t ON t.station_uid = d.to_uid
"""


def insert_many(rows: list[dict]) -> list[int]:
    """寫入一批調度單，回傳 id 清單（順序同輸入）。

    ★★ 2026-09-12 改為**疊加**（原本是覆蓋）。
      出處：meet/20260912/計劃-同站重複調度會覆蓋.md 方案 B。

      舊制 `bikes = EXCLUDED.bikes` 的語意是「同一輪內改主意，3 台改成 5 台
      就該是同一筆」。但索引不含 origin，所以**跨輪的第二趟車**也走同一條
      路徑 —— 第 1 輪 A→B 派 6 台、第 2 輪再派 2 台，結果是 2 台不是 8 台，
      第一趟的 4 台憑空消失，`already` / `promised` 跟著少扣，A 站被當成
      還有餘裕，可能再被超賣一次。

      現在 `bikes = 本表.bikes + EXCLUDED.bikes`：每次確認都是「再追加一趟」。
      ⚠ 代價講明：**「改主意」沒有了** —— 同一對站再送一次一律相加，
        3 台改成 5 台會變成 8 台。要減量請撤銷（DELETE /dispatch/orders/{id}）
        再重下。相對應地，寫入驗證那邊的「把本 anchor 已佔走的量加回上界」
        必須拿掉（見 dispatch_service.create_orders），否則會放行超賣。

    ★ 其餘欄位維持 EXCLUDED（覆蓋成最新值）：origin / created_slot 因此代表
      **最後一次追加是哪一輪、哪個虛擬時刻**，不是第一趟。溯源要看整段歷程
      請查 job_run 與 id 區間。

    ★ conflict target 的 WHERE 必須與 active_pair_idx 的 predicate 一字不差，
      否則 Postgres 找不到對應索引會直接報 no unique constraint matching。

    ★ 逐筆 execute 不用 executemany：一次確認最多 10 筆（§4-3 給前端的上限），
      批次省下的往返遠不如 RETURNING id 要拆 nextset 的麻煩。
    """
    if not rows:
        return []
    ph = ", ".join(["%s"] * len(COLS))
    # bikes 疊加，其餘覆蓋。★ 用表名限定（不是 EXCLUDED）才是「已經在表裡的那筆」。
    upd = ", ".join(f"bikes = {TABLE}.bikes + EXCLUDED.bikes" if c == "bikes"
                    else f"{c} = EXCLUDED.{c}"
                    for c in COLS if c not in ("from_uid", "to_uid"))
    sql = (f"INSERT INTO {TABLE} ({', '.join(COLS)}) VALUES ({ph}) "
           f"ON CONFLICT (from_uid, to_uid) WHERE status = 'active' "
           f"DO UPDATE SET {upd} RETURNING id")
    ids = []
    with get_conn().transaction(), get_conn().cursor() as cur:
        for r in rows:
            cur.execute(sql, tuple(r.get(c) for c in COLS))
            ids.append(cur.fetchone()["id"])
    return ids


def list_by_status(status: str = "active") -> list[dict]:
    """整批撈某個狀態的單（走 status_anchor_idx）。

    ★ 不吃 town_code —— 地圖的調度線刻意不套區篩選（跨區調度的兩端本來
      就分屬不同區，篩掉任一端線就斷了）。前端一次全撈，區的切換純前端。
    """
    with get_conn().cursor() as cur:
        cur.execute(_JOIN_SELECT + " WHERE d.status = %s ORDER BY d.id", (status,))
        return cur.fetchall()


def by_anchor(anchor_uid: str, status: str = "active") -> list[dict]:
    """某個風險站發起的單 —— 行動卡「已調度 N 台 · 來自 XX、YY」用。"""
    with get_conn().cursor() as cur:
        cur.execute(_JOIN_SELECT + " WHERE d.status = %s AND d.anchor_uid = %s "
                                   " ORDER BY d.id", (status, anchor_uid))
        return cur.fetchall()


def promised(direction: int) -> dict[str, int]:
    """各站已被 active 單佔走的台數 —— 候選演算法要從 supply 扣掉。

    ★ 來源站是共享資源：A 站多出 7 台，已答應調 6 台給 B，此刻對 C 只剩
      1 台可用。不扣就會出現「兩張單都說從 A 調 6 台」的超賣。

    direction  +1 = anchor 缺車（看 from_uid，誰被抽走了車）
               −1 = anchor 滿站（看 to_uid，誰已經被塞了車）
    """
    col = "from_uid" if direction == 1 else "to_uid"
    with get_conn().cursor() as cur:
        cur.execute(f"SELECT {col} AS uid, sum(bikes)::int AS taken "
                    f"  FROM {TABLE} WHERE status = 'active' GROUP BY 1")
        return {r["uid"]: r["taken"] for r in cur.fetchall()}


def anchor_row(origin: datetime, station_uid: str) -> dict | None:
    """發起站在某一輪的風險現況 + 主檔欄位（發起資格與 need 的來源）。"""
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT r.station_uid, s.station_name, s.town, s.town_code, s.lat, s.lon, "
            "       r.now_avail, r.baseline, r.level_n, r.side, r.action, r.bikes "
            "  FROM hackathon_backend_risk_snapshot r "
            "  JOIN hackathon_backend_station s USING (station_uid) "
            " WHERE r.origin = %s AND r.station_uid = %s", (origin, station_uid))
        return cur.fetchone()


def candidate_rows(origin: datetime, anchor_uid: str, direction: int,
                   exclude_action: str, min_bikes: int) -> list[dict]:
    """一次撈全市的候選站（分層與排序留給 dispatch_service，這裡只管 SQL）。

    ★ 不吃 town_code —— 同區優先是 Python 那層的事；SQL 先把全市撈回來，
      同區湊不滿才要擴到跨區，分兩次查反而多跑一趟。1.5k 站的量不痛。

    supply（可用量）＝ (現況 − 該時段常態) × direction，再扣掉已承諾量：
      direction=+1  anchor 缺車，候選當取車來源，要「現況高於常態」
      direction=−1  anchor 滿站，候選當補車目標，要「現況低於常態」

    ★ 為什麼是 floor 不是 round／ceil：risk_service._dispatch() 算 anchor
      的需求用 ceil（寧可多補一台），供給側就該對稱地保守。多搬一台會讓
      來源站掉到常態水位以下，下一輪可能反被判缺車 → 這張單隔輪就被
      dispatch_sweep 規則②作廢。

    三個排除條件各有出處：
      baseline IS NULL  沒有歷史常態就沒有「高於」可言（樣本 n < 10）
      s.lat IS NULL     無座標站排不了距離，暫時排除
      exclude_action    候選自己也被判成同方向 → 它自己就缺車／滿站，
                        不能再被抽（§13：判準一律用 action 不用 level）
    """
    supply = ("floor((r.now_avail - r.baseline) * %(dir)s)::int "
              "  - COALESCE(p.taken, 0)")
    promised_col = "from_uid" if direction == 1 else "to_uid"
    with get_conn().cursor() as cur:
        cur.execute(
            f"WITH promised AS ("
            f"  SELECT {promised_col} AS uid, sum(bikes)::int AS taken "
            f"    FROM {TABLE} WHERE status = 'active' GROUP BY 1) "
            f"SELECT r.station_uid, s.station_name, s.town, s.town_code, s.lat, s.lon, "
            f"       r.now_avail, r.baseline, r.level_n, r.side, r.action, "
            f"       {supply} AS supply "
            f"  FROM hackathon_backend_risk_snapshot r "
            f"  JOIN hackathon_backend_station s USING (station_uid) "
            f"  LEFT JOIN promised p ON p.uid = r.station_uid "
            f" WHERE r.origin = %(origin)s "
            f"   AND r.station_uid <> %(anchor)s "
            f"   AND r.baseline IS NOT NULL "
            f"   AND s.lat IS NOT NULL "
            f"   AND (r.action IS NULL OR r.action <> %(exclude)s) "
            f"   AND {supply} >= %(min_bikes)s",
            {"origin": origin, "anchor": anchor_uid, "dir": direction,
             "exclude": exclude_action, "min_bikes": min_bikes})
        return cur.fetchall()


def get(order_id: int) -> dict | None:
    with get_conn().cursor() as cur:
        cur.execute(_JOIN_SELECT + " WHERE d.id = %s", (order_id,))
        return cur.fetchone()


def close(order_id: int, status: str, reason: str, closed_slot: datetime) -> int:
    """軟刪一筆：active → fulfilled / invalid。

    ★ WHERE 帶 status='active' —— 已收掉的單不該被二次覆蓋（sweep 與人工
      撤銷可能同時發生）。回傳 0 表示這筆已經不是 active，呼叫端據此判斷。
    """
    with get_conn().cursor() as cur:
        cur.execute(f"UPDATE {TABLE} SET status = %s, closed_reason = %s, "
                    f"       closed_slot = %s "
                    f" WHERE id = %s AND status = 'active'",
                    (status, reason, closed_slot, order_id))
        return cur.rowcount


def close_many(items: list[tuple], closed_slot: datetime) -> int:
    """批次收單，items = [(id, status, reason), ...]，共用同一個虛擬時刻。"""
    if not items:
        return 0
    n = 0
    with get_conn().transaction():
        for oid, status, reason in items:
            n += close(oid, status, reason, closed_slot)
    return n


# ════════════════════════════════════════════════════════════
# 冒煙：uv run python -m app.repository.dispatch_repo
#   用 1999 年的假 slot + 真站號（要 join 主檔），跑完自己清掉。
#   ⚠ 這支會寫真表，但只寫 anchor_uid='__smoke__' 的列，自清以此為條件。
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    S1 = datetime(1999, 1, 1, 0, 0)
    S2 = datetime(1999, 1, 1, 0, 30)
    MARK = "__smoke__"

    with get_conn().cursor() as c:
        c.execute("SELECT station_uid, station_name FROM hackathon_backend_station "
                  " WHERE lat IS NOT NULL ORDER BY station_uid LIMIT 3")
        st = c.fetchall()
    u = [r["station_uid"] for r in st]

    def row(frm, to, bikes):
        return {"from_uid": frm, "to_uid": to, "bikes": bikes, "distance_m": 620,
                "anchor_uid": MARK, "action": "refill",
                "created_slot": S1, "origin": S1}

    try:
        print("── ① 寫兩筆（u0→u2、u1→u2），拿到 id")
        ids = insert_many([row(u[0], u[2], 3), row(u[1], u[2], 4)])
        assert len(ids) == 2 and all(isinstance(i, int) for i in ids), ids
        print(f"   ids={ids} ✓")

        print("── ② join 主檔：兩端站名與座標都補到")
        rows = [r for r in list_by_status("active") if r["anchor_uid"] == MARK]
        assert len(rows) == 2, len(rows)
        r0 = rows[0]
        assert r0["from_name"] and r0["to_name"] and r0["from_lat"] is not None, r0
        print(f"   {r0['from_name']} → {r0['to_name']}（{r0['bikes']} 台）✓")

        print("── ③ operator 吃 DB 預設值，呼叫端無法傳值")
        assert r0["operator"] == "IM_TEST", r0["operator"]
        print(f"   operator={r0['operator']} ✓")

        print("── ④ 同一對重複寫入 → 仍一列，台數**疊加**（9/12 改，原為覆蓋）")
        ids2 = insert_many([row(u[0], u[2], 9)])
        rows = [r for r in list_by_status("active") if r["anchor_uid"] == MARK]
        assert ids2[0] == ids[0], (ids2, ids)
        assert len(rows) == 2, f"重複寫入不該多一列，實得 {len(rows)}"
        # ★ 3 + 9 = 12。若這裡拿到 9，表示 DO UPDATE 又變回覆蓋了 ——
        #   那正是「第一趟的台數憑空消失」的病徵，整個調度總量會少算。
        assert next(r["bikes"] for r in rows if r["id"] == ids[0]) == 12
        print(f"   id={ids2[0]} 不變，bikes 3 + 9 = 12 ✓")

        print("── ⑤ promised：dir=+1 看 from_uid（誰的車被抽走）")
        p = promised(1)
        assert p.get(u[0], 0) >= 12 and p.get(u[1], 0) >= 4, p
        print(f"   {u[0][-4:]} 已承諾 {p[u[0]]} 台、{u[1][-4:]} {p[u[1]]} 台 ✓")

        print("── ⑥ promised：dir=−1 看 to_uid（誰已被塞車）")
        p2 = promised(-1)
        assert p2.get(u[2], 0) >= 16, p2
        print(f"   {u[2][-4:]} 已被塞 {p2[u[2]]} 台 ✓")

        print("── ⑦ by_anchor：行動卡「已調度 N 台」的來源")
        mine = by_anchor(MARK)
        assert len(mine) == 2 and sum(r["bikes"] for r in mine) == 16, mine
        print(f"   {len(mine)} 筆、合計 {sum(r['bikes'] for r in mine)} 台 ✓")

        print("── ⑧ close：軟刪不 DELETE，且只收 active")
        n = close(ids[0], "fulfilled", "目標站已無補車需求", S2)
        assert n == 1
        again = close(ids[0], "invalid", "不該再被收一次", S2)
        assert again == 0, "已收掉的單被二次覆蓋了"
        g = get(ids[0])
        assert g["status"] == "fulfilled" and g["closed_slot"] == S2, g
        assert g["closed_reason"] == "目標站已無補車需求"
        print(f"   status={g['status']}、closed_slot={g['closed_slot']:%H:%M}、二次收單擋下 ✓")

        print("── ⑨ 收掉後同一對可再派（部分唯一索引只擋 active）")
        ids3 = insert_many([row(u[0], u[2], 5)])
        assert ids3[0] != ids[0], "舊單收掉後應開新單"
        print(f"   新 id={ids3[0]}（舊 {ids[0]} 已 fulfilled）✓")

        print("── ⑩ close_many：一輪共用同一個虛擬時刻")
        left = [r["id"] for r in by_anchor(MARK)]
        n = close_many([(i, "invalid", "冒煙清理") for i in left], S2)
        assert n == len(left), (n, left)
        assert by_anchor(MARK) == [], "應已無 active"
        print(f"   收掉 {n} 筆 ✓")

    finally:
        with get_conn().cursor() as c:
            c.execute(f"DELETE FROM {TABLE} WHERE anchor_uid = %s", (MARK,))
            print(f"── 自清：刪除 {c.rowcount} 列 ✓")
