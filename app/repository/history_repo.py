# ════════════════════════════════════════════════════════════
# history_repo —— hackathon_backend_level30 → 48 格水位歷程
#
# ★ 缺格（該 slot 無列或 avail IS NULL）回 None，絕不補 0 ——
#   0 會被模型當成「真的沒車」（payload 規則第 4 條，安靜錯誤）。
# 不足 48 格的判定交給 service：回傳 first_slot 讓它比對視窗起點，
# 因為「站的歷史根本沒到那麼早」與「視窗中間有缺格」都以 None 呈現，
# 只有 first_slot 能區分兩者。
# ════════════════════════════════════════════════════════════
from datetime import datetime, timedelta

from app import config
from app.repository.db import get_conn


def tail(station_uid: str, at: datetime | None = None,
         n: int = config.CONTEXT) -> dict | None:
    """回傳 {"start", "anchor", "first_slot", "values"(長度 n, 缺格 None)}；
    at 之前完全沒有資料時回 None。anchor = 序列最末格 =「當下」。"""
    with get_conn().cursor() as cur:
        if at is None:
            cur.execute("SELECT max(slot) AS a, min(slot) AS f "
                        "FROM hackathon_backend_level30 WHERE station_uid = %s",
                        (station_uid,))
        else:
            cur.execute("SELECT max(slot) FILTER (WHERE slot <= %s) AS a, min(slot) AS f "
                        "FROM hackathon_backend_level30 WHERE station_uid = %s",
                        (at, station_uid))
        row = cur.fetchone()
        anchor, first_slot = row["a"], row["f"]
        if anchor is None:
            return None
        start = anchor - timedelta(minutes=config.FREQ_MIN * (n - 1))
        cur.execute(
            "SELECT l.avail FROM generate_series(%s::timestamp, %s::timestamp, "
            "                                    make_interval(mins => %s)) AS g(slot) "
            "LEFT JOIN hackathon_backend_level30 l "
            "       ON l.station_uid = %s AND l.slot = g.slot "
            "ORDER BY g.slot",
            (start, anchor, config.FREQ_MIN, station_uid))
        values = [r["avail"] for r in cur.fetchall()]
    return {"start": start, "anchor": anchor, "first_slot": first_slot, "values": values}



def anchor_all(at: datetime) -> dict[str, dict]:
    """批次版：全站在 at 當下的「現況可借量」，回 {uid: {"slot", "avail"}}。

    給 Job B 的風險判定用（風險等級最重的那一級靠的是**實測現況**）。
    取 slot <= at 且 avail 非 NULL 的最末一格 —— 等於 day_view 的
    carry-forward 語意：回傳的 slot < at 就是「延用前值」（now_carried）。
    ★ 一次 round-trip；逐站呼叫 tail() 是 1,538 次，不可以。
    """
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (station_uid) station_uid, slot, avail "
            "  FROM hackathon_backend_level30 "
            " WHERE slot <= %s AND avail IS NOT NULL "
            " ORDER BY station_uid, slot DESC", (at,))
        return {r["station_uid"]: {"slot": r["slot"], "avail": r["avail"]}
                for r in cur.fetchall()}

if __name__ == "__main__":            # 驗證：計劃-後端服務.md §8
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--station", required=True)
    p.add_argument("--at", default=None)
    a = p.parse_args()
    at = datetime.fromisoformat(a.at) if a.at else None
    r = tail(a.station, at)
    if r is None:
        raise SystemExit("✗ 該站在指定時刻之前沒有任何資料")
    print(f"start      {r['start']}\nanchor     {r['anchor']}\nfirst_slot {r['first_slot']}")
    print(f"values({len(r['values'])})  {r['values']}")
    n_null = sum(v is None for v in r["values"])
    ok_len = len(r["values"]) == config.CONTEXT
    ok_cov = r["first_slot"] <= r["start"]
    print(f"長度 48：{'✓' if ok_len else '✗'}   缺格(None)：{n_null}   "
          f"歷史涵蓋視窗起點：{'✓' if ok_cov else '✗（INSUFFICIENT_HISTORY）'}")
