# ── 調度助理・對外事件流 ────────────────────────────────────
# chat_events：把「一次對話請求」轉成 SSE 事件流。這裡只做編排，實際邏輯散在
#   scope（意圖前置 + 範圍）、datapkg（資料包 + panel + 按鈕）、llm（prompt + Harness + 驗證 + 降級）。
from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator

from app.errors import AppError
from app.repository import station_repo

from . import common as C
from . import llm, scope
from .datapkg import gather_context

_log = logging.getLogger("assistant")


def _canned(msg: str, ctx: dict) -> Iterator[dict]:
    """罐頭回覆（亂打 / 打招呼 / 別的縣市 / 助理未就緒）：切段文字 + 相關問題 chip + done。"""
    for c in C.chunks(msg):
        yield {"type": "delta", "text": c}
    yield {"type": "suggestions", "items": scope.scope_suggestions(ctx)}
    yield {"type": "done"}


def chat_events(messages: list[dict], ctx: dict) -> Iterator[dict]:
    q = scope.last_user(messages)
    if not q:
        yield {"type": "error", "message": "沒有收到問題內容。"}
        return
    if len(q) > C.MAX_Q_LEN:
        yield {"type": "error", "message": f"問題太長了，請精簡在 {C.MAX_Q_LEN} 字以內再問一次。"}
        return

    ctx = ctx or {}

    # ── 不打 Harness 的前置 ──────────────────────────────
    if scope.is_noise(q):
        yield from _canned(C.NOISE_MSG, ctx)
        return
    if C.GREETING_RE.match(q.strip().lower()):
        yield from _canned(C.GREETING_MSG, ctx)
        return
    # 明顯是別的縣市 / 台北市的地點，且問句沒點到新北的區 / 站 → 直接說不在範圍
    if C.OTHER_CITY_RE.search(q):
        try:
            _towns = station_repo.towns()
            _off = not scope.named_towns(q, _towns) and not scope.find_station(q, station_repo.all_stations())
        except AppError:
            _off = True
        if _off:
            yield from _canned(C.OTHER_CITY_MSG, ctx)
            return

    arn = os.environ.get("ASSISTANT_HARNESS_ARN", "").strip()
    if not arn:  # 助理未就緒（正式環境不該發生）
        if C.DOMAIN_RE.search(q):
            yield {"type": "error", "message": C.UNAVAILABLE_MSG}
        else:
            yield from _canned(C.OFF_SCOPE_MSG, ctx)
        return

    # ── 撈資料 → 組 prompt → 打 Harness → 驗證 → 收尾 ──
    ctx = scope.effective_ctx(messages, ctx)
    try:
        data, buttons, panel = gather_context(q, ctx)
    except AppError:
        data, buttons, panel = None, [], None

    prompt = llm.build_agent_prompt(q, ctx, data, panel)
    sid = scope.session_id(messages, ctx)

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
        if C.STREAM_LIVE:
            buf: list[str] = []
            for ev in llm.invoke_harness(C.SYSTEM_PROMPT, prompt, sid):
                if ev.get("type") == "delta":
                    buf.append(ev["text"])
                    yield ev
            nova_raw = "".join(buf)
            final = llm.strip_source_line(nova_raw)
            bad = llm.unverified_numbers(final, data)
            if bad:
                _log.warning("number check failed (stream mode, not blocked): %s", bad)
        else:
            nova_raw = "".join(ev["text"] for ev in llm.invoke_harness(C.SYSTEM_PROMPT, prompt, sid)
                               if ev.get("type") == "delta")
            answer = llm.strip_source_line(nova_raw).strip()
            bad = llm.unverified_numbers(answer, data)
            if bad:
                _log.warning("number check failed %s not in data → 退回模板；answer=%r", bad, answer)
                answer = llm.templated_answer(data) or answer
            final = answer or llm.templated_answer(data) or C.UNAVAILABLE_MSG
            for c in C.chunks(final):
                yield {"type": "delta", "text": c}
        llm.dump_debug(q, ctx, C.SYSTEM_PROMPT, prompt, sid, data, panel, nova_raw, bad, final)
        yield from _tail()
    except Exception:  # noqa: BLE001 —— 任何失敗都轉成可讀輸出，不讓前端看到 stack
        _log.exception("invoke_harness failed")
        tmpl = llm.templated_answer(data)
        llm.dump_debug(q, ctx, C.SYSTEM_PROMPT, prompt, sid, data, panel, nova_raw, ["<exception>"],
                       (tmpl + "（降級）") if tmpl else "<error>")
        if tmpl:  # graceful degradation：Bedrock 掛了還是給得出樸素但正確的現況
            for c in C.chunks(tmpl + "（調度助理暫時無法回應，以上為系統即時摘要）"):
                yield {"type": "delta", "text": c}
            yield from _tail()
        else:
            yield {"type": "error", "message": C.UNAVAILABLE_MSG}


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
