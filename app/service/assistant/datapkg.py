# ── 調度助理・即時資料包 ────────────────────────────────────
# 依範圍（某站 / 某區 / 全市 / 多區比較）撈 alert_service，塑形成一小包 JSON 給 LLM 當事實依據；
# 同時組「開啟站點 / 切區」按鈕、以及清單題的 {type:list} / 多區比較的 {type:table} panel。
# 數字全在這裡定死，LLM 只負責引用。
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from app.repository import station_repo
from app.service import alert_service

from . import common as C
from .scope import find_station, named_towns


# ── 型別 ─────────────────────────────────────────────────
class Panel(TypedDict, total=False):
    type: Literal["list", "table"]
    title: str
    items: list[dict]                 # list：[{name, meta, uid}]
    columns: list[dict]               # table：[{key, label, align}]
    rows: list[dict]                   # table：[{name, town, cells}]


ScopeKind = Literal["compare", "district", "city"]


@dataclass(frozen=True)
class Scope:
    """一句問句解析後的「要看哪裡 + 想要什麼」。gather_context 依此 dispatch；eval 可直接斷言。"""
    kind: ScopeKind
    named: list[dict] = field(default_factory=list)   # 命中的行政區（compare 用前 4 個）
    ask_full: bool = False                             # 問「滿站」
    ask_short: bool = False                            # 問「空站」
    ask_source: bool = False                           # 問「從哪調車過來」


def resolve_scope(q: str, ctx: dict) -> Scope:
    """純函式：問句 + ctx → Scope。不撈 alert_service，只用站點主檔判行政區。"""
    towns = station_repo.towns()
    named = named_towns(q, towns)
    city = (
        bool(C.CITY_ALLDISTRICTS_RE.search(q))                       # strong：一律全市
        or (bool(C.CITY_WHOLE_RE.search(q)) and not named)           # whole：沒點名區才全市
        or (bool(C.CITY_RE.search(q)) and not named and not ctx.get("town_code"))  # weak
    )
    if len(named) >= 2 and not city:
        kind: ScopeKind = "compare"
    elif (named[:1] or ctx.get("town_code")) and not city:
        kind = "district"
    else:
        kind = "city"
    return Scope(
        kind=kind, named=named,
        ask_full=bool(re.search(r"滿站", q)),
        ask_short=bool(re.search(r"空站", q)),
        ask_source=bool(C.ASK_SOURCE_RE.search(q)),
    )


# ── 小塑形工具 ────────────────────────────────────────────
def _counts(rows: list[dict]) -> dict:
    return {
        "空站": sum(1 for i in rows if i["side"] == "shortage"),
        "滿站": sum(1 for i in rows if i["side"] == "full"),
        "高風險": sum(1 for i in rows if i["level"] == "high"),
        "中風險": sum(1 for i in rows if i["level"] == "mid"),
    }


def _summ_block(summary: dict, items: list[dict]) -> dict:
    """摘要區塊：數量（現算，跟主動警示同一套）＋補取台數（伺服器算的 summary）。"""
    return {
        **_counts(items),
        "供需健康站數": summary.get("none"),  # 無風險的站＝一般所謂「安全 / 正常」（不會逐站列出）
        "站數合計": summary.get("stations"),
        "建議補車總量": summary["refill"]["bikes"],
        "建議取車總量": summary["remove"]["bikes"],
        "無預測站數": summary["no_forecast"],
    }


def _urgent_list(items: list[dict], n: int = 8) -> list[dict]:
    """items 已由 alert_service 排好序（高>中>低、streak、台數、onset）；照原序取前 n。"""
    return [
        {"name": i["name"], "方向": C.SIDE_WORD.get(i["side"], i["side"]),
         "等級": C.LV_WORD.get(i["level"], i["level"]),
         "建議": C.act_phrase(i.get("dispatch")),
         "可借": (i.get("now") or {}).get("avail"),
         # 兩個時數語意不同：前者是「高風險連續幾小時」（只有高風險站有值），
         # 後者是「可借數卡在同一個數字幾小時」（疑似斷線／車輛沒在流動）
         "已持續小時": (i.get("streak") or {}).get("hours"),
         "水位停滯小時": (i.get("stale") or {}).get("hours")}
        for i in items[:n]
    ]


def _side_names(rows: list[dict], side: str, n: int = 12) -> list[str]:
    """該方向（shortage / full）的站名清單；rows 已依緊急度排序，取前 n。"""
    return [i["name"] for i in rows if i["side"] == side][:n]


def _donor_item(i: dict) -> dict:
    # 「可調出上限」＝這站自己的建議取車量（取到這個數它仍健康）。刻意不用「建議取 N 台」
    # 這種指令式字眼，避免被讀成「從這裡取 N 台」。
    bikes = (i.get("dispatch") or {}).get("bikes")
    return {"name": i["name"], "可借": (i.get("now") or {}).get("avail"),
            "容量": i.get("capacity"),
            "可調出上限": (bikes if isinstance(bikes, int) else None)}


def _haversine(p1, p2) -> float | None:
    """兩點 (lat, lon) 的直線距離（公尺）；缺座標回 None。"""
    if not p1 or not p2 or p1[0] is None or p2[0] is None:
        return None
    lat1, lon1, lat2, lon2 = (float(x) for x in (*p1, *p2))
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def _dist_word(m: float | None) -> str | None:
    if m is None:
        return None
    return f"約 {round(m)} 公尺" if m < 1000 else f"約 {m / 1000:.1f} 公里"


# ── 清單題 / 多區比較的 render payload ─────────────────────
def _row_item(i: dict, with_level: bool = False) -> dict:
    d = i.get("dispatch") or {}
    a, b = d.get("action"), d.get("bikes")
    act = (f"補 {b} 台" if a == "refill" and b is not None else
           f"取 {b} 台" if a == "remove" and b is not None else "暫可觀察")
    lv = C.LV_WORD.get(i["level"], "")
    meta = f"{lv}・{act}" if with_level and lv and i["level"] != "none" else act
    return {"name": i["name"], "meta": meta, "uid": i["station_uid"]}


def _build_list(q: str, rows: list[dict], scope_name: str) -> dict | None:
    """清單題才回 SSE list 事件；狀態題 / 知識題 / 問數量 → None（讓 LLM 一句話回答）。"""
    if not rows or C.LIST_SKIP_RE.search(q):
        return None
    if re.search(r"滿站", q):
        sel, title = [i for i in rows if i["side"] == "full"], f"{scope_name}・滿站"
    elif re.search(r"空站", q):
        sel, title = [i for i in rows if i["side"] == "shortage"], f"{scope_name}・空站"
    elif C.LIST_REFILL_RE.search(q):
        sel = [i for i in rows if (i.get("dispatch") or {}).get("action") == "refill"]
        title = f"{scope_name}・待補車"
    elif C.LIST_REMOVE_RE.search(q):
        sel = [i for i in rows if (i.get("dispatch") or {}).get("action") == "remove"]
        title = f"{scope_name}・待取車"
    elif C.LIST_PENDING_RE.search(q):
        sel, title = list(rows), f"{scope_name}・待處理站"
    else:
        return None
    if not sel:
        return None
    with_lv = title.endswith("待處理站")
    return {"type": "list", "title": title,
            "items": [_row_item(i, with_lv) for i in sel[:20]]}


def _compare_table(cmp: list[dict], towns: list[dict]) -> dict | None:
    """多區比較 → SSE table 事件（區名 + 數值欄 + 最急站）；數字全來自 proxy，不經 LLM。"""
    if len(cmp) < 2:
        return None
    cols = [
        {"key": "empty", "label": "空站", "align": "right"},
        {"key": "full", "label": "滿站", "align": "right"},
        {"key": "high", "label": "高風險", "align": "right"},
        {"key": "refill", "label": "待補", "align": "right"},
        {"key": "top", "label": "最急站", "align": "left"},
    ]
    rows = [
        {"name": c["name"], "town": t["town_code"],
         "cells": [str(c["空站"]), str(c["滿站"]), str(c["高風險"]),
                   str(c["建議補車總量"]), c.get("最優先處理") or "—"]}
        for c, t in zip(cmp, towns)
    ]
    return {"type": "table", "title": "行政區比較", "columns": cols, "rows": rows}


# ── 各範圍的資料包分支 ────────────────────────────────────
def _pkg_compare(named: list[dict], city_items: list[dict]) -> tuple[dict, list[dict], Panel | None]:
    """多區比較：逐區各撈一個精簡摘要（不含待處理站清單，控制 token）。"""
    cmp: list[dict] = []
    buttons: list[dict] = []
    for t in named[:4]:
        d = alert_service.alerts(town_code=t["town_code"], limit=1000)
        rows = d["items"]
        cmp.append({
            "name": t["town"],
            **_counts(rows),
            "供需健康站數": d["summary"].get("none"),
            "建議補車總量": d["summary"]["refill"]["bikes"],
            "建議取車總量": d["summary"]["remove"]["bikes"],
            "最優先處理": (rows[0]["name"] if rows else None),
        })
        if rows:
            buttons.append(C.open_action(
                {"name": rows[0]["name"], "station_uid": rows[0]["station_uid"]}))
    return {"行政區比較": cmp, "全市對照": _counts(city_items)}, buttons, _compare_table(cmp, named[:4])


def _pkg_district(sc: "Scope", ctx: dict, towns: list[dict],
                  city_items: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """帶 town_code 的 scoped 查詢：完整、有該區自己的補取台數。回傳 (data, buttons, 該區 rows)。"""
    named = sc.named[0] if sc.named else None
    tc = named["town_code"] if named else ctx.get("town_code")
    d = alert_service.alerts(town_code=tc, limit=1000)
    drows = d["items"]
    t = next((x for x in towns if x["town_code"] == tc), None)
    blk: dict = {
        "name": (t["town"] if t else tc),
        **_summ_block(d["summary"], drows),
        "最優先處理": (drows[0]["name"] if drows else None),  # 已排序，第一個就是最急
        "待處理站(依優先序)": _urgent_list(drows),
    }
    if sc.ask_full:
        blk["滿站站點"] = _side_names(drows, "full")
    if sc.ask_short:
        blk["空站站點"] = _side_names(drows, "shortage")
    # 開站按鈕跟著問句焦點：問滿站→給滿站；問空站→給空站；否則給最急的前幾個（限問了特定區）
    focus = ([i for i in drows if i["side"] == "full"] if sc.ask_full
             else [i for i in drows if i["side"] == "shortage"] if sc.ask_short
             else (drows if named else []))
    buttons = [C.open_action(i) for i in focus[:3]]
    if named and named["town_code"] != ctx.get("town_code"):  # 問的區 ≠ 畫面正在看的區
        buttons.append({"label": f"切到 {blk['name']} 地圖", "type": "filter_town", "value": tc})
    # 全市只放數量當對照，不放台數（避免被誤植進行政區句子）
    return {"行政區": blk, "全市對照": _counts(city_items)}, buttons, drows


def _pkg_city(sc: "Scope", city_alerts: dict) -> tuple[dict, list[dict]]:
    items = city_alerts["items"]
    blk: dict = {**_summ_block(city_alerts["summary"], items),
                 "最優先處理": (items[0]["name"] if items else None),
                 "待處理站(依優先序)": _urgent_list(items)}
    if sc.ask_full:
        blk["滿站站點"] = _side_names(items, "full", 15)
    if sc.ask_short:
        blk["空站站點"] = _side_names(items, "shortage", 15)
    return {"全市": blk}, items


def _attach_station(q: str, ctx: dict, data: dict, items: list[dict],
                    named_in_q: bool) -> tuple[dict | None, list[dict]]:
    """把「問句指到的那一站」現況塞進 data["站點"]；回傳 (st, 要 prepend 的按鈕)。
    問句沒點到站名、但單站檢視正開著某站、又沒點名別的區 → 當作在問那一站。"""
    st = find_station(q, station_repo.all_stations())
    if st is None and ctx.get("station_uid") and (C.THIS_STATION_RE.search(q) or not named_in_q):
        st = station_repo.find(ctx["station_uid"])
    if not st:
        return None, []
    a = next((i for i in items if i["station_uid"] == st["station_uid"]), None)
    if a is None:  # 該站不在目前範圍的清單裡，另查一次
        a = next((i for i in alert_service.alerts(town_code=st["town_code"], limit=1000)["items"]
                  if i["station_uid"] == st["station_uid"]), None)
    data["站點"] = {
        "name": st["station_name"], "行政區": st["town"],
        "狀態": (f"{C.SIDE_WORD.get(a['side'])}・{C.LV_WORD.get(a['level'])}"
                if a and a["level"] != "none" else "供需健康"),
        "可借": (a.get("now") or {}).get("avail") if a else None,
        "容量": (a.get("capacity") if a else st.get("capacity")),
        "建議": C.act_phrase(a.get("dispatch")) if a else "—",
        "預計越線": C.hhmm(a.get("onset")) if a else None,
        "常態水位": (a.get("baseline") if a else None),
    }
    return st, [{"label": f"開啟 {st['station_name']}",
                "type": "select_station", "value": st["station_uid"]}]


def _donor_action(i: dict) -> dict:
    return {"label": f"調出點：{i['name']}", "type": "select_station", "value": i["station_uid"]}


def _attach_donors(data: dict, st: dict | None, city_items: list[dict]) -> list[dict]:
    """「從哪調車過來」→ 可供調出的站：風險模型說「該取車」的滿站（dispatch=remove），
    所以每站的『可調出上限』就是「可安全調出、調完仍健康」的量，助理不用自己判斷來源會不會變風險。
    有目標站 → 依直線距離排序、只留 DONOR_MAX_M 內的；都太遠 → 改建議用調度中心備車。
    沒目標站（區/全市層級）→ 給該範圍的、不排序（沒有基準點算不了距離）。
    回傳「調出點」按鈕（前 3 個近站），前端可點去地圖定位。"""
    far = "附近沒有可調出的餘裕站，建議由調度中心的備用車 / 調度站預備車補入"
    reserve = "本區沒有可調出的滿站，建議由調度中心的備用車補入"
    donors = [i for i in city_items
              if i["side"] == "full" and (i.get("dispatch") or {}).get("action") == "remove"]
    if st and donors:
        coord = {s["station_uid"]: (s["lat"], s["lon"]) for s in station_repo.all_stations()}
        here = coord.get(st["station_uid"])
        ranked = [(i, _haversine(here, coord.get(i["station_uid"]))) for i in donors]
        near = sorted((x for x in ranked if x[1] is not None and x[1] <= C.DONOR_MAX_M),
                      key=lambda x: x[1])[:5]
        if near:
            # 從最近的開始累加「可調出上限」，湊到本站『建議補 N 台』就停 —— 資料包 / chip / prose 同一組
            m = re.search(r"補\s*(\d+)", data.get("站點", {}).get("建議", "") or "")
            need = int(m.group(1)) if m else None
            if need:
                pick, acc = [], 0
                for i, dist in near:
                    pick.append((i, dist))
                    acc += (i.get("dispatch") or {}).get("bikes") or 0
                    if acc >= need:
                        break
                near = pick
                if acc < need:  # 附近餘裕站全加起來還不夠 N
                    data.setdefault("站點", {})["可調出備註"] = (
                        f"附近餘裕站合計約 {acc} 台，尚缺 {need - acc} 台建議由調度中心的備用車補入")
            data.setdefault("站點", {})["可調出候選"] = [
                {**_donor_item(i), "直線距離": _dist_word(dist)} for i, dist in near
            ]
            return [_donor_action(i) for i, _ in near]
        # 最近的滿站也超過上限 —— 站對站不划算
        data.setdefault("站點", {})["可調出備註"] = far
        return []
    if st:
        data.setdefault("站點", {})["可調出備註"] = reserve
        return []
    # 沒目標站（區 / 全市層級）：只放清單，不排序、不做按鈕（沒有基準點）
    if data.get("行政區") is not None:
        same = [i for i in donors if i["town"] == data["行政區"]["name"]]
        if same:
            data["行政區"]["可調出候選"] = [_donor_item(i) for i in same][:8]
        elif donors:
            data["行政區"]["可調出候選"] = [_donor_item(i) for i in donors][:5]
            data["行政區"]["可調出備註"] = "本區沒有可調出的滿站，以下為全市；或由調度中心備用車補入"
        else:
            data["行政區"]["可調出備註"] = reserve
    elif "全市" in data:
        data["全市"]["可調出候選"] = [_donor_item(i) for i in donors][:10] or None
        if not donors:
            data["全市"]["可調出備註"] = reserve
    return []


# ── 主入口：組資料包 ──────────────────────────────────────
def gather_context(q: str, ctx: dict) -> tuple[dict, list[dict], Panel | None]:
    """依畫面脈絡（在看哪區 / 哪站）＋問句，撈一小包即時資料給 Harness 當事實依據。
    回傳 (給 LLM 的精簡 dict, 給前端的按鈕清單, list/table panel or None)。"""
    sc = resolve_scope(q, ctx)
    towns = station_repo.towns()
    city_alerts = alert_service.alerts(limit=1000)   # 全市一次撈定，各分支共用
    city_items = city_alerts["items"]

    if sc.kind == "compare":
        data, buttons, panel = _pkg_compare(sc.named, city_items)
        items: list[dict] = []
    elif sc.kind == "district":
        data, buttons, items = _pkg_district(sc, ctx, towns, city_items)
        panel = None
    else:
        data, items = _pkg_city(sc, city_alerts)
        buttons, panel = [], None

    st, st_buttons = _attach_station(q, ctx, data, items, named_in_q=bool(sc.named))
    buttons = st_buttons + buttons  # 站點按鈕排最前（等同原本的 insert(0,…)）

    # 清單題 panel：問句指到某一站（單站問題）不出清單；比較題已有表格 panel，不覆蓋。
    if panel is None and not st:
        scope_name = data["行政區"]["name"] if "行政區" in data else "全市"
        panel = _build_list(q, items, scope_name)

    donor_cap = 3
    if sc.ask_source:
        donor_buttons = _attach_donors(data, st, city_items)
        buttons += donor_buttons  # 「調出點：X」排在目標站按鈕後面
        if donor_buttons:
            donor_cap = 1 + len(donor_buttons)  # 目標站 + 每個調出點都要留

    seen: set[str] = set()
    nav = [b for b in buttons if b["type"] == "filter_town"][:1]
    # 有結構化清單時，逐站「開啟」按鈕多餘（清單列本身可點）→ 只留切區按鈕
    stn = [] if panel else [
        b for b in buttons if b["type"] == "select_station"
        and not (b["value"] in seen or seen.add(b["value"]))
    ][:donor_cap]
    return data, nav + stn, panel
