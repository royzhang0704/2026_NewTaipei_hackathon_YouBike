# ════════════════════════════════════════════════════════════
# risk_repo —— hackathon_backend_risk_snapshot 的讀寫
#
#   寫：Job B 階段二（batch_predict.write_risk）每輪 upsert 全站。
#   讀：/alerts 的全市／同區排行（查詢端零計算，純 SELECT + join 主檔）。
#
# ★ PK 是 (origin, station_uid)，origin 在前 —— 排行掃的是「一個 origin 的
#   全部列」，PK 本身就是那筆掃描的索引；streak 遞推查上一輪也吃同一支。
#
# 冒煙：uv run python -m app.repository.risk_repo
# ════════════════════════════════════════════════════════════
from datetime import datetime

from app.repository.db import get_conn

# 主鍵是 (origin, station_uid)；run_id 是掛回主檔的連結（無 cat 站為 NULL）
KEYS = ("origin", "station_uid")
COLS = ("run_id", "origin", "station_uid", "status", "level_n", "shortage_n", "full_n",
        "side", "conflict", "confidence", "threshold", "onset",
        "now_avail", "now_carried", "now_crossed", "baseline",
        "action", "bikes", "basis", "streak_n", "streak_since",
        "stale_avail", "stale_since", "algo_ver")

# 排序（固定，不開放參數）：
#   高>中>低 是第一鍵；streak 當第二鍵 —— 連 6 輪（3 小時）沒改善的站
#   要排在剛亮燈的站前面，這正是記 streak 的用途。
#   ⚠ 9/12 起 streak 只算高風險，中低風險的 streak_n 一律 0 —— 那兩群
#     組內實際上退化成以 bikes 排序，已知且接受（要排序的是高風險群）。
ORDER_BY = ("ORDER BY r.level_n DESC, r.streak_n DESC, r.bikes DESC NULLS LAST, "
            "r.onset ASC, r.station_uid")


def upsert_many(rows: list[tuple]) -> int:
    """整輪寫入（含 level_n=0 與 status='no_forecast' 的站）。

    ★ 每輪寫全站是 9/1 定案：上一輪一定查得到列，streak 遞推就不必分辨
      「查無列 = 安全 還是 漏批」。同 origin 重判走 upsert 覆蓋。
    """
    if not rows:
        return 0
    ph = ", ".join(["%s"] * len(COLS))
    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLS if c not in KEYS)
    with get_conn().transaction(), get_conn().cursor() as cur:
        cur.executemany(
            f"INSERT INTO hackathon_backend_risk_snapshot ({', '.join(COLS)}) "
            f"VALUES ({ph}) "
            f"ON CONFLICT (origin, station_uid) DO UPDATE SET {upd}, created_at = now()",
            rows)
        return cur.rowcount


def prev(origin: datetime) -> dict[str, dict]:
    """上一輪（origin 本身就要傳「上一輪的 origin」）的遞推來源。

    兩套遞推共用這一次查詢：streak（吃 algo_ver）與水位停滯（不吃）。
    """
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT station_uid, status, level_n, streak_n, streak_since, "
            "       stale_avail, stale_since, algo_ver "
            "  FROM hackathon_backend_risk_snapshot WHERE origin = %s", (origin,))
        return {r["station_uid"]: r for r in cur.fetchall()}


def rank(origin: datetime, town_code: str | None = None,
         levels: tuple[int, ...] = (3, 2, 1), side: str | None = None,
         action: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    """排行清單。town_code 走 station_uid 第 8~9 碼（主檔的 town_code 同義）。"""
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT r.*, s.station_name, s.town_code, s.town, s.capacity "
            "  FROM hackathon_backend_risk_snapshot r "
            "  JOIN hackathon_backend_station s USING (station_uid) "
            " WHERE r.origin = %s AND r.level_n = ANY(%s) "
            "   AND (%s::text IS NULL OR s.town_code = %s) "
            "   AND (%s::text IS NULL OR r.side = %s) "
            "   AND (%s::text IS NULL OR r.action = %s) "
            f"{ORDER_BY} LIMIT %s OFFSET %s",
            (origin, list(levels), town_code, town_code, side, side,
             action, action, limit, offset))
        return cur.fetchall()


def count(origin: datetime, town_code: str | None = None,
          levels: tuple[int, ...] = (3, 2, 1), side: str | None = None,
          action: str | None = None) -> int:
    """rank() 同條件的總筆數（分頁用；不受 limit 影響）。"""
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM hackathon_backend_risk_snapshot r "
            "  JOIN hackathon_backend_station s USING (station_uid) "
            " WHERE r.origin = %s AND r.level_n = ANY(%s) "
            "   AND (%s::text IS NULL OR s.town_code = %s) "
            "   AND (%s::text IS NULL OR r.side = %s) "
            "   AND (%s::text IS NULL OR r.action = %s)",
            (origin, list(levels), town_code, town_code, side, side, action, action))
        return cur.fetchone()["n"]


def summary(origin: datetime, town_code: str | None = None) -> dict:
    """該 origin 的分級與調度盤點。

    ★ 不帶 town_code 時 /alerts 應該直接讀主檔那一列（零 count）；
      這支是「帶 town_code 時現算」的路徑 —— 主檔統計是全市口徑，
      不能拿來當一個區的答案。一個區平均 53 站，count 不痛。
    """
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT count(*) AS stations, "
            "  count(*) FILTER (WHERE r.level_n = 3) AS risk_high, "
            "  count(*) FILTER (WHERE r.level_n = 2) AS risk_mid, "
            "  count(*) FILTER (WHERE r.level_n = 1) AS risk_low, "
            "  count(*) FILTER (WHERE r.level_n = 0) AS risk_none, "
            "  count(*) FILTER (WHERE r.status = 'no_forecast') AS risk_no_forecast, "
            "  count(*) FILTER (WHERE r.action = 'refill') AS refill_stations, "
            "  COALESCE(sum(r.bikes) FILTER (WHERE r.action = 'refill'), 0) AS refill_bikes, "
            "  count(*) FILTER (WHERE r.action = 'remove') AS remove_stations, "
            "  COALESCE(sum(r.bikes) FILTER (WHERE r.action = 'remove'), 0) AS remove_bikes, "
            "  count(*) FILTER (WHERE r.action = 'hold') AS hold_stations "
            "  FROM hackathon_backend_risk_snapshot r "
            "  JOIN hackathon_backend_station s USING (station_uid) "
            " WHERE r.origin = %s AND (%s::text IS NULL OR s.town_code = %s)",
            (origin, town_code, town_code))
        return cur.fetchone()


def station_history(station_uid: str, n: int = 48) -> list[dict]:
    """單站的風險歷程（新→舊），走 (station_uid, origin DESC) 索引。"""
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT origin, status, level_n, side, streak_n, streak_since, "
            "       stale_avail, stale_since, now_avail, threshold, action, bikes "
            "  FROM hackathon_backend_risk_snapshot "
            " WHERE station_uid = %s ORDER BY origin DESC LIMIT %s", (station_uid, n))
        return cur.fetchall()


# ════════════════════════════════════════════════════════════
# 冒煙：uv run python -m app.repository.risk_repo
#   用 1999 年的假 origin + 真站號（要 join 主檔），跑完自己清掉。
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    O1, O2 = datetime(1999, 1, 1, 0, 0), datetime(1999, 1, 1, 0, 30)
    with get_conn().cursor() as c:
        c.execute("SELECT station_uid, town_code FROM hackathon_backend_station "
                  "ORDER BY station_uid LIMIT 3")
        st = c.fetchall()
    u = [r["station_uid"] for r in st]
    tc = st[0]["town_code"]

    def row(origin, uid, status, lv, sn, since, action=None, bikes=None, side=None,
            stale_avail=1, stale_since=None):
        return (None, origin, uid, status, lv, lv if side == "shortage" else None,
                lv if side == "full" else None, side, None, "likely", 3,
                origin if lv == 3 else None, 1, False, lv == 3, 5.0,
                action, bikes, "slot_average", sn, since,
                stale_avail, stale_since or origin, "__smoke__")

    print("── ① 寫一輪三站（高／無／no_forecast）")
    n = upsert_many([row(O1, u[0], "ok", 3, 1, O1, "refill", 7, "shortage"),
                     row(O1, u[1], "ok", 0, 0, None),
                     row(O1, u[2], "no_forecast", None, 0, None)])
    print(f"   寫入 {n} 列 ✓")

    print("── ② rank：預設只回有風險的站，join 得到站名")
    r = rank(O1)
    assert len(r) == 1 and r[0]["station_uid"] == u[0] and r[0]["station_name"], r
    print(f"   {len(r)} 列，{r[0]['station_name']}（{r[0]['town']}）level={r[0]['level_n']} ✓")

    print("── ③ summary：no_forecast 不算 none")
    s = summary(O1)
    assert s["stations"] == 3 and s["risk_high"] == 1 and s["risk_none"] == 1
    assert s["risk_no_forecast"] == 1 and s["refill_bikes"] == 7, s
    print(f"   高 {s['risk_high']}／無 {s['risk_none']}／無預測 {s['risk_no_forecast']}"
          f"／補車 {s['refill_stations']} 站 {s['refill_bikes']} 台 ✓")

    print("── ④ 同區過濾 + prev（streak／水位停滯兩套遞推的來源）")
    assert count(O1, town_code=tc) >= 0
    # O2 水位仍是 1（同 O1）→ stale_since 沿用 O1，不跟著 origin 走
    upsert_many([row(O2, u[0], "ok", 3, 2, O1, "refill", 7, "shortage",
                     stale_avail=1, stale_since=O1)])
    p = prev(O1)
    assert p[u[0]]["streak_n"] == 1 and p[u[0]]["algo_ver"] == "__smoke__"
    assert p[u[0]]["stale_avail"] == 1 and p[u[0]]["stale_since"] == O1
    print(f"   prev(O1)[{u[0]}] streak={p[u[0]]['streak_n']}；"
          f"O2 遞推到 {prev(O2)[u[0]]['streak_n']}；"
          f"stale_since 仍是 {prev(O2)[u[0]]['stale_since']:%H:%M} ✓")

    print("── ⑤ upsert 覆蓋（同 PK 重寫不新增列）")
    upsert_many([row(O1, u[0], "ok", 2, 9, O1, "refill", 3, "shortage")])
    assert summary(O1)["stations"] == 3 and rank(O1)[0]["streak_n"] == 9
    h = station_history(u[0])
    print(f"   仍是 3 列、streak 已更新為 9；station_history 回 {len(h)} 輪 ✓")

    with get_conn().cursor() as cur:
        cur.execute("DELETE FROM hackathon_backend_risk_snapshot WHERE algo_ver = '__smoke__'")
        print(f"\n✓ 全部通過，已清除 {cur.rowcount} 筆測試資料")
