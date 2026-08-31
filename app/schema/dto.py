from datetime import datetime
from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    station_uid: str
    at: datetime | None = None          # 預測原點；空 = 該站最末格
    is_holiday: int | None = Field(default=None, ge=0, le=1)  # what-if：覆寫全部 54 格
