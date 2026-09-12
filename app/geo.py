# ── 共用地理計算 ──────────────────────────────────────────
# 純函式，被 assistant/datapkg.py（調度來源候選）跟 alert_service.py（清單建議調出站）共用。
from __future__ import annotations

import math


def haversine_m(p1: tuple | None, p2: tuple | None) -> float | None:
    """兩點 (lat, lon) 的直線距離（公尺）；缺座標回 None。"""
    if not p1 or not p2 or p1[0] is None or p2[0] is None:
        return None
    lat1, lon1, lat2, lon2 = (float(x) for x in (*p1, *p2))
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def dist_word(m: float | None) -> str | None:
    if m is None:
        return None
    return f"約 {round(m)} 公尺" if m < 1000 else f"約 {m / 1000:.1f} 公里"
