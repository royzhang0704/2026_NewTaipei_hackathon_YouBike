# baseline_grid 的唯讀查詢 —— demo「對答案」用：
# 回放世界裡的「未來」在這張表裡是已知的歷史真值。
from datetime import datetime

from app.repository.db import get_conn


def avail_between(station_uid: str, t0: datetime, t1: datetime) -> list[dict]:
    """[t0, t1] 區間的實際可借台數（含 avail NULL 的缺觀測格）。"""
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT slot, avail FROM baseline_grid "
            "WHERE station_uid = %s AND slot >= %s AND slot <= %s ORDER BY slot",
            (station_uid, t0, t1))
        return cur.fetchall()
