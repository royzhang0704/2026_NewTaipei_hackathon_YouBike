from app.errors import AppError
from app.repository import station_repo


def _pub(st: dict) -> dict:
    return {"uid": st["station_uid"], "name": st["station_name"],
            "town_code": st["town_code"], "town": st["town"],
            "lat": float(st["lat"]) if st["lat"] is not None else None,
            "lon": float(st["lon"]) if st["lon"] is not None else None,
            "capacity": st["capacity"], "addr": st["addr_zh"],
            # 呼叫端打 /predict 之前就能知道會不會拿到 STATION_UNKNOWN
            "model_known": st["cat"] is not None,
            "proxy_available": st["proxy_station_uid"] is not None}


def towns() -> list[dict]:
    return station_repo.towns()


def stations(town_code: str | None = None) -> list[dict]:
    """town_code 給了就限該區；不給回全量（前端靜態查詢用）。"""
    rows = (station_repo.by_town(town_code) if town_code
            else station_repo.all_stations())
    return [_pub(s) for s in rows]


def station(uid: str) -> dict:
    st = station_repo.find(uid)
    if st is None:
        raise AppError("STATION_NOT_FOUND", 404,
                       f"hackathon_backend_station 沒有 {uid}")
    out = _pub(st)
    if st["proxy_station_uid"] is not None:
        out["proxy"] = {"cat_from": st["proxy_station_uid"],
                        "distance_m": st["proxy_distance_m"]}
    return out
