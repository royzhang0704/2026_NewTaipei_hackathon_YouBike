from datetime import datetime
from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    station_uid: str
    # 預測原點；空 = 虛擬的現在（sys_config_repo.effective_now）。
    # ★ 不是「該站最末格」—— 那是 9/4 那顆錨到未來的 bug，9/7 已修
    #   （history_repo.tail 的 at 預設值）。
    at: datetime | None = None
    is_holiday: int | None = Field(default=None, ge=0, le=1)  # what-if：覆寫全部 54 格
