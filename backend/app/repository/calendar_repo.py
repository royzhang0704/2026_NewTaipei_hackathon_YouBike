# hackathon_backend_calendar（民國 113~116 年 / 2024~2027）→ 54 格 is_holiday
from datetime import datetime, timedelta

from app import config
from app.repository.db import get_conn


def holiday_seq(start_ts: datetime, n: int) -> tuple[list[int | None], list]:
    """自 start_ts 起 n 格（30min/格），逐格取該日 is_holiday。
    回傳 (seq, missing_dates)；日曆缺該日期時 seq 該格為 None 並列入 missing。"""
    slots = [start_ts + timedelta(minutes=config.FREQ_MIN * i) for i in range(n)]
    dates = sorted({s.date() for s in slots})
    with get_conn().cursor() as cur:
        cur.execute("SELECT d, is_holiday FROM hackathon_backend_calendar "
                    "WHERE d = ANY(%s)", (dates,))
        m = {r["d"]: r["is_holiday"] for r in cur.fetchall()}
    seq = [m.get(s.date()) for s in slots]
    missing = [d for d in dates if d not in m]
    return seq, missing


if __name__ == "__main__":            # 驗證：計劃-後端服務.md §8
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="frm", required=True)
    p.add_argument("--n", type=int, default=config.CONTEXT + config.H)
    a = p.parse_args()
    seq, missing = holiday_seq(datetime.fromisoformat(a.frm), a.n)
    print(f"seq({len(seq)})  {seq}")
    print("✓ 無缺日" if not missing else f"✗ 日曆缺 {missing}（CALENDAR_OUT_OF_RANGE）")
