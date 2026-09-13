# 調度助理後端大腦。對外只用 chat_events；其餘為 eval / 測試方便一併 re-export。
#
# 一次請求的路（詳見 backend/調度助理-AgentCore串接.md）：
#   events.chat_events
#     ├─ scope：長度 / _is_noise / 打招呼 / 別的縣市 → 罐頭回覆（不打 Harness）
#     ├─ scope.effective_ctx：範圍延續（這句沒帶範圍 → 沿用最近 3 句內指定的）
#     ├─ datapkg.gather_context：查 alert_service 組資料包 JSON + 按鈕 + list/table panel
#     ├─ llm.build_agent_prompt → llm.invoke_harness（★ 打 Bedrock）
#     ├─ llm.unverified_numbers：≥2 位數的數字不在資料包 → 丟掉整段、退 llm.templated_answer
#     └─ 收尾：panel + actions + suggestions + done
#
# 模組：common（常數 / 正則 / 規則）· scope · datapkg · llm · events
from __future__ import annotations

from .datapkg import Scope, gather_context, resolve_scope
from .events import chat_events
from .llm import (
    build_agent_prompt,
    templated_answer,
    unverified_numbers,
)
from .scope import effective_ctx, is_noise, session_id

__all__ = [
    "chat_events",
    "gather_context",
    "resolve_scope",
    "Scope",
    "effective_ctx",
    "unverified_numbers",
    "templated_answer",
    "build_agent_prompt",
    "is_noise",
    "session_id",
]
