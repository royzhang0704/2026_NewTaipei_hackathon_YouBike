# ════════════════════════════════════════════════════════════
# assistant_service —— 調度助理的後端大腦（9/9）
#
# 所有問題走同一條路：
#   proxy 依畫面脈絡（在看哪區 / 哪站 / 虛擬時鐘）先撈好即時調度資料 → 連同問句
#   丟給 AgentCore Harness，由它綜合現況、給行動建議、用知識庫工具拉 SOP 附出處。
#   數字精確靠「proxy 撈好塞進 prompt + 指示照原文引用」；撈到的站點另組成按鈕。
#
# 未設 ASSISTANT_HARNESS_ARN（正式環境不該發生）：
#   領域內問題 → error「暫時無法回應」；純離題 → 講服務範圍 + 相關問題按鈕。
#
# 輸出一律是 SSE 事件 dict，由 controller 包成 `data: {...}\n\n`：
#   {"type":"delta","text":str}   逐段文字
#   {"type":"sources","items":[{title,snippet?,uri?}]}
#   {"type":"actions","items":[{label,type,value}]}   type: select_station | filter_town
#   {"type":"list","title":str,"items":[{name,meta,uid}]}          清單題（哪些站要補/取車…）
#   {"type":"table","title":str,"columns":[…],"rows":[…]}          多區比較
#   {"type":"suggestions","items":[str]}
#   {"type":"done"}
#   {"type":"error","message":str}
# ════════════════════════════════════════════════════════════
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Iterator

from app.errors import AppError
from app.repository import station_repo
from app.service import alert_service

_log = logging.getLogger("assistant")

# ── 文字對照 ──────────────────────────────────────────────
_LV_WORD = {"high": "高風險", "mid": "中風險", "low": "低風險", "none": "無風險"}
_SIDE_WORD = {"shortage": "空站", "full": "滿站"}
_ACT_WORD = {"refill": "建議補", "remove": "建議取"}

# ── 少數比對用正則 ────────────────────────────────────────
_FRAG_RE = re.compile(r"[一-鿿A-Za-z0-9]{2,}")  # 抓問句中的中文／英數片段比對站名
# 問「全市 / 全部 / 其他行政區 / 各區」時 → 不縮到單一行政區（即使畫面正篩著某區）。
_CITY_RE = re.compile(
    r"(全市|全區|全部|各區|各行政區|所有|整體|新北市|全新北|整個新北|"
    r"其[他它]\S{0,3}區|其餘|別的\S{0,3}區|每\S{0,3}區)"
)
# 三層強度：
#   strong（各區 / 其他行政區 / 每區 / 全市）—— 一律全市，即使問句也點名了某區
#   whole（新北市 / 全新北 / 整個新北）—— 全市，除非問句同時點名了某區（「新北市板橋區」→ 板橋）
#   weak（整體 / 全部 / 所有，_CITY_RE 的其餘）—— 只有「沒點名區、也沒在篩某區」時才當全市
_CITY_ALLDISTRICTS_RE = re.compile(
    r"(全市|全區|各區|各行政區|"
    r"其[他它]\S{0,3}區|其餘|別的\S{0,3}區|每\S{0,3}區)"
)
_CITY_WHOLE_RE = re.compile(r"(新北市|全新北|整個新北)")
# 「跳出目前這區、看其餘 / 全部」的措辭 —— 用來把 q 正規化成乾淨問法
_BROADEN_RE = re.compile(r"(其[他它]|全部|各[區行]|每.{0,3}區|其餘|別的)")
_THIS_STATION_RE = re.compile(r"(這站|該站|此站|這個站|這一站|這裡|這邊)")  # 指「目前開著的那站」
# 判斷「是不是 YouBike 調度相關」——非相關的閒聊 / 其他領域，直接講服務範圍
_DOMAIN_RE = re.compile(
    r"(空|滿|缺|補|取|站|車|台|調度|水位|風險|門檻|派|急|優先|概況|行政區|區$|"
    r"youbike|ubike|單車|自行車|腳踏車|板橋|三重|新莊|中和|永和|新店|土城|蘆洲|汐止|淡水|樹林|林口)"
)

# 助理未就緒 / 呼叫失敗時的使用者訊息（不露內部細節）
_UNAVAILABLE_MSG = "調度助理暫時無法回應，請稍後再試。即時站況可於主控台查看。"
_OFF_SCOPE_MSG = "調度助理僅回答 YouBike 調度相關問題 —— 即時站況、風險分級與調度規則。"
_NOISE_MSG = "看不太懂你的問題，可以再說清楚一點嗎？"

_CJK_RE = re.compile(r"[一-鿿]")

# 純打招呼 / 社交話 → 簡短自我介紹，不打 Harness、不撈資料
_GREETING_RE = re.compile(
    r"^(嗨+|哈囉+|halo|hello|hi+|hey|你好|妳好|您好|哈嘍|早安|午安|晚安|在嗎|在不在|有人嗎|"
    r"謝謝|感謝|thx|thanks|thank\s?you|好的|收到|了解|辛苦了|掰掰|拜拜|再見|bye)[\s!！?？。.~]*$"
)
_GREETING_MSG = (
    "我是調度助理，可以問即時站況、風險分級與調度規則 —— "
    "例如「現在有幾個高風險站」「板橋該怎麼調度」「空站門檻怎麼定的」。"
)


def _is_noise(q: str) -> bool:
    """明顯亂打 / 無意義輸入（123、?!?、asdf…）→ 不必打 Harness。"""
    s = q.strip()
    if len(s) < 2:
        return True
    core = re.sub(r"[\s\d\W_]+", "", s)  # 去空白 / 數字 / 標點後剩的字
    if len(core) < 2:
        return True
    # 非中文、又不含任何領域字 → 短句、或整串沒空白的單一 token（asdfgh、qwerty…）當亂打
    if not _CJK_RE.search(s) and not _DOMAIN_RE.search(s.lower()):
        if len(s) < 12 or " " not in s:
            return True
    return False


# ── 小工具 ────────────────────────────────────────────────
def _last_user(messages: list[dict]) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"].strip()
    return ""


def _find_station(q: str, stations: list[dict]) -> dict | None:
    """比對站名：問句片段是站名的一部分（打字打一半），或站名整個出現在問句裡。
    多個命中取「站名最長」的那個。

    片段比對只吃 >= 3 字的片段：2 字的「公園 / 路口 / 一街」會命中一堆站，
    誤判率過高（對話史踩過），改由「站名整段出現在問句裡」涵蓋刻意提整名的情況。"""
    frags = sorted({f for f in _FRAG_RE.findall(q) if len(f) >= 3}, key=len, reverse=True)
    best: tuple[str, dict] | None = None
    for s in stations:
        n = s.get("station_name")
        if not isinstance(n, str) or len(n) < 2:
            continue
        if any(f in n for f in frags) or (len(n) >= 4 and n in q):
            if best is None or len(n) > len(best[0]):
                best = (n, s)
    return best[1] if best else None


def _named_towns(q: str, towns: list[dict]) -> list[dict]:
    """比對行政區（可多個）：DB 名稱帶「區」（板橋區），使用者常只打「板橋」。
    依 towns 順序回傳、去重；用於「比較板橋和三重」這類多區問題。"""
    out: list[dict] = []
    seen: set[str] = set()
    for t in towns:
        name = t["town"]
        if (name in q or (name.endswith("區") and name[:-1] in q)) and t["town_code"] not in seen:
            seen.add(t["town_code"])
            out.append(t)
    return out


def _hhmm(onset: str | None) -> str | None:
    return onset[11:16] if onset and len(onset) >= 16 else None


def _act_phrase(dispatch: dict | None) -> str:
    if not dispatch:
        return "暫可觀察"
    act, bikes = dispatch.get("action"), dispatch.get("bikes")
    if act in _ACT_WORD and bikes is not None:
        return f"{_ACT_WORD[act]} {bikes} 台"
    return "暫可觀察"


def _open_action(item: dict) -> dict:
    return {"label": f"開啟 {item['name']}",
            "type": "select_station", "value": item["station_uid"]}


def _q_has_scope(q: str, towns: list[dict]) -> bool:
    """這句問句自己有沒有帶範圍訊號（全市 / 某區 / 這站）。"""
    return bool(_CITY_RE.search(q) or _BROADEN_RE.search(q)
                or _THIS_STATION_RE.search(q) or _named_towns(q, towns))


def _sticky_scope(messages: list[dict], current_q: str, towns: list[dict]) -> tuple[bool, str | None]:
    """對話範圍延續：往回找最近一次「有明確指定範圍」的使用者訊息。
    回傳 (是否全市, town_code)。只有當前問句自己沒帶範圍時才會用到 —— 讓
    「先問全市概況，再問哪些站要補車」的追問延續全市，而不是掉回畫面篩選的那一區。"""
    users = [m["content"] for m in (messages or [])
             if m.get("role") == "user" and isinstance(m.get("content"), str)]
    if users and users[-1].strip() == current_q.strip():
        users = users[:-1]  # 去掉當前這句
    # 只看緊接在前的 1～3 句 —— 「先問全市、再問哪些站要補」要延續；
    # 但「10 句前問過全市、中間聊別的、現在說你好」不該還黏在全市。
    for msg in reversed(users[-3:]):
        nl = _named_towns(msg, towns)
        if _CITY_ALLDISTRICTS_RE.search(msg) or _BROADEN_RE.search(msg) or (_CITY_RE.search(msg) and not nl):
            return True, None
        if nl:
            return False, nl[0]["town_code"]
    return False, None


def _effective_ctx(messages: list[dict], ctx: dict) -> dict:
    """把「範圍延續」套進 ctx：當前問句沒帶範圍時，改用對話裡最近一次指定的範圍，
    而不是一律掉回畫面篩選的那一區。chat_events 與 eval 共用，確保資料包一致。"""
    ctx = ctx or {}
    q = _last_user(messages)
    if not q:
        return ctx
    try:
        towns = station_repo.towns()
    except AppError:
        return ctx
    if _q_has_scope(q, towns):
        return ctx
    city, town = _sticky_scope(messages, q, towns)
    if city:
        return {**ctx, "town_code": None}
    if town:
        return {**ctx, "town_code": town}
    return ctx


def _session_id(messages: list[dict], ctx: dict) -> str:
    """AgentCore runtimeSessionId 需 >= 33 字元，且同一輪對話要穩定（多輪記憶）。
    優先用前端每個對話 mint 的 thread_id（換對話就換、內容重複也不會撞）；
    舊前端沒帶時，退回「第一則使用者訊息雜湊」。"""
    tid = (ctx or {}).get("thread_id")
    if isinstance(tid, str) and len(tid) >= 8:
        return f"yb-dispatch-assistant-{tid}"[:48]
    seed = ""
    for m in messages or []:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            seed = m["content"]
            break
    h = hashlib.sha1((seed or "anon").encode("utf-8")).hexdigest()
    return f"yb-dispatch-assistant-{h}"[:48]  # 22 + 40，取 48，恆 >= 33


def _scope_suggestions(ctx: dict) -> list[str]:
    """依畫面目前選取的站／區給相關問題（離題時當可點按鈕）。"""
    sug: list[str] = []
    uid = ctx.get("station_uid")
    if uid:
        st = station_repo.find(uid)
        if st:
            sug.append(f"「{st['station_name']}」現在狀況？")
    tc = ctx.get("town_code")
    if tc:
        t = next((x for x in station_repo.towns() if x["town_code"] == tc), None)
        if t:
            sug.append(f"{t['town']}該怎麼調度？")
    for g in ("現在全市概況？", "哪些站要優先補車？", "空站門檻怎麼定的？"):
        if len(sug) >= 3:
            break
        if g not in sug:
            sug.append(g)
    return sug[:3]


# ── 撈即時資料當事實依據 ──────────────────────────────────
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
        {"name": i["name"], "方向": _SIDE_WORD.get(i["side"], i["side"]),
         "等級": _LV_WORD.get(i["level"], i["level"]),
         "建議": _act_phrase(i.get("dispatch")),
         "可借": (i.get("now") or {}).get("avail"),
         "已持續小時": (i.get("streak") or {}).get("hours")}
        for i in items[:n]
    ]


def _side_names(rows: list[dict], side: str, n: int = 12) -> list[str]:
    """該方向（shortage / full）的站名清單；rows 已依緊急度排序，取前 n。"""
    return [i["name"] for i in rows if i["side"] == side][:n]


# ── 清單題 → 給前端渲染的結構化清單（站名 + 台數/等級 + uid） ─────
_LIST_REFILL_RE = re.compile(r"(補車|要補|補多少|缺車|補幾台)")
_LIST_REMOVE_RE = re.compile(r"(取車|要取|清車|移車|取多少|取幾台)")
_LIST_PENDING_RE = re.compile(r"(待處理|要處理|處理清單|處理順序|哪些站|急的站|亮燈|越線的站)")
# 這些是「問定義 / 問狀態 / 問健康站」——不是要清單
_LIST_SKIP_RE = re.compile(r"(為什麼|怎麼算|定義|門檻|規範|SOP|如何|意思|是什麼|安全|健康|正常|沒問題|無風險|幾站|幾個|多少站)")


def _row_item(i: dict, with_level: bool = False) -> dict:
    d = i.get("dispatch") or {}
    a, b = d.get("action"), d.get("bikes")
    act = (f"補 {b} 台" if a == "refill" and b is not None else
           f"取 {b} 台" if a == "remove" and b is not None else "暫可觀察")
    lv = _LV_WORD.get(i["level"], "")
    meta = f"{lv}・{act}" if with_level and lv and i["level"] != "none" else act
    return {"name": i["name"], "meta": meta, "uid": i["station_uid"]}


def _build_list(q: str, rows: list[dict], scope_name: str) -> dict | None:
    """清單題才回 SSE list 事件；狀態題 / 知識題 / 問數量 → None（讓 LLM 一句話回答）。"""
    if not rows or _LIST_SKIP_RE.search(q):
        return None
    if re.search(r"滿站", q):
        sel, title = [i for i in rows if i["side"] == "full"], f"{scope_name}・滿站"
    elif re.search(r"空站", q):
        sel, title = [i for i in rows if i["side"] == "shortage"], f"{scope_name}・空站"
    elif _LIST_REFILL_RE.search(q):
        sel = [i for i in rows if (i.get("dispatch") or {}).get("action") == "refill"]
        title = f"{scope_name}・待補車"
    elif _LIST_REMOVE_RE.search(q):
        sel = [i for i in rows if (i.get("dispatch") or {}).get("action") == "remove"]
        title = f"{scope_name}・待取車"
    elif _LIST_PENDING_RE.search(q):
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


def _gather_context(q: str, ctx: dict) -> tuple[dict, list[dict], dict | None]:
    """依畫面脈絡（在看哪區 / 哪站）＋問句，撈一小包即時資料給 Harness 當事實依據。
    回傳 (給 LLM 的精簡 dict, 給前端的按鈕清單, 清單題的結構化清單 or None)。"""
    data: dict = {}
    buttons: list[dict] = []
    panel: dict | None = None  # SSE list / table 事件（清單題 or 多區比較）
    ask_full = bool(re.search(r"滿站", q))
    ask_short = bool(re.search(r"空站", q))

    towns = station_repo.towns()
    named_list = _named_towns(q, towns)
    city = (
        bool(_CITY_ALLDISTRICTS_RE.search(q))                       # strong：一律全市
        or (bool(_CITY_WHOLE_RE.search(q)) and not named_list)      # whole：沒點名區才全市
        or (bool(_CITY_RE.search(q)) and not named_list and not ctx.get("town_code"))  # weak
    )

    if len(named_list) >= 2 and not city:
        # 多區比較：逐區各撈一個精簡摘要（不含待處理站清單，控制 token）
        cmp: list[dict] = []
        for t in named_list[:4]:
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
                buttons.append(_open_action(
                    {"name": rows[0]["name"], "station_uid": rows[0]["station_uid"]}))
        data["行政區比較"] = cmp
        panel = _compare_table(cmp, named_list[:4])
        c = alert_service.alerts(limit=1000)
        data["全市對照"] = _counts(c["items"])
        items = []
    elif (named_list[:1] or ctx.get("town_code")) and not city:
        named = named_list[0] if named_list else None
        tc = named["town_code"] if named else ctx.get("town_code")
        # 帶 town_code 的 scoped 查詢：完整、有該區自己的補取台數
        d = alert_service.alerts(town_code=tc, limit=1000)
        drows = d["items"]
        t = next((x for x in towns if x["town_code"] == tc), None)
        data["行政區"] = {
            "name": (t["town"] if t else tc),
            **_summ_block(d["summary"], drows),
            "最優先處理": (drows[0]["name"] if drows else None),  # 已排序，第一個就是最急
            "待處理站(依優先序)": _urgent_list(drows),
        }
        if ask_full:
            data["行政區"]["滿站站點"] = _side_names(drows, "full")
        if ask_short:
            data["行政區"]["空站站點"] = _side_names(drows, "shortage")
        panel = _build_list(q, drows, data["行政區"]["name"])
        # 開站按鈕跟著問句焦點：問滿站→給滿站；問空站→給空站；否則給最急的前幾個（限問了特定區）
        focus = ([i for i in drows if i["side"] == "full"] if ask_full
                 else [i for i in drows if i["side"] == "shortage"] if ask_short
                 else (drows if named else []))
        buttons += [_open_action(i) for i in focus[:3]]
        if named and named["town_code"] != ctx.get("town_code"):  # 問的區 ≠ 畫面正在看的區
            buttons.append({"label": f"切到 {data['行政區']['name']} 地圖",
                            "type": "filter_town", "value": tc})
        # 全市只放數量當對照，不放台數（避免被誤植進行政區句子）
        c = alert_service.alerts(limit=1000)
        data["全市對照"] = _counts(c["items"])
        items = drows
    else:
        c = alert_service.alerts(limit=1000)
        items = c["items"]
        data["全市"] = {**_summ_block(c["summary"], items),
                       "最優先處理": (items[0]["name"] if items else None),
                       "待處理站(依優先序)": _urgent_list(items)}
        if ask_full:
            data["全市"]["滿站站點"] = _side_names(items, "full", 15)
        if ask_short:
            data["全市"]["空站站點"] = _side_names(items, "shortage", 15)
        panel = _build_list(q, items, "全市")

    st = _find_station(q, station_repo.all_stations())
    if st is None and ctx.get("station_uid") and _THIS_STATION_RE.search(q):
        st = station_repo.find(ctx["station_uid"])
    if st:
        a = next((i for i in items if i["station_uid"] == st["station_uid"]), None)
        if a is None:  # 該站不在目前範圍的清單裡，另查一次
            a = next((i for i in alert_service.alerts(town_code=st["town_code"], limit=1000)["items"]
                      if i["station_uid"] == st["station_uid"]), None)
        data["站點"] = {
            "name": st["station_name"], "行政區": st["town"],
            "狀態": (f"{_SIDE_WORD.get(a['side'])}・{_LV_WORD.get(a['level'])}"
                    if a and a["level"] != "none" else "供需健康"),
            "可借": (a.get("now") or {}).get("avail") if a else None,
            "容量": (a.get("capacity") if a else st.get("capacity")),
            "建議": _act_phrase(a.get("dispatch")) if a else "—",
            "預計越線": _hhmm(a.get("onset")) if a else None,
            "常態水位": (a.get("baseline") if a else None),
        }
        buttons.insert(0, {"label": f"開啟 {st['station_name']}",
                           "type": "select_station", "value": st["station_uid"]})

    seen: set[str] = set()
    nav = [b for b in buttons if b["type"] == "filter_town"][:1]
    # 有結構化清單時，逐站「開啟」按鈕多餘（清單列本身可點）→ 只留切區按鈕
    stn = [] if panel else [
        b for b in buttons if b["type"] == "select_station"
        and not (b["value"] in seen or seen.add(b["value"]))
    ][:3]
    return data, nav + stn, panel


def _followups(q: str, data: dict | None) -> list[str]:
    """回答後的追問 chip：依這題撈到的脈絡（站 / 區 / 知識）給下一步可能想問的。
    chip 文字一律帶明確範圍（區名 / 「全市」）—— 點下去 proxy 才判得對，不會掉回畫面篩選的區。"""
    if data and "站點" in data:
        st = data["站點"]["name"]
        return [f"「{st}」為什麼建議這個台數？", f"{data['站點']['行政區']}還有哪些站要處理？", "現在全市概況？"]
    if data and "行政區比較" in data:
        ns = [t["name"] for t in data["行政區比較"]]
        pair = ns[:2] + ["", ""]
        return [f"{pair[0]}最急的站是哪個？", f"{pair[1]}最急的站是哪個？", "現在全市概況？"]
    if data and "行政區" in data:
        d = data["行政區"]["name"]
        return [f"{d}最急的站是哪個？", f"{d}哪些站要優先補車？", "看全市整體概況"]
    if data and "全市" in data:  # 答的是全市 → 追問也維持全市
        return ["全市哪些站要優先補車？", "全市哪些站要優先取車？", "空站門檻怎麼定的？"]
    if re.search(r"(為什麼|怎麼算|定義|門檻|規範|SOP|如何)", q):  # 知識題 → 引回即時查詢
        return ["現在全市概況？", "全市哪些站要優先補車？", "全市哪些站要優先取車？"]
    return ["哪些站要優先補車？", "哪些站要優先取車？", "空站門檻怎麼定的？"]


def _build_agent_prompt(q: str, ctx: dict, data: dict | None, panel: dict | None = None) -> str:
    if data and "站點" in data:
        looking = f"站點「{data['站點']['name']}」"
    elif data and "行政區比較" in data:
        looking = "、".join(t["name"] for t in data["行政區比較"])
    elif data and "行政區" in data:
        looking = data["行政區"]["name"]
    else:
        looking = "全市"
    parts = [f"使用者目前正在看：{looking}。"]
    if panel and panel["type"] == "list":
        parts.append(
            f"（畫面會另外用清單顯示「{panel['title']}」共 {len(panel['items'])} 站，含站名與台數，"
            "所以你只要寫 1～2 句總結：最急的是哪一站、集中在哪一帶、或建議的跑車順序，"
            "不要逐站條列、不要把整份清單再念一遍。）")
    elif panel and panel["type"] == "table":
        parts.append(
            "（畫面會另外用表格逐區列出各項數字與最急站，所以你只要寫 1～2 句總結："
            "哪一區最吃緊、哪一區最輕，不要逐區逐數字念一遍。）")
    if data and "全市" in data and _BROADEN_RE.search(q):
        # 「其他行政區呢？」這種：proxy 已判成看全市。純粹是「跳出這區、看其餘」的追問時，
        # 直接換成乾淨問法送給模型 —— 弱模型看到「其他」會鑽牛角尖說「其他區未提供」。
        rest = re.sub(r"行政區|區|的|呢|嗎|啊|如何|狀況|概況|怎樣|情形|現在|目前|[\s？?！!，,。.]",
                      "", _BROADEN_RE.sub("", q))
        if len(rest) <= 1:
            q = "現在全市整體概況如何？"
        else:
            parts.append("使用者要看的是全部行政區的整體狀況，用『全市』區塊直接回答，"
                         "不要說其他區未提供、不要叫使用者切換行政區。")
    if data:
        vnow = (ctx.get("virtual_now") or "")[:16]  # 截到分鐘，僅供你判讀時效，不要寫進答案
        parts += [
            f"目前即時調度資料（{vnow or '現在'}）：",
            json.dumps(data, ensure_ascii=False),
        ]
    parts += [
        "",
        f"使用者問：{q}",
        "",
        "回答要求："
        "① 純文字，不要用 Markdown（不要 **、#、- 條列符號）；最多 3～5 句話，講重點不要展開整段。"
        "② 數字一律照上面資料原文引用，不要自己估算或四捨五入。"
        "③ 問某個行政區時，只用『行政區』區塊的數字（含補取台數）；『全市對照』只是背景，不要混進行政區的句子。"
        "③-1 問多個行政區比較時，數字看『行政區比較』清單（各區各自的）；畫面已用表格逐區列出，"
        "你只寫 1～2 句總結（哪區最吃緊 / 最輕），不要逐區逐數字念、不要加總。"
        "④ 「最急 / 最優先」直接用『最優先處理』欄位的站名，不要自己從清單挑。"
        "⑤ 只是問狀態 / 數量 → 直接一句話回答；問「怎麼調 / 要不要補 / 先補哪」→ 先講現況再給一個行動建議。"
        "⑥ 答案裡不要出現「來源：」、檔名、時間或虛擬時鐘；知識庫查到的規則直接把內容講出來即可。"
        "⑦ 使用者打錯字或句子不完整時，用『正在看』的脈絡合理推測；"
        "若使用者只丟地名／站名、沒有完整問句，開頭先加一行「（理解為：<地名>目前調度狀況）」再回答；真的無法推測才反問一句。"
        "⑧ 「滿站」是還不了車的問題站，不是「安全」。被問「安全 / 健康 / 正常 / 沒問題」的站 → "
        "用『供需健康站數』欄位回答（無風險的站數），並說明這些站不會逐站列出；資料包沒有的東西不要編。"
        "⑨ 上面的資料包就是即時資料本身。有的數據直接回答，不要說「資料未顯示 / 未提供 / 不在此頁面」，"
        "也不要叫使用者切換頁面或行政區；問全市就用『全市』區塊直接答。"
        "⑩ 問「有哪些空站 / 滿站」時，把『空站站點』/『滿站站點』清單裡的站名念出來"
        "（最多約 10 個，更多用「等 N 站」帶過），不要只回一個數量。"
        "繁體中文。",
    ]
    return "\n".join(parts)


# ── AgentCore Harness ────────────────────────────────────
_boto_client = None


def _get_client():
    global _boto_client
    if _boto_client is None:
        import boto3

        _boto_client = boto3.client(
            "bedrock-agentcore",
            region_name=os.environ.get("AWS_REGION", "ap-northeast-1"),
        )
    return _boto_client


_STREAM_ERRORS = (
    "internalServerException", "modelStreamErrorException", "validationException",
    "throttlingException", "serviceUnavailableException",
)


def _invoke_harness(user_text: str, session_id: str) -> Iterator[dict]:
    """呼叫 AgentCore Harness（client.invoke_harness），把 Converse 串流轉成 SSE 事件。
    形狀對照 Harness 詳情頁「View invocation code」(Python)。

    只送最新一則使用者訊息；多輪脈絡靠 runtimeSessionId（Harness 端 Memory 保存）。
    citation：Harness 依 system prompt 會把「來源：xx.md」寫在答案文字裡，
      這裡另嘗試從 metadata 事件抽結構化來源（有就多送 {"type":"sources"}）。
    """
    client = _get_client()
    resp = client.invoke_harness(
        harnessArn=os.environ["ASSISTANT_HARNESS_ARN"],
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": user_text or "（無內容）"}]}],
    )
    for event in resp.get("stream", []):
        if "contentBlockDelta" in event:
            txt = (event["contentBlockDelta"].get("delta") or {}).get("text")
            if txt:
                yield {"type": "delta", "text": txt}
        elif "metadata" in event:
            src = _extract_sources(event["metadata"])
            if src:
                yield {"type": "sources", "items": src}
        else:
            for k in _STREAM_ERRORS:
                if isinstance(event.get(k), dict):
                    raise RuntimeError(event[k].get("message", k))


def _extract_sources(obj) -> list[dict]:
    if not isinstance(obj, dict):
        return []
    raw = obj.get("citations") or obj.get("sources") or obj.get("retrievalResults") or []
    out: list[dict] = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        meta = r.get("metadata") or {}
        title = (r.get("title") or meta.get("_document_title")
                 or meta.get("title") or meta.get("source") or "文件")
        snippet = r.get("snippet") or r.get("text") or (r.get("content") or {}).get("text")
        uri = r.get("uri") or r.get("url") or meta.get("uri")
        item = {"title": str(title)}
        if snippet:
            item["snippet"] = str(snippet)[:280]
        if uri:
            item["uri"] = str(uri)
        out.append(item)
    return out


# 模型偶爾仍會在結尾自己補「來源：<檔名>」（prompt ⑥ 已禁止，這裡再保險一層）。
# KB 檔名是 AgentCore Retrieve 機器產生的亂碼字串，對使用者沒意義，一律不顯示。
_SRC_MARK_RE = re.compile(r"(來源|資料來源|參考資料|出處|Source)\s*[:：]")


def _strip_source_line(text: str) -> str:
    """整段答案收齊後，切掉結尾自己冒出來的『來源：…』。"""
    m = _SRC_MARK_RE.search(text)
    return text[:m.start()].rstrip(" \n\r\t・,，、;；") if m else text


# ── 數字事後驗證：答案裡的數字必須都能在資料包裡找到 ─────────
_NUM_RE = re.compile(r"\d+")


def _unverified_numbers(answer: str, data: dict | None) -> list[str]:
    """答案出現、但資料包 JSON 裡找不到的數字（模型算錯 / 幻覺）。
    只在意 >= 2 位數：個位數誤判率高、殺傷力低，且幾乎必然剛好出現在 JSON 某處。"""
    if not data:
        return []
    hay = json.dumps(data, ensure_ascii=False)
    return [n for n in _NUM_RE.findall(answer) if len(n) >= 2 and n not in hay]


def _templated_answer(data: dict | None) -> str:
    """不靠 LLM、純從資料包組一句樸素但正確的摘要。
    用於：① 數字驗證失敗退回 ② Bedrock 呼叫失敗的 graceful degradation。"""
    if not data:
        return ""
    segs: list[str] = []
    cmp = data.get("行政區比較")
    if cmp:
        for t in cmp:
            tail = f"，最優先 {t['最優先處理']}" if t.get("最優先處理") else ""
            segs.append(f"{t['name']}：空站 {t['空站']}、滿站 {t['滿站']}，"
                        f"建議補車 {t['建議補車總量']} 台、取車 {t['建議取車總量']} 台{tail}。")
    blk = data.get("行政區") or (None if cmp else data.get("全市"))
    if blk:
        line = (f"{blk.get('name', '全市')}：空站 {blk['空站']}、滿站 {blk['滿站']}，"
                f"建議補車 {blk['建議補車總量']} 台、取車 {blk['建議取車總量']} 台")
        if blk.get("最優先處理"):
            line += f"。最優先處理 {blk['最優先處理']}"
        segs.append(line + "。")
    st = data.get("站點")
    if st:
        cap = f"／{st['容量']}" if st.get("容量") is not None else ""
        segs.append(f"{st['name']}（{st['行政區']}）：{st['狀態']}，"
                    f"可借 {st.get('可借', '—')}{cap}，{st.get('建議', '—')}。")
    return " ".join(s for s in segs if s)


# ── 串流切片 ──────────────────────────────────────────────
_CHUNK = int(os.environ.get("ASSISTANT_CHUNK", "20"))
# 預設「收齊再送」：答案完整生成後才做數字驗證 → 出糗的數字不會先流到畫面上。
# 開發時想看逐字串流可設 ASSISTANT_STREAM=1（此時數字問題只記 log、不攔）。
_STREAM_LIVE = os.environ.get("ASSISTANT_STREAM") == "1"
_MAX_Q_LEN = 400

# 開發用：ASSISTANT_DEBUG_DUMP=1（或給資料夾路徑）→ 每次請求把「送給 Harness 的整段 prompt +
# 資料包 + Nova 原始回覆 + 數字驗證 + 最終輸出」寫成一個檔，方便檢視 / diff。預設關。
_DEBUG_DUMP = os.environ.get("ASSISTANT_DEBUG_DUMP", "").strip()
_DEBUG_DIR = ("assistant_debug" if _DEBUG_DUMP in ("1", "true", "True", "yes")
              else _DEBUG_DUMP)


def _chunks(text: str) -> Iterator[str]:
    for i in range(0, len(text), _CHUNK):
        yield text[i:i + _CHUNK]


def _dump_debug(q: str, ctx: dict, prompt: str, sid: str, data: dict | None,
                panel: dict | None, nova_raw: str, bad: list, final: str) -> None:
    """把一次請求的完整內幕寫成一個檔（只在 ASSISTANT_DEBUG_DUMP 有設時）。"""
    if not _DEBUG_DIR:
        return
    try:
        import datetime
        os.makedirs(_DEBUG_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S_%f")[:-3]
        slug = re.sub(r"[^\w一-鿿]+", "", q)[:16] or "q"
        path = os.path.join(_DEBUG_DIR, f"{ts}_{slug}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join([
                f"=== REQUEST {ts} ===",
                f"session_id    : {sid}",
                f"effective_ctx : {json.dumps(ctx, ensure_ascii=False)}",
                f"question      : {q}",
                "",
                "--- 送給 Harness 的 messages[0].content（整段 prompt）---",
                prompt,
                "",
                "--- data 資料包（pretty）---",
                json.dumps(data, ensure_ascii=False, indent=2) if data else "(無)",
                "",
                "--- panel（list / table 事件）---",
                json.dumps(panel, ensure_ascii=False, indent=2) if panel else "(無)",
                "",
                "=== RESPONSE ===",
                f"nova_raw           : {nova_raw!r}",
                f"unverified_numbers : {bad}",
                f"final_sent         : {final!r}",
                "",
            ]))
        _log.info("assistant debug dump → %s", path)
    except Exception:  # noqa: BLE001 —— dump 失敗絕不能影響回應
        _log.exception("debug dump failed")


# ── 對外：組事件流 ────────────────────────────────────────
def chat_events(messages: list[dict], ctx: dict) -> Iterator[dict]:
    q = _last_user(messages)
    if not q:
        yield {"type": "error", "message": "沒有收到問題內容。"}
        return
    if len(q) > _MAX_Q_LEN:
        yield {"type": "error", "message": f"問題太長了，請精簡在 {_MAX_Q_LEN} 字以內再問一次。"}
        return

    ctx = ctx or {}

    # 亂打 / 無意義輸入 → 請對方講清楚，不打 Harness（省一次 LLM，也保證行為一致）
    if _is_noise(q):
        for c in _chunks(_NOISE_MSG):
            yield {"type": "delta", "text": c}
        yield {"type": "suggestions", "items": _scope_suggestions(ctx)}
        yield {"type": "done"}
        return

    # 純打招呼 / 社交話 → 簡短自我介紹，不撈資料、不打 Harness
    if _GREETING_RE.match(q.strip().lower()):
        for c in _chunks(_GREETING_MSG):
            yield {"type": "delta", "text": c}
        yield {"type": "suggestions", "items": _scope_suggestions(ctx)}
        yield {"type": "done"}
        return

    arn = os.environ.get("ASSISTANT_HARNESS_ARN", "").strip()

    # 助理未就緒（正式環境不該發生）
    if not arn:
        if _DOMAIN_RE.search(q):
            yield {"type": "error", "message": _UNAVAILABLE_MSG}
        else:
            for c in _chunks(_OFF_SCOPE_MSG):
                yield {"type": "delta", "text": c}
            yield {"type": "suggestions", "items": _scope_suggestions(ctx)}
            yield {"type": "done"}
        return

    # 範圍延續：當前問句沒帶範圍時，沿用對話裡最近一次明確指定的範圍，
    # 而不是一律掉回畫面篩選的那一區（「先問全市、再問哪些站要補車」要延續全市）。
    ctx = _effective_ctx(messages, ctx)

    try:
        data, buttons, panel = _gather_context(q, ctx)
    except AppError:
        data, buttons, panel = None, [], None

    prompt = _build_agent_prompt(q, ctx, data, panel)
    sid = _session_id(messages, ctx)

    def _tail():
        """答案之後的共用尾段：結構化清單 / 比較表 + 按鈕 + 追問 chip + done。"""
        if panel:
            yield panel  # {"type":"list"|"table", ...}
        if buttons:
            yield {"type": "actions", "items": buttons}
        yield {"type": "suggestions", "items": _followups(q, data)}
        yield {"type": "done"}

    nova_raw = ""
    bad: list[str] = []
    final = ""
    try:
        if _STREAM_LIVE:
            buf: list[str] = []
            for ev in _invoke_harness(prompt, sid):
                if ev.get("type") == "delta":
                    buf.append(ev["text"])
                    yield ev
            nova_raw = "".join(buf)
            final = _strip_source_line(nova_raw)
            bad = _unverified_numbers(final, data)
            if bad:
                _log.warning("number check failed (stream mode, not blocked): %s", bad)
        else:
            nova_raw = "".join(ev["text"] for ev in _invoke_harness(prompt, sid)
                               if ev.get("type") == "delta")
            answer = _strip_source_line(nova_raw).strip()
            bad = _unverified_numbers(answer, data)
            if bad:
                _log.warning("number check failed %s not in data → 退回模板；answer=%r", bad, answer)
                answer = _templated_answer(data) or answer
            final = answer or _templated_answer(data) or _UNAVAILABLE_MSG
            for c in _chunks(final):
                yield {"type": "delta", "text": c}
        _dump_debug(q, ctx, prompt, sid, data, panel, nova_raw, bad, final)
        yield from _tail()
    except Exception:  # noqa: BLE001 —— 任何失敗都轉成可讀輸出，不讓前端看到 stack
        _log.exception("invoke_harness failed")
        tmpl = _templated_answer(data)
        _dump_debug(q, ctx, prompt, sid, data, panel, nova_raw, ["<exception>"],
                    (tmpl + "（降級）") if tmpl else "<error>")
        if tmpl:  # graceful degradation：Bedrock 掛了還是給得出樸素但正確的現況
            for c in _chunks(tmpl + "（調度助理暫時無法回應，以上為系統即時摘要）"):
                yield {"type": "delta", "text": c}
            yield from _tail()
        else:
            yield {"type": "error", "message": _UNAVAILABLE_MSG}
