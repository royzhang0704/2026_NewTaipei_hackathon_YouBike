from app.repository.db import get_conn

_COLS = """station_uid, station_name, town_code, town, lat, lon,
           capacity, addr_zh, last_seen, cat, proxy_station_uid, proxy_distance_m"""


def find(station_uid: str) -> dict | None:
    with get_conn().cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM hackathon_backend_station WHERE station_uid = %s",
                    (station_uid,))
        return cur.fetchone()


def towns() -> list[dict]:
    with get_conn().cursor() as cur:
        cur.execute("SELECT town_code, town, station_count "
                    "FROM hackathon_backend_town ORDER BY town_code")
        return cur.fetchall()


def all_stations() -> list[dict]:
    """全部站（1.5k 列）—— 前端一次載入、之後純前端過濾用。"""
    with get_conn().cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM hackathon_backend_station "
                    "ORDER BY station_uid")
        return cur.fetchall()


def by_town(town_code: str) -> list[dict]:
    with get_conn().cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM hackathon_backend_station "
                    "WHERE town_code = %s ORDER BY station_uid", (town_code,))
        return cur.fetchall()
