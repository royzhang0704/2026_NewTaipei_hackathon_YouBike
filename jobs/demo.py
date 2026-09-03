# ════════════════════════════════════════════════════════════
# jobs/demo.py —— demo 回放模式的開關
# 規格：meet/20260831/計劃-demo回放模式與重訓.md §2-2
#
#   uv run python -m jobs.demo --start    # 設 demo 時鐘
#   uv run python -m jobs.demo --start --reset  # ★ 先清預測再起跑（demo 重跑用）
#     ★ --reset 清三張表：forecast_history／risk_snapshot／forecast_run。
#       主檔（forecast_run）漏清 = 冪等判定說「已預測過」→ tick 整輪跳過 → 畫面空白。
#   uv run python -m jobs.demo --status   # 虛擬時刻／回放進度
#   uv run python -m jobs.demo --stop     # 清時鐘與書籤
#
# ★★ 2026-09-02：本檔不再碰 level30（預載與 --purge 都移除了）。
#   出處：meet/20260902/計劃-level30灌歷史與無限carry.md 決策 13。
#   理由：level30 的 04~07 月是「歷史真相的重採樣」，不是 demo 產生的狀態
#   —— reset demo 不該動它。單一出處改成 backend/sql/43_level30_carry.sql，
#   要重建就跑那支（它自己 DELETE 04-01~08-01 再全量重灌，冪等）。
#   ⚠ 舊 --reset 的附帶效益「把誤混進 level30 的非 baseline_grid 來源列
#     一併清掉」沒有遺失 —— 43 的 DELETE + 全量重灌提供同樣的保證。
#
# --start 做三件事：
#   ① demo_t0_real = 現在、demo_t0_virtual = 2026-05-01 00:00
#   ② current_slot = 2026-04-30 23:30 —— ★ 必要：殘留的真排程書籤
#      比虛擬 now 晚，不重設 tick 會判定「不落後」永遠不動
#   ③ 清 forecast_end（那是真排程的預測終點，對虛擬時間軸是謊言）
#
# --stop 清 demo 兩鍵 + current_slot + forecast_end + virtual_now（保險）。
#   ★ current_slot 清掉後 tick 會回頭問 level30 的 max(slot)，
#     發現落後真實時間而自動補拉 —— 真排程自己收斂，不必人工對時。
# ════════════════════════════════════════════════════════════
import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import config
from app.repository import sys_config_repo as sc
from app.repository.db import get_conn

TZ = ZoneInfo(config.TZ_TAIPEI)
STEP = timedelta(minutes=config.FREQ_MIN)


def start(reset: bool = False) -> int:
    if sc.is_demo() and not reset:
        print("✗ demo 已在進行中（先 --stop，或直接 --start --reset 重跑）")
        return 1
    if sc.is_virtual():
        print("✗ 靜態 virtual_now 設著（優先序比 demo 高，時鐘會不走）——"
              "先清：uv run python -m app.repository.sys_config_repo --set virtual_now -")
        return 1

    if reset:
        # demo 重跑：清掉上一輪的預測，回到全新起跑線。
        # ★ 只清 demo 視窗（4~5 月）—— 真排程寫的 2026-08 之後不碰，
        #   舊 MOCK／真排程預測列（origin 在 8 月）也不碰（D2 定案保留）。
        # ★★ 9/2 起 level30 不在這裡清（決策 13）。要重建歷史區請跑
        #     backend/sql/43_level30_carry.sql —— 它才是那段的單一出處。
        with get_conn().transaction(), get_conn().cursor() as cur:
            cur.execute("DELETE FROM hackathon_backend_forecast_history "
                        "WHERE origin >= %s AND origin < '2026-06-01'",
                        (config.DEMO_PRELOAD_FROM,))
            n_fc = cur.rowcount
            # ★★ 主檔與風險快照一定要跟著清（9/1）——
            #   不清主檔的下場：reset 完了，主檔那些 origin 還寫著
            #   predict_status='done'，tick 一跑冪等判定會**整輪跳過**，
            #   demo 畫面整片空白。這是這組改動最容易踩的坑。
            cur.execute("DELETE FROM hackathon_backend_risk_snapshot "
                        "WHERE origin >= %s AND origin < '2026-06-01'",
                        (config.DEMO_PRELOAD_FROM,))
            n_rk = cur.rowcount
            cur.execute("DELETE FROM hackathon_backend_forecast_run "
                        "WHERE origin >= %s AND origin < '2026-06-01'",
                        (config.DEMO_PRELOAD_FROM,))
            n_fr = cur.rowcount
        print(f"── reset：清 forecast_history {n_fc:,} 列（demo 視窗 origin）"
              f"＋ risk_snapshot {n_rk:,} 列 ＋ forecast_run {n_fr:,} 列"
              f"（★ level30 不動，歷史區的出處是 sql/43_level30_carry.sql）")

    t0v = datetime.fromisoformat(config.DEMO_VIRTUAL_T0)
    last_slot = t0v - STEP                      # 起點前一格

    # ★ 9/2 起不預載（決策 13）。改成起跑前先確認 43 灌過了 —— 少了這一步
    #   tail() 抓不到 48 格，每站都 INSUFFICIENT_HISTORY，而且安靜無 log。
    with get_conn().cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM hackathon_backend_level30 "
                    "WHERE slot > %s AND slot <= %s",
                    (last_slot - STEP * config.CONTEXT, last_slot))
        n_ctx = cur.fetchone()["n"]
    if n_ctx == 0:
        print(f"✗ level30 在 {last_slot} 之前的 {config.CONTEXT} 格 context 是空的。")
        print("  先跑： PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres "
              "-d youbike -v ON_ERROR_STOP=1 -f backend/sql/43_level30_carry.sql")
        return 1
    print(f"── context 檢查：{last_slot} 之前 {config.CONTEXT} 格內有 {n_ctx:,} 列")

    t0r = datetime.now(TZ).replace(tzinfo=None)
    sc.set(sc.K_DEMO_T0_REAL, t0r)
    sc.set(sc.K_DEMO_T0_VIRTUAL, t0v)
    sc.set(sc.K_CURRENT_SLOT, last_slot)
    sc.set(sc.K_FORECAST_END, None)
    print(f"✓ demo 開始：虛擬 now = {t0v}（流速 ×{config.DEMO_SPEED:g}）"
          f"｜current_slot = {last_slot}")
    print("  下一輪 tick 就會回放第一格並觸發 Job B")
    return 0


def stop() -> int:
    if not sc.is_demo():
        print("（demo 未在進行，仍照清一遍鍵值）")
    for k in (sc.K_DEMO_T0_REAL, sc.K_DEMO_T0_VIRTUAL,
              sc.K_CURRENT_SLOT, sc.K_FORECAST_END, sc.K_VIRTUAL_NOW):
        sc.set(k, None)
    print("✓ 已清 demo_t0_real / demo_t0_virtual / current_slot / forecast_end / virtual_now")

    # ★ 9/2 起 --purge 已移除（決策 13）：level30 不歸本檔管。
    #   要重建歷史區跑 sql/43_level30_carry.sql，它自己會 DELETE 再重灌。
    print("  level30 不動（歷史區的出處是 sql/43_level30_carry.sql；"
          "forecast_history 一律保留，model_job 欄可區分）")
    print("  真排程下一輪 tick 會自己對時補拉")
    return 0


def status() -> int:
    now = sc.effective_now()
    cur_slot = sc.get_ts(sc.K_CURRENT_SLOT)
    fe = sc.get_ts(sc.K_FORECAST_END)
    until = sc.get_ts(sc.K_DEMO_UNTIL)
    mode = ("靜態 virtual_now（時鐘不走！）" if sc.is_virtual()
            else f"demo 回放（×{config.DEMO_SPEED:g}）⏸ 已到終點，時鐘停表中"
                 if sc.demo_ended()
            else f"demo 回放（×{config.DEMO_SPEED:g}）" if sc.is_demo()
            else "真實時間（demo 未啟動）")
    print(f"模式       {mode}")
    print(f"有效 now   {now}")
    print(f"回放終點   {until or '(無，一路跑下去)'}")
    print(f"觸發 Job B {'是' if sc.replay_predict() else '否（不打 endpoint）'}")
    print(f"回放到     {cur_slot or '(無)'}")
    print(f"預測終點   {fe or '(無)'}")
    with get_conn().cursor() as cur:
        cur.execute("""
            SELECT count(*) AS n, min(slot) AS s0, max(slot) AS s1
              FROM hackathon_backend_level30 WHERE slot < '2026-06-01'""")
        r = cur.fetchone()
        print(f"level30    回放範圍 {r['n']:,} 列"
              + (f"（{r['s0']} ~ {r['s1']}）" if r["n"] else ""))
        cur.execute("""
            SELECT count(*) AS n, count(DISTINCT origin) AS o, max(origin) AS mo
              FROM hackathon_backend_forecast_history
             WHERE origin >= %s AND origin < '2026-06-01'""",
            (config.DEMO_PRELOAD_FROM,))
        r = cur.fetchone()
        print(f"預測累積   {r['n']:,} 列 / {r['o']} 個 origin"
              + (f"（最新 {r['mo']}）" if r["o"] else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="demo 回放模式開關")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", action="store_true")
    g.add_argument("--stop", action="store_true")
    g.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true",
                    help="與 --start 併用：先清 demo 視窗的預測再起跑（重跑）")
    a = ap.parse_args()
    if a.start:
        return start(a.reset)
    if a.stop:
        return stop()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
