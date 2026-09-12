# ── 調度助理・LLM I/O ───────────────────────────────────────
# 組 prompt（資料包 + 問句 + 規則）、呼叫 AgentCore Harness、把 Converse 串流轉 SSE、
# 數字事後驗證、Bedrock 掛掉 / 驗證失敗時的純模板降級、除錯 dump。
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import threading
import time
from collections.abc import Iterator

from app.repository import station_repo

from . import common as C

_log = logging.getLogger("assistant")


def _actual_view(ctx: dict) -> str:
    """畫面『實際』顯示什麼——從 ctx（前端真正在看的站/區）算，不是這題問句解析出來的範圍。
    兩者常常不同（問別區狀態時本來就是），差別只在於：這裡講的是事實，looking 是這題資料對應的範圍。"""
    uid = ctx.get("station_uid")
    if uid:
        st = station_repo.find(uid)
        if st:
            return f"站點「{st['station_name']}」（{st['town']}）"
    code = ctx.get("town_code")
    if code:
        for t in station_repo.towns():
            if t.get("town_code") == code:
                return t["town"]
    return "全市"


# ── prompt ───────────────────────────────────────────────
def build_agent_prompt(q: str, ctx: dict, data: dict | None, panel: dict | None = None) -> str:
    if data and "站點" in data:
        looking = f"站點「{data['站點']['name']}」"
    elif data and "行政區比較" in data:
        looking = "、".join(t["name"] for t in data["行政區比較"])
    elif data and "行政區" in data:
        looking = data["行政區"]["name"]
    else:
        looking = "全市"
    actual = _actual_view(ctx)
    parts = [f"畫面目前顯示：{actual}。"]
    if looking not in actual and actual not in looking:
        # 這題資料的範圍跟畫面實際顯示的不一樣（例如問了別區 / 問「能不能切換」）——
        # 規則⑬會擋住「已經切換」這種話，這裡只要老實講資料對應哪個範圍。
        parts.append(f"使用者問的是「{looking}」的資料（下面已附上）。")
    if panel and panel["type"] == "list":
        parts.append(
            f"（畫面會另外用清單顯示「{panel['title']}」共 {len(panel['items'])} 站，含站名與台數，"
            "所以你只要寫 1～2 句總結：最急的是哪一站、集中在哪一帶、或建議的跑車順序，"
            "不要逐站條列、不要把整份清單再念一遍。）")
    elif panel and panel["type"] == "table":
        parts.append(
            "（畫面會另外用表格逐區列出各項數字與最急站，所以你只要寫 1～2 句總結："
            "哪一區最吃緊、哪一區最輕，不要逐區逐數字念一遍。）")

    q_for_llm = q
    if data and "全市" in data and C.BROADEN_RE.search(q):
        # 「其他行政區呢？」這種：proxy 已判成看全市。純粹是「跳出這區、看其餘」的追問時，
        # 直接換成乾淨問法送給模型 —— 弱模型看到「其他」會鑽牛角尖說「其他區未提供」。
        rest = re.sub(r"行政區|區|的|呢|嗎|啊|如何|狀況|概況|怎樣|情形|現在|目前|[\s？?！!，,。.]",
                      "", C.BROADEN_RE.sub("", q))
        if len(rest) <= 1:
            q_for_llm = "現在全市整體概況如何？"
        else:
            parts.append("使用者要看的是全部行政區的整體狀況，用『全市』區塊直接回答，"
                         "不要說其他區未提供、不要叫使用者切換行政區。")

    if data:
        vnow = (ctx.get("virtual_now") or "")[:16]  # 截到分鐘，僅供你判讀時效，不要寫進答案
        parts += [
            f"目前即時調度資料（{vnow or '現在'}）：",
            json.dumps(data, ensure_ascii=False),
        ]
    # RULES 刻意留在這裡（user message 尾巴，緊接生成點之前），不併進 common.SYSTEM_PROMPT——
    # 實測 Nova 2 Lite 對「離生成點較遠」的規則遵循度會下降，見 common.py 的說明。
    parts += ["", f"使用者問：{q_for_llm}", "", "回答要求：" + "".join(C.RULES)]
    return "\n".join(parts)


# ── AgentCore Harness ────────────────────────────────────
_boto_client = None
_STREAM_ERRORS = (
    "internalServerException", "modelStreamErrorException", "validationException",
    "throttlingException", "serviceUnavailableException",
)

# 比賽規則：Bedrock 請求需控制在 1 RPS 以下。整個後端只有 invoke_harness() 這一處會打
# Bedrock，卡在這裡就涵蓋所有路徑（app 請求、eval/run.py）。用 threading.Lock 不用
# asyncio.Lock——chat_events 是同步 generator，Starlette 的 StreamingResponse 會把它丟進
# thread pool 執行，不同 HTTP 請求跑在不同 thread，asyncio.Lock 鎖不住跨 thread 的情況。
# ASSISTANT_BEDROCK_MIN_INTERVAL=0（正式環境）可關閉；只保證「同一個 process 內」的間隔，
# 不同 process／多 instance 同時打同一帳號仍可能疊加超過，見文件說明。
_rate_lock = threading.Lock()
_last_call_at = 0.0


def _throttle() -> None:
    global _last_call_at
    if C.BEDROCK_MIN_INTERVAL <= 0:
        return
    with _rate_lock:
        wait = _last_call_at + C.BEDROCK_MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


def _get_client():
    global _boto_client
    if _boto_client is None:
        import boto3

        _boto_client = boto3.client(
            "bedrock-agentcore",
            # .env 的 AWS_REGION 一定會覆蓋這個預設值；這裡只是「.env 沒讀到時」的安全網，
            # 保持跟目前實際在用的帳號/region 一致，避免靜默連到錯的 region 產生難懂的錯誤。
            region_name=os.environ.get("AWS_REGION", "us-west-2"),
        )
    return _boto_client


def invoke_harness(system_text: str, user_text: str, session_id: str) -> Iterator[dict]:
    """呼叫 AgentCore Harness（client.invoke_harness），把 Converse 串流轉成 SSE 事件。
    system_text（角色/KB規則/12條規則）走 systemPrompt 參數，跟 user_text（資料包+問句）
    結構性分開送——systemPrompt 會覆蓋 Harness 資源在 console 存的預設，不會疊加。
    只送最新一則使用者訊息；多輪脈絡靠 runtimeSessionId（Harness 端 Memory 保存）。"""
    _throttle()
    client = _get_client()
    resp = client.invoke_harness(
        harnessArn=os.environ["ASSISTANT_HARNESS_ARN"],
        runtimeSessionId=session_id,
        systemPrompt=[{"text": system_text}],
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


def strip_source_line(text: str) -> str:
    """整段答案收齊後，切掉結尾自己冒出來的『來源：…』。"""
    m = C.SRC_MARK_RE.search(text)
    return text[:m.start()].rstrip(" \n\r\t・,，、;；") if m else text


# ── 數字事後驗證 + 純模板降級 ────────────────────────────
def unverified_numbers(answer: str, data: dict | None) -> list[str]:
    """答案出現、但資料包 JSON 裡找不到的數字（模型算錯 / 幻覺）。
    只在意 >= 2 位數：個位數誤判率高、殺傷力低，且幾乎必然剛好出現在 JSON 某處。
    先把千分位逗號拿掉（Nova 有時回「3,877」，會被切成 3 + 877 而誤判）。"""
    if not data:
        return []
    hay = json.dumps(data, ensure_ascii=False)
    clean = re.sub(r"(?<=\d)[,，](?=\d)", "", answer)
    return [n for n in C.NUM_RE.findall(clean) if len(n) >= 2 and n not in hay]


def templated_answer(data: dict | None) -> str:
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


# ── 除錯 dump ────────────────────────────────────────────
def dump_debug(q: str, ctx: dict, system_text: str, prompt: str, sid: str, data: dict | None,
               panel: dict | None, nova_raw: str, bad: list, final: str) -> None:
    """把一次請求的完整內幕寫成一個檔（只在 ASSISTANT_DEBUG_DUMP 有設時）。"""
    if not C.DEBUG_DIR:
        return
    try:
        os.makedirs(C.DEBUG_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S_%f")[:-3]
        slug = re.sub(r"[^\w一-鿿]+", "", q)[:16] or "q"
        path = os.path.join(C.DEBUG_DIR, f"{ts}_{slug}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join([
                f"=== REQUEST {ts} ===",
                f"session_id    : {sid}",
                f"effective_ctx : {json.dumps(ctx, ensure_ascii=False)}",
                f"question      : {q}",
                "",
                "--- systemPrompt（角色/KB規則/12條規則，覆蓋 console 預設）---",
                system_text,
                "",
                "--- 送給 Harness 的 messages[0].content（資料包 + 問句）---",
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
