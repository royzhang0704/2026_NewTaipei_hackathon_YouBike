#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════
# 調度助理 —— 黃金題庫回歸測試
#
# 每次改 prompt / 換模型 / 動 _gather_context 就跑一次，擋掉回歸。
# 針對每題斷言三類事：
#   ① 事實：答案裡 >= 2 位數的數字都能在資料包 JSON 裡找到（無幻覺 / 無算錯）
#   ② 格式：無 Markdown、無「來源：」、句數在上限內
#   ③ 行為：該拒絕的有拒絕（亂打 / 過長）、該提到的關鍵字有提到
#
# 前置需求（跑得動的話）：
#   - .env 已設 ASSISTANT_HARNESS_ARN + AWS 憑證（見 調度助理-AgentCore串接.md §3〜§4）
#   - demo 資料已載入、虛擬時鐘在有資料的區間（DEMO_SPEED 起服務即可）
#   沒有憑證時每題會是 ERROR，可先確認題庫格式與斷言邏輯。
#
# 用法：  cd backend && .venv/bin/python eval/run.py
#        .venv/bin/python eval/run.py --only district-compare,noise-digits
# ════════════════════════════════════════════════════════════
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # python-dotenv 沒裝也無妨
    pass

from app.service import assistant_service as A  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden.jsonl")
_SENT_RE = re.compile(r"[。！？!?\n]+")


def _collect(messages: list[dict], context: dict) -> dict:
    """跑一次 chat_events，把事件流攤平成好斷言的形狀。"""
    text_parts: list[str] = []
    err: str | None = None
    actions: list = []
    suggestions: list = []
    lst: list = []
    tbl: list = []
    lst_title = ""
    for ev in A.chat_events(messages, context):
        t = ev.get("type")
        if t == "delta":
            text_parts.append(ev.get("text", ""))
        elif t == "error":
            err = ev.get("message", "")
        elif t == "actions":
            actions = ev.get("items", [])
        elif t == "suggestions":
            suggestions = ev.get("items", [])
        elif t == "list":
            lst = ev.get("items", [])
            lst_title = ev.get("title", "")
        elif t == "table":
            tbl = ev.get("rows", [])
            lst_title = ev.get("title", "")
    return {
        "text": "".join(text_parts).strip(),
        "error": err,
        "actions": actions,
        "suggestions": suggestions,
        "list": lst,
        "table": tbl,
        "list_title": lst_title,
    }


def _sentences(text: str) -> int:
    return len([s for s in _SENT_RE.split(text) if s.strip()])


def _check(case: dict) -> list[str]:
    """回傳失敗訊息清單；空 = 通過。"""
    exp = case.get("expect", {})
    fails: list[str] = []

    q = next((m["content"] for m in reversed(case["messages"]) if m["role"] == "user"), "")
    ctx = case.get("context") or {}
    out = _collect(case["messages"], ctx)
    body = out["error"] or out["text"]

    if exp.get("is_error"):
        if not out["error"]:
            fails.append(f"預期 error 事件，卻正常回覆：{out['text'][:60]!r}")
    else:
        if out["error"]:
            fails.append(f"非預期 error：{out['error']!r}")
        if not out["text"]:
            fails.append("沒有任何文字回覆")

    for kw in exp.get("contains", []):
        if kw not in body:
            fails.append(f"缺關鍵字 {kw!r}")
    for kw in exp.get("not_contains", []):
        if kw in body:
            fails.append(f"出現不該有的 {kw!r}")

    if (mx := exp.get("max_sentences")) and out["text"]:
        n = _sentences(out["text"])
        if n > mx:
            fails.append(f"句數 {n} > 上限 {mx}")

    if exp.get("numbers_in_data") and out["text"] and not out["error"]:
        try:
            data, _, _ = A._gather_context(q, A._effective_ctx(case["messages"], ctx))
        except Exception as e:  # noqa: BLE001
            data = None
            fails.append(f"_gather_context 例外：{type(e).__name__}: {e}")
        bad = A._unverified_numbers(out["text"], data)
        if bad:
            fails.append(f"答案數字不在資料包：{bad}")

    if (need := exp.get("list_items")) and not out["error"]:
        if len(out["list"]) < need:
            fails.append(f"清單只有 {len(out['list'])} 列，預期 >= {need}（title={out['list_title']!r}）")

    if (need := exp.get("table_rows")) and not out["error"]:
        if len(out["table"]) < need:
            fails.append(f"表格只有 {len(out['table'])} 列，預期 >= {need}（title={out['list_title']!r}）")

    if exp.get("no_list") and (out["list"] or out["table"]):
        fails.append(f"不該有清單/表格卻出現：{out['list_title']!r}")

    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="逗號分隔的題目 id，只跑這幾題")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None

    with open(GOLDEN, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    if only:
        cases = [c for c in cases if c["id"] in only]

    passed = 0
    for c in cases:
        fails = _check(c)
        if fails:
            print(f"✗ {c['id']}")
            for m in fails:
                print(f"    - {m}")
        else:
            passed += 1
            print(f"✓ {c['id']}")

    print(f"\n{passed}/{len(cases)} 通過")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
