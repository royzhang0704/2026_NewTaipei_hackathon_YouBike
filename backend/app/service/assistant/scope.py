# ── 調度助理・意圖前置判斷 + 範圍解析 ────────────────────────
# _is_noise / 打招呼 / 別的縣市 這類「不打 Harness」的前置；以及「這句問句要看哪個範圍」
# （某站 / 某區 / 全市），含對話範圍延續（_effective_ctx）。
from __future__ import annotations

import hashlib
import re

from app.errors import AppError
from app.repository import station_repo

from . import common as C


def is_noise(q: str) -> bool:
    """明顯亂打 / 無意義輸入（123、?!?、asdf…）→ 不必打 Harness。"""
    s = q.strip()
    if len(s) < 2:
        return True
    core = re.sub(r"[\s\d\W_]+", "", s)  # 去空白 / 數字 / 標點後剩的字
    if len(core) < 2:
        return True
    # 非中文、又不含任何領域字 → 短句、或整串沒空白的單一 token（asdfgh、qwerty…）當亂打
    if not C.CJK_RE.search(s) and not C.DOMAIN_RE.search(s.lower()):
        if len(s) < 12 or " " not in s:
            return True
    return False


def last_user(messages: list[dict]) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"].strip()
    return ""


def find_station(q: str, stations: list[dict]) -> dict | None:
    """比對站名：站名整段出現在問句裡（刻意提整名）優先；否則靠 >= 3 字的片段命中，
    取「命中的片段最長」的那站 —— 不是站名最長的（不然「1號出口」會命中一堆站，
    最後選到名字最長的那個，而不是使用者真的講的那站）。

    片段只吃 >= 3 字：2 字的「公園 / 路口 / 一街」會命中一堆站，誤判率過高（對話史踩過）。"""
    frags = sorted({f for f in C.FRAG_RE.findall(q) if len(f) >= 3}, key=len, reverse=True)
    exact: tuple[str, dict] | None = None       # 站名整段出現在問句裡
    frag_hit: tuple[int, dict] | None = None     # (命中的最長片段長度, 站)
    for s in stations:
        n = s.get("station_name")
        if not isinstance(n, str) or len(n) < 2:
            continue
        if len(n) >= 4 and n in q:
            if exact is None or len(n) > len(exact[0]):
                exact = (n, s)
            continue
        m = next((f for f in frags if f in n), None)  # frags 已按長度降序 → 第一個命中就是最長片段
        if m and (frag_hit is None or len(m) > frag_hit[0]):
            frag_hit = (len(m), s)
    if exact:
        return exact[1]
    return frag_hit[1] if frag_hit else None


def named_towns(q: str, towns: list[dict]) -> list[dict]:
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


def q_has_scope(q: str, towns: list[dict]) -> bool:
    """這句問句自己有沒有帶範圍訊號（全市 / 某區 / 這站）。"""
    return bool(C.CITY_RE.search(q) or C.BROADEN_RE.search(q)
                or C.THIS_STATION_RE.search(q) or named_towns(q, towns))


def sticky_scope(messages: list[dict], current_q: str, towns: list[dict]) -> tuple[bool, str | None]:
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
        nl = named_towns(msg, towns)
        if C.CITY_ALLDISTRICTS_RE.search(msg) or C.BROADEN_RE.search(msg) or (C.CITY_RE.search(msg) and not nl):
            return True, None
        if nl:
            return False, nl[0]["town_code"]
    return False, None


def effective_ctx(messages: list[dict], ctx: dict) -> dict:
    """把「範圍延續」套進 ctx：當前問句沒帶範圍時，改用對話裡最近一次指定的範圍，
    而不是一律掉回畫面篩選的那一區。chat_events 與 eval 共用，確保資料包一致。

    ctx.scope_just_changed（前端這輪主動帶的旗標）優先於對話延續 —— 使用者剛切換篩選 /
    開新站時，這份 ctx 才是「畫面現在真的顯示什麼」的事實，不該被對話記憶蓋過去。
    沒有這個旗標（沒動過畫面）時，行為跟以前一樣，靠 sticky_scope 補位。"""
    ctx = ctx or {}
    q = last_user(messages)
    if not q:
        return ctx
    if ctx.get("scope_just_changed"):
        return ctx
    try:
        towns = station_repo.towns()
    except AppError:
        return ctx
    if q_has_scope(q, towns):
        return ctx
    city, town = sticky_scope(messages, q, towns)
    if city:
        return {**ctx, "town_code": None}
    if town:
        return {**ctx, "town_code": town}
    return ctx


def session_id(messages: list[dict], ctx: dict) -> str:
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


def scope_suggestions(ctx: dict) -> list[str]:
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
