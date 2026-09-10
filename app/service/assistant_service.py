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
_CITY_RE = re.compile(r"(全市|全區|各區|所有|整體)")  # 問全市時不縮到單一行政區
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


def _is_noise(q: str) -> bool:
    """明顯亂打 / 無意義輸入（123、?!?、asdf…）→ 不必打 Harness。"""
    s = q.strip()
    if len(s) < 2:
        return True
    core = re.sub(r"[\s\d\W_]+", "", s)  # 去空白 / 數字 / 標點後剩的字
    if len(core) < 2:
        return True
    if not _CJK_RE.search(s) and not _DOMAIN_RE.search(s.lower()) and len(s) < 6:
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
    多個命中取「站名最長」的那個。"""
    frags = sorted(set(_FRAG_RE.findall(q)), key=len, reverse=True)
    if not frags:
        return None
    best: tuple[str, dict] | None = None
    for s in stations:
        n = s.get("station_name")
        if not isinstance(n, str) or len(n) < 2:
            continue
        if any(f in n for f in frags) or (len(n) >= 4 and n in q):
            if best is None or len(n) > len(best[0]):
                best = (n, s)
    return best[1] if best else None


def _named_town(q: str, towns: list[dict]) -> dict | None:
    """比對行政區：DB 名稱帶「區」（板橋區），使用者常只打「板橋」。"""
    for t in towns:
        name = t["town"]
        if name in q or (name.endswith("區") and name[:-1] in q):
            return t
    return None


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
    return {"label": f"在單站檢視開啟「{item['name']}」",
            "type": "select_station", "value": item["station_uid"]}


def _session_id(messages: list[dict], ctx: dict) -> str:
    """AgentCore runtimeSessionId 需 >= 33 字元，且同一輪對話要穩定（多輪記憶）。
    以第一則使用者訊息當種子雜湊 → 對話期間不變、換對話就換 id。"""
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


def _gather_context(q: str, ctx: dict) -> tuple[dict, list[dict]]:
    """依畫面脈絡（在看哪區 / 哪站）＋問句，撈一小包即時資料給 Harness 當事實依據。
    回傳 (給 LLM 的精簡 dict, 給前端的按鈕清單)。"""
    data: dict = {}
    buttons: list[dict] = []

    towns = station_repo.towns()
    named = _named_town(q, towns)
    tc = named["town_code"] if named else ctx.get("town_code")
    scoped = tc and not _CITY_RE.search(q)

    if scoped:
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
        if named:
            buttons += [_open_action(i) for i in drows[:3]]
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
        buttons.insert(0, {"label": f"在單站檢視開啟「{st['station_name']}」",
                           "type": "select_station", "value": st["station_uid"]})

    seen: set[str] = set()
    uniq = [b for b in buttons if not (b["value"] in seen or seen.add(b["value"]))][:3]
    return data, uniq


def _build_agent_prompt(q: str, ctx: dict, data: dict | None) -> str:
    if data and "站點" in data:
        looking = f"站點「{data['站點']['name']}」"
    elif data and "行政區" in data:
        looking = data["行政區"]["name"]
    else:
        looking = "全市"
    parts = [f"使用者目前正在看：{looking}。"]
    if data:
        parts += [
            f"目前即時調度資料（虛擬時鐘 {ctx.get('virtual_now') or '—'}）：",
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
        "④ 「最急 / 最優先」直接用『最優先處理』欄位的站名，不要自己從清單挑。"
        "⑤ 只是問狀態 / 數量 → 直接一句話回答；問「怎麼調 / 要不要補 / 先補哪」→ 先講現況再給一個行動建議。"
        "⑥ 需要調度規則或 SOP（台數怎麼算、何時暫不派車、風險分級）時，用知識庫工具檢索，答案結尾附「來源：檔名」。"
        "⑦ 使用者打錯字或講不清楚時，用『正在看』的脈絡合理推測，或反問一句確認。"
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


# ── 串流切片 ──────────────────────────────────────────────
_CHUNK = int(os.environ.get("ASSISTANT_CHUNK", "20"))


def _chunks(text: str) -> Iterator[str]:
    for i in range(0, len(text), _CHUNK):
        yield text[i:i + _CHUNK]


# ── 對外：組事件流 ────────────────────────────────────────
def chat_events(messages: list[dict], ctx: dict) -> Iterator[dict]:
    q = _last_user(messages)
    if not q:
        yield {"type": "error", "message": "沒有收到問題內容。"}
        return

    ctx = ctx or {}

    # 亂打 / 無意義輸入 → 請對方講清楚，不打 Harness（省一次 LLM，也保證行為一致）
    if _is_noise(q):
        for c in _chunks(_NOISE_MSG):
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

    try:
        data, buttons = _gather_context(q, ctx)
    except AppError:
        data, buttons = None, []

    try:
        for ev in _invoke_harness(_build_agent_prompt(q, ctx, data), _session_id(messages, ctx)):
            yield ev
        if buttons:
            yield {"type": "actions", "items": buttons}
        yield {"type": "done"}
    except Exception:  # noqa: BLE001 —— 任何失敗都轉成可讀錯誤，不讓前端看到 stack
        _log.exception("invoke_harness failed")
        yield {"type": "error", "message": _UNAVAILABLE_MSG}
