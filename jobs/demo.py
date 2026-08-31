# ════════════════════════════════════════════════════════════
# jobs/demo.py —— demo 回放模式的開關
# 規格：meet/20260831/計劃-demo回放模式與重訓.md §2-2
#
#   uv run python -m jobs.demo --start    # 預載 4 月 + 設 demo 時鐘
#   uv run python -m jobs.demo --start --reset  # ★ 先清乾淨再預載（demo 重跑用）
#   uv run python -m jobs.demo --status   # 虛擬時刻／回放進度
#   uv run python -m jobs.demo --stop     # 清時鐘與書籤（level30 保留）
#   uv run python -m jobs.demo --stop --purge  # 連回放進 level30 的列一起刪
#
# --start 做四件事：
#   ① 預載 2026-04 整月 baseline_grid → level30（給足 48 格 context，
#      前端也能看整個 4 月的歷史曲線）
#   ② demo_t0_real = 現在、demo_t0_virtual = 2026-05-01 00:00
#   ③ current_slot = 2026-04-30 23:30 —— ★ 必要：殘留的真排程書籤
#      比虛擬 now 晚，不重設 tick 會判定「不落後」永遠不動
#   ④ 清 forecast_end（那是真排程的預測終點，對虛擬時間軸是謊言）
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
        # demo 重跑：清掉上一輪的回放列與預測，回到全新起跑線。
        # ★ 只清 demo 視窗（4~5 月）—— 真排程寫的 2026-08 之後不碰，
        #   舊 MOCK／真排程預測列（origin 在 8 月）也不碰（D2 定案保留）。
        #   附帶效益：誤混進 level30 的非 baseline_grid 來源列一併清掉，
        #   重載後來源純化為 baseline_grid 單一出處。
        with get_conn().transaction(), get_conn().cursor() as cur:
            cur.execute("DELETE FROM hackathon_backend_level30 "
                        "WHERE slot >= %s AND slot < '2026-06-01'",
                        (config.DEMO_PRELOAD_FROM,))
            n_lv = cur.rowcount
            cur.execute("DELETE FROM hackathon_backend_forecast_history "
                        "WHERE origin >= %s AND origin < '2026-06-01'",
                        (config.DEMO_PRELOAD_FROM,))
            n_fc = cur.rowcount
        print(f"── reset：清 level30 {n_lv:,} 列（4~5 月）"
              f"＋ forecast_history {n_fc:,} 列（demo 視窗 origin）")

    t0v = datetime.fromisoformat(config.DEMO_VIRTUAL_T0)
    last_slot = t0v - STEP                      # 起點前一格

    print(f"── 預載 {config.DEMO_PRELOAD_FROM} ~ {last_slot} baseline_grid → level30")
    with get_conn().transaction(), get_conn().cursor() as cur:
        cur.execute(
            """
            INSERT INTO hackathon_backend_level30
                   (station_uid, slot, avail, docks, is_observed)
            SELECT station_uid, slot, avail, docks, is_observed
              FROM baseline_grid
             WHERE slot >= %s AND slot <= %s AND avail IS NOT NULL
            ON CONFLICT (station_uid, slot) DO UPDATE SET
                   avail = EXCLUDED.avail,
                   docks = EXCLUDED.docks,
                   is_observed = EXCLUDED.is_observed,
                   is_imputed = 0
            """, (config.DEMO_PRELOAD_FROM, last_slot))
        n = cur.rowcount
    print(f"   {n:,} 列")

    t0r = datetime.now(TZ).replace(tzinfo=None)
    sc.set(sc.K_DEMO_T0_REAL, t0r)
    sc.set(sc.K_DEMO_T0_VIRTUAL, t0v)
    sc.set(sc.K_CURRENT_SLOT, last_slot)
    sc.set(sc.K_FORECAST_END, None)
    print(f"✓ demo 開始：虛擬 now = {t0v}（流速 ×{config.DEMO_SPEED:g}）"
          f"｜current_slot = {last_slot}")
    print("  下一輪 tick 就會回放第一格並觸發 Job B")
    return 0


def stop(purge: bool) -> int:
    if not sc.is_demo():
        print("（demo 未在進行，仍照清一遍鍵值）")
    for k in (sc.K_DEMO_T0_REAL, sc.K_DEMO_T0_VIRTUAL,
              sc.K_CURRENT_SLOT, sc.K_FORECAST_END, sc.K_VIRTUAL_NOW):
        sc.set(k, None)
    print("✓ 已清 demo_t0_real / demo_t0_virtual / current_slot / forecast_end / virtual_now")

    if purge:
        # 只刪回放範圍（4~5 月）—— 真排程寫的 2026-08 之後不碰
        with get_conn().transaction(), get_conn().cursor() as cur:
            cur.execute("DELETE FROM hackathon_backend_level30 "
                        "WHERE slot >= %s AND slot < '2026-06-01'",
                        (config.DEMO_PRELOAD_FROM,))
            print(f"✓ 已刪 level30 回放列 {cur.rowcount:,} 列（4~5 月）")
    else:
        print("  level30 的回放列保留（要清加 --purge；forecast_history 一律保留，"
              "model_job 欄可區分）")
    print("  真排程下一輪 tick 會自己對時補拉")
    return 0


def status() -> int:
    now = sc.effective_now()
    cur_slot = sc.get_ts(sc.K_CURRENT_SLOT)
    fe = sc.get_ts(sc.K_FORECAST_END)
    mode = ("靜態 virtual_now（時鐘不走！）" if sc.is_virtual()
            else f"demo 回放（×{config.DEMO_SPEED:g}）" if sc.is_demo()
            else "真實時間（demo 未啟動）")
    print(f"模式       {mode}")
    print(f"有效 now   {now}")
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
    ap.add_argument("--purge", action="store_true",
                    help="與 --stop 併用：連 level30 的回放列（4~5 月）一起刪")
    ap.add_argument("--reset", action="store_true",
                    help="與 --start 併用：先清回放列與 demo 視窗的預測再預載（重跑）")
    a = ap.parse_args()
    if a.start:
        return start(a.reset)
    if a.stop:
        return stop(a.purge)
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
