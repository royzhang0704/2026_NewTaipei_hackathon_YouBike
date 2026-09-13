# hackathon_backend_station_slot_average 的唯讀查詢 —— 調度台數的基準值。
#   「該站 × 平日假日 × 該時刻」的歷史平均可借量（統計至 2026-04-30 訓練截止日）。
#   建表腳本 sql/50_station_slot_average.sql。
from datetime import datetime

from app.repository.db import get_conn


def series(station_uid: str, ts_list: list[datetime]) -> dict[datetime, dict]:
    """一次取多個時刻的歷史平均，回 {ts: {avg_avail, n}}。查無該桶就不出現在
       結果裡（5 站訓練期完全沒觀測、另有 1,629 桶沒有樣本）。

       ★ 一定要逐格帶日期、不能只帶 time —— 預測窗會跨午夜（origin 21:30 的
         窗尾是隔天 00:00），跨過去那格的平日／假日可能就不一樣了。
       日別由 hackathon_backend_calendar 決定，已驗證與建表用的 dim_calendar
       1,461 天假日旗完全一致。"""
    if not ts_list:
        return {}
    vals = ", ".join(["(%s::timestamp)"] * len(ts_list))
    with get_conn().cursor() as cur:
        cur.execute(
            f"WITH want(ts) AS (VALUES {vals}) "
            "SELECT w.ts, a.avg_avail, a.n "
            "FROM want w "
            "JOIN hackathon_backend_calendar c ON c.d = w.ts::date "
            "JOIN hackathon_backend_station_slot_average a "
            "  ON a.station_uid = %s AND a.is_holiday = c.is_holiday "
            " AND a.tod = w.ts::time",
            (*ts_list, station_uid))
        return {r["ts"]: {"avg": float(r["avg_avail"]), "n": r["n"]}
                for r in cur.fetchall()}


def series_all(ts_list: list[datetime]) -> dict[tuple[str, datetime], dict]:
    """批次版：一次取**全站** × 多個時刻，回 {(station_uid, ts): {"avg", "n"}}。

    給 Job B 的風險判定用（全市 1,538 站 × 7 個時刻 ≈ 1 萬列，一次 round-trip）。
    ★ 不可改用逐站呼叫 series() —— 1,538 次 round-trip 會把批次跑成龜速。
    join 條件與單站版逐字相同（同樣必須逐格帶日期，預測窗會跨午夜）。
    查無的桶不會出現在結果裡，樣本數過濾交給呼叫端（config.SLOT_AVG_MIN_N）。
    """
    if not ts_list:
        return {}
    vals = ", ".join(["(%s::timestamp)"] * len(ts_list))
    with get_conn().cursor() as cur:
        cur.execute(
            f"WITH want(ts) AS (VALUES {vals}) "
            "SELECT a.station_uid, w.ts, a.avg_avail, a.n "
            "FROM want w "
            "JOIN hackathon_backend_calendar c ON c.d = w.ts::date "
            "JOIN hackathon_backend_station_slot_average a "
            "  ON a.is_holiday = c.is_holiday AND a.tod = w.ts::time",
            tuple(ts_list))
        return {(r["station_uid"], r["ts"]): {"avg": float(r["avg_avail"]), "n": r["n"]}
                for r in cur.fetchall()}
