from datetime import datetime
from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    station_uid: str
    # 預測原點；空 = 虛擬的現在（sys_config_repo.effective_now）。
    # ★ 不是「該站最末格」—— 那是 9/4 那顆錨到未來的 bug，9/7 已修
    #   （history_repo.tail 的 at 預設值）。
    at: datetime | None = None
    is_holiday: int | None = Field(default=None, ge=0, le=1)  # what-if：覆寫全部 54 格


class DispatchItem(BaseModel):
    uid: str                      # 候選站（車的來源或目的，方向由 action 推導）
    bikes: int = Field(ge=1)      # ★ 真正的下界是 config.DISPATCH_MIN_BIKES，
                                  #   但擋在 service 才能回統一的 DISPATCH_STALE 訊息；
                                  #   這裡只擋負數與 0（那是明顯的壞請求）。


class DispatchCreate(BaseModel):
    anchor_uid: str                          # 使用者當初點的那個風險站
    action: str = Field(pattern="^(refill|remove)$")   # 站在 anchor 的立場
    items: list[DispatchItem] = Field(min_length=1)
    # ★ 沒有 operator 欄位 —— 一律由後端寫死 IM_TEST，不接受前端傳值（sql/70 欄註解）。
