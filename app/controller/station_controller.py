from fastapi import APIRouter, Query

from app.service import alert_service, overview_service, station_service

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


@router.get("/alerts")
def alerts(town_code: str | None = Query(default=None, min_length=2, max_length=2),
           level: str | None = Query(default=None,
                                     description="逗號分隔 high,mid,low（預設三級都給）"),
           side: str | None = Query(default=None, pattern="^(shortage|full)$"),
           action: str | None = Query(default=None, pattern="^(refill|remove|hold)$"),
           limit: int = Query(default=100, ge=1, le=1000),
           offset: int = Query(default=0, ge=0)):
    """全市／同區風險告警清單，高>中>低排序（同級再比持續輪數）。只讀 DB。"""
    return alert_service.alerts(town_code, level, side, action, limit, offset)


@router.get("/stations/{uid}/risk")
def station_risk(uid: str, n: int = Query(default=48, ge=1, le=336)):
    """單站風險歷程（新→舊）——「這站連續亮了幾輪」的下鑽。"""
    return alert_service.station_risk(uid, n)
