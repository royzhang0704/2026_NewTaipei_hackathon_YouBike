# hackathon_backend_forecast_history 的唯讀查詢（Job B 每 30 分批次寫入）。
# 單站檢視走這裡拿預測 —— 不打 SageMaker，即時推論只剩 /predict 一條路。
from datetime import datetime

from app.repository import sys_config_repo
from app.repository.db import get_conn


def latest_origin(station_uid: str) -> datetime | None:
    """該站最新一輪批次預測的 origin；從未被批到（新站/無歷史）回 None。

    ★ 上界是 effective_now()，不是全表 max(origin)（9/1 修）：
      demo 回放時 forecast_history 裡可能已經躺著整段未來的預測
      （上一輪 demo 跑完留下的）。不夾上界，前端一律錨在最末一輪，
      虛擬時鐘怎麼走畫面都不動 —— 回放等於沒效果。
      真排程不受影響：origin 本來就不可能超過「現在」。
    """
    with get_conn().cursor() as cur:
        cur.execute("SELECT max(origin) AS o FROM hackathon_backend_forecast_history "
                    "WHERE station_uid = %s AND origin <= %s",
                    (station_uid, sys_config_repo.effective_now()))
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
