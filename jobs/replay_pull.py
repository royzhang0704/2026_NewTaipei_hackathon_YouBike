# ════════════════════════════════════════════════════════════
# jobs/replay_pull.py —— Job A′：demo 回放版的「拉當下水位」
# 規格：meet/20260831/計劃-demo回放模式與重訓.md §2-1
#
# 真排程的 Job A 打 TDX；回放模式資料早就在 baseline_grid 裡，
# 「抓取」退化成「對時」：把當下 slot 那一格從 baseline_grid 搬進
# level30，其餘照抄 Job A 的骨架（覆蓋率門檻 → job_run → 書籤 → Job B）。
#
# ★ 不寫 actual_history、不做週期補值 —— baseline_grid 是 ground truth，
#   回放不得污染，也不需要補值（缺格就是當年真實的缺格）。
# ★ 只搬 avail IS NOT NULL 的列：baseline_grid 的 NULL 格 = 當時缺觀測，
#   照 level30 慣例用「沒有列」表達缺格，寫 NULL 列進去反而混淆 tail()。
# ════════════════════════════════════════════════════════════
from datetime import datetime, timedelta

from app import config
from app.repository import job_run_repo, sys_config_repo
from app.repository.db import get_conn

JOB_NAME = "pull_replay"


def copy_range(since: datetime | None, until: datetime) -> tuple[int, int]:
    """baseline_grid 的 (since, until] 各格 → level30。

    回 (until 那一格的列數, 全部寫入列數)。
    ★ 與真排程 Job A 的根本差異：中間漏掉的格子資料都在 baseline_grid，
      一次 INSERT..SELECT 就補齊 —— 機器睡著醒來自動收斂，不需要 Job C。
      since=None（書籤遺失）時只搬 until 一格，不整片重灌。
    """
    lo = since if since is not None else until - timedelta(minutes=config.FREQ_MIN)
    with get_conn().transaction(), get_conn().cursor() as cur:
        cur.execute(
            """
            INSERT INTO hackathon_backend_level30
                   (station_uid, slot, avail, docks, is_observed)
            SELECT station_uid, slot, avail, docks, is_observed
              FROM baseline_grid
             WHERE slot > %s AND slot <= %s AND avail IS NOT NULL
            ON CONFLICT (station_uid, slot) DO UPDATE SET
                   avail = EXCLUDED.avail,
                   docks = EXCLUDED.docks,
                   is_observed = EXCLUDED.is_observed,
                   is_imputed = 0
            """, (lo, until))
        n_total = cur.rowcount
        # ★ 數「有值的列」不是「is_observed=1 的列」—— baseline_grid 的
        #   填補列（carry-forward，is_observed=0）也是有效水位，
        #   與 Job A「寫入即計數」的語意對齊。只數實測會把覆蓋率
        #   砍到 5 成以下，每一格都誤判 skipped。
        cur.execute("SELECT count(*) AS n FROM hackathon_backend_level30 "
                    "WHERE slot = %s AND avail IS NOT NULL", (until,))
        n_last = cur.fetchone()["n"]
    return n_last, n_total


def run(slot: datetime, trigger: bool = True,
        since: datetime | None = None) -> tuple[bool, str]:
    """跑一輪 Job A′。介面對齊 pull_realtime.run —— tick 兩邊可互換。
    since = 上一次回放到的 slot（tick 的 latest）；中間的洞一併搬。"""
    run_id = job_run_repo.start(JOB_NAME, slot)
    try:
        n_grid, n_total = copy_range(since, slot)
        with get_conn().cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM hackathon_backend_station")
            total = cur.fetchone()["n"]
        need = int(total * config.JOB_A_MIN_COVERAGE)
        print(f"   回放 {slot} → 本格 {n_grid} 列／含補洞共 {n_total} 列"
              f"（主檔 {total} 站，門檻 {config.JOB_A_MIN_COVERAGE:.0%} = {need}）")

        detail = {"replayed": n_grid, "rows_total": n_total,
                  "virtual_now": str(sys_config_repo.effective_now())}

        if n_grid < need:
            # 半份資料打出來的預測比沒有更糟（與 Job A 同規則）。
            # demo 範圍內出現這情況 = 該 slot 當年整片缺觀測，跳過這格。
            msg = f"覆蓋率不足：{n_grid} < {need}，不觸發 Job B"
            print(f"   ✗ {msg}")
            job_run_repo.finish(run_id, "skipped", stations_ok=n_grid,
                                rows_written=n_total, error=msg, detail=detail)
            # ★ 書籤照樣前進 —— 不然 tick 每分鐘重試同一個壞 slot，永遠卡住
            sys_config_repo.set(sys_config_repo.K_CURRENT_SLOT, slot)
            return False, msg

        job_run_repo.finish(run_id, "success", stations_ok=n_grid,
                            rows_written=n_total, detail=detail)
        sys_config_repo.set(sys_config_repo.K_CURRENT_SLOT, slot)
        print(f"✓ Job A′ success（stations_ok={n_grid}）")

        if trigger:
            print(f"── 觸發 Job B（origin={slot}）")
            from jobs import batch_predict
            try:
                print(f"   {batch_predict.run(slot)}")
            except Exception as e:      # Job B 的失敗不該蓋掉 Job A′ 的成功
                print(f"   Job B 失敗（已記在它自己那列）：{type(e).__name__}: {e}")
        return True, f"stations_ok={n_grid}"

    except Exception as e:
        job_run_repo.finish(run_id, "failed", error=f"{type(e).__name__}: {e}")
        raise
