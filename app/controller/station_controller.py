from fastapi import APIRouter, Query

from app.service import overview_service, station_service

router = APIRouter()


@router.get("/towns")
def towns():
    return station_service.towns()


@router.get("/stations")
def stations(town_code: str | None = Query(default=None, min_length=2, max_length=2)):
    """不帶 town_code = 全量（前端一次載入後自行過濾）。"""
    return station_service.stations(town_code)


@router.get("/stations/{uid}")
def station(uid: str):
    return station_service.station(uid)


@router.get("/stations/{uid}/day")
def station_day(uid: str):
    """24 小時視圖：21h 實況 + 3h 批次預測。只讀 DB，不打 SageMaker。"""
    return overview_service.day_view(uid)
