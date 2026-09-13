from fastapi import APIRouter

from app import config
from app.repository import sys_config_repo as sc

router = APIRouter()


@router.get("/healthz")
def healthz():
    """★ 系統狀態一站看完：資料拉到哪、預測涵蓋到哪、排程還活著嗎。

    data_age / forecast_left 是給人看的判讀線索：
      data_age > 1 小時    → 排程大概停了（或機器睡了）
      forecast_left < 0    → 預測已經過期，前端不該再顯示
      virtual_now 非 null  → ⚠ 活在虛擬時間，數字不是「現在」

    ★ 2026-09-12：demo_tail_speed 換成 demo_speed（流速搬進 DB，來源只剩
      一個），新增 predicting_origin —— 前端靠它上全頁遮罩。
      出處：meet/20260912/計劃-demo回放邏輯重整.md §3-4。
    """
    now = sc.effective_now()
    cur, fe, tick = (sc.get_ts(sc.K_CURRENT_SLOT), sc.get_ts(sc.K_FORECAST_END),
                     sc.get_ts(sc.K_LAST_TICK))
    po, psince = sc.predicting()
    return {
        "ok": True, "mock": config.MOCK, "endpoint": config.ENDPOINT_NAME,
        "now": str(now),
        "virtual_now": str(now) if sc.is_virtual() else None,
        "scheduler_on": sc.scheduler_on(),
        # 回放流速。線上要確認「時鐘還會不會走、走多快」只能問這裡 ——
        # now 連看兩次才看得出來，那太慢。0 = 停表。
        "demo_speed": sc.demo_speed() if sc.is_demo() else None,
        # 正在現算的 origin。非 null = 前端上全頁遮罩「最新資料載入中」。
        # ★ 有 TTL 自癒（sys_config_repo.predicting）—— 迴圈被 kill 之後
        #   這裡會自己回 null，遮罩不會卡死。
        "predicting_origin": str(po) if po else None,
        "predicting_since": str(psince) if psince else None,
        "current_slot": str(cur) if cur else None,
        "forecast_end": str(fe) if fe else None,
        "data_age_min": round((now - cur).total_seconds() / 60) if cur else None,
        "forecast_left_min": round((fe - now).total_seconds() / 60) if fe else None,
        "last_tick": str(tick) if tick else None,
        "tick_age_min": round((now - tick).total_seconds() / 60) if tick else None,
    }
