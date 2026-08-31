# hackathon_backend_forecast_history 的唯讀查詢（Job B 每 30 分批次寫入）。
# 單站檢視走這裡拿預測 —— 不打 SageMaker，即時推論只剩 /predict 一條路。
from datetime import datetime

from app.repository.db import get_conn


def latest_origin(station_uid: str) -> datetime | None:
    """該站最新一輪批次預測的 origin；從未被批到（新站/無歷史）回 None。"""
    with get_conn().cursor() as cur:
        cur.execute("SELECT max(origin) AS o FROM hackathon_backend_forecast_history "
                    "WHERE station_uid = %s", (station_uid,))
        return cur.fetchone()["o"]


def at_origin(station_uid: str, origin: datetime) -> list[dict]:
    """某一輪 origin 的 6 格預測，依時刻排序。"""
    with get_conn().cursor() as cur:
        cur.execute(
            "SELECT at, q19, q50, q90, model_job "
            "FROM hackathon_backend_forecast_history "
            "WHERE station_uid = %s AND origin = %s ORDER BY at",
            (station_uid, origin))
        return cur.fetchall()
