#!/usr/bin/env bash
set -euo pipefail

# ════════════════════════════════════════════════════════════
# ★★ 2026-09-12：後端啟動就只做一件事 —— 提供 API。
#   出處：meet/20260912/計劃-demo回放邏輯重整.md §1-4／§3-5。
#
#   舊版在這裡用 RUN_TICK=1 起一個背景 tick 迴圈，等於「啟動 API」順帶
#   「啟動排程」。那會出兩種事：
#     ① 起兩個 API 副本 = 兩個迴圈同時推進虛擬時鐘（job_run 同 slot 兩列、
#        endpoint 被打兩次，**完全不會報錯**）
#     ② 想單純看 API 的人被迫也在跑排程
#
#   回放推進者現在只有一個入口，自己開一個進程／容器跑：
#       uv run python -m jobs.demo --run
#   它在 DB 有租約（sys_config.demo_loop_lease），重複啟動會被擋下來。
#
#   ⚠ 流速不必再用 DEMO_SPEED 環境變數帶 —— 真相在 sys_config.demo_speed，
#     要調速請下 `uv run python -m jobs.demo --speed N`。
# ════════════════════════════════════════════════════════════

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
