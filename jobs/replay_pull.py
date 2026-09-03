# ════════════════════════════════════════════════════════════
# jobs/replay_pull.py —— Job A′：demo 回放版的「拉當下水位」
# 規格：meet/20260831/計劃-demo回放模式與重訓.md §2-1
#
# 真排程的 Job A 打 TDX；回放模式資料早就在 baseline_grid 裡，
# 「抓取」退化成「對時」：把當下 slot 那一格從 baseline_grid 搬進
# level30，其餘照抄 Job A 的骨架（覆蓋率門檻 → job_run → 書籤 → Job B）。
#
# ★ 不寫 actual_history、不做週期補值 —— baseline_grid 是 ground truth，
#   回放不得污染。
#
# ★★ 2026-09-02 改：NULL 格不再跳過，改成無限 carry 並標 is_imputed=2。
#   出處：meet/20260902/計劃-level30灌歷史與無限carry.md 決策 2/3。
#   （舊行為是 `AND avail IS NOT NULL`，NULL 格不寫列，錨點因此退後 →
#     STALE_ANCHOR → 那些站在 demo 畫面上整個消失。使用者要「站不要消失」。）
#
#   ⚠ carry 的來源是 **baseline_grid**，不是 level30。這是與
#     43_level30_carry.sql 等價的前提 —— 43 是拿 baseline_grid 全窗算的，
#     回放若改看 level30，level30 一被清過就算出不同的值，兩處立刻分岔。
#
#   算法與 43 等價，但拆成兩段以免逐列 lateral：
#     ① 在 (since, until] 範圍內用 island 編號 carry（同 43）
#     ② 範圍開頭就沒有值的站，用 seed 往 slot <= since 找最後一個真值。
#        seed 每站只查一次，成本與格數無關 —— 機器睡三天醒來補幾千格時
#        這個差別是幾百萬次 lateral 與一千六百次的差別。
#
#   ⚠ seed 的下界 = config.DEMO_PRELOAD_FROM（= 43 的窗起點 2026-04-01），
#     這一條不能省。43 的規則 1 是「該站在**窗內**首個有水位的格之前不寫列」；
#     seed 若不設下界就會撈到窗起點之前的真值，於是回放寫出 43 不會寫的列。
#     ★ 驗收 9 首跑就是這樣多出 240 列 / 5 站 —— 那 5 站（NWT500205038、
#       NWT500207043、NWT500207094、NWT500214032、NWT500214069）在窗內的
#       首個水位遠在 2026-07-10，4 月到 7 月初整段是空的。
#       「回放永遠不落在那個邊界」的假設是錯的，別再犯。
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
            WITH tgt AS (
              SELECT station_uid, slot, avail, docks, is_observed
                FROM baseline_grid
               WHERE slot > %(lo)s AND slot <= %(until)s
            ), seed AS (
              -- ② 範圍外的前值。每站一次，與格數無關。
              --    avail 與 docks 各查各的 —— 兩者的洞不完全重合
              --    （有 132 格是 avail 有值而 docks 為 NULL），共用會錯位。
              SELECT s.station_uid, pa.avail AS avail0, pd.docks AS docks0
                FROM (SELECT DISTINCT station_uid FROM tgt) s
                LEFT JOIN LATERAL (
                  SELECT b.avail FROM baseline_grid b
                   WHERE b.station_uid = s.station_uid
                     AND b.slot <= %(lo)s AND b.slot >= %(floor)s
                     AND b.avail IS NOT NULL
                   ORDER BY b.slot DESC LIMIT 1) pa ON true
                LEFT JOIN LATERAL (
                  SELECT b.docks FROM baseline_grid b
                   WHERE b.station_uid = s.station_uid
                     AND b.slot <= %(lo)s AND b.slot >= %(floor)s
                     AND b.docks IS NOT NULL
                   ORDER BY b.slot DESC LIMIT 1) pd ON true
            ), g AS (
              -- ① island 編號。⚠ avail 與 docks 必須各自編號；且 PG 不准
              --   把 window function 巢狀寫進 WINDOW 定義，所以要拆兩層。
              SELECT station_uid, slot, avail, docks, is_observed,
                     count(avail) OVER pw AS grp_a,
                     count(docks) OVER pw AS grp_d
                FROM tgt
              WINDOW pw AS (PARTITION BY station_uid ORDER BY slot
                            ROWS UNBOUNDED PRECEDING)
            ), c AS (
              SELECT station_uid, slot, is_observed, avail AS raw,
                     first_value(avail) OVER (PARTITION BY station_uid, grp_a
                                              ORDER BY slot) AS avail_c,
                     first_value(docks) OVER (PARTITION BY station_uid, grp_d
                                              ORDER BY slot) AS docks_c
                FROM g
            )
            INSERT INTO hackathon_backend_level30
                   (station_uid, slot, avail, docks, is_observed, is_imputed)
            SELECT c.station_uid, c.slot,
                   COALESCE(c.avail_c, seed.avail0),
                   COALESCE(c.docks_c, seed.docks0),
                   c.is_observed,
                   CASE WHEN c.raw IS NULL THEN 2 ELSE 0 END::smallint
              FROM c JOIN seed USING (station_uid)
             -- 該站連 seed 都沒有 = 這格之前它從未回報過。寫進去也是 NULL，
             -- 不如不寫（同 43 的規則 1「首值之前不寫列」）。
             WHERE COALESCE(c.avail_c, seed.avail0) IS NOT NULL
            ON CONFLICT (station_uid, slot) DO UPDATE SET
                   avail = EXCLUDED.avail,
                   docks = EXCLUDED.docks,
                   is_observed = EXCLUDED.is_observed,
                   -- ★ 不能寫死 0。carry 出來的格標成 0 就等於謊稱實測，
                   --   而且 43 灌的 is_imputed=2 會被回放悄悄洗成 0。
                   is_imputed = EXCLUDED.is_imputed
            """, {"lo": lo, "until": until,
                  "floor": config.DEMO_PRELOAD_FROM})
        n_total = cur.rowcount
        # ★ 數「有值的列」不是「is_observed=1 的列」—— baseline_grid 的
        #   填補列（carry-forward，is_observed=0）也是有效水位，
        #   與 Job A「寫入即計數」的語意對齊。只數實測會把覆蓋率
        #   砍到 5 成以下，每一格都誤判 skipped。
        #
        # ⚠ 2026-09-02 起這個數字的意義變了：NULL 格改成 carry 之後，
        #   它等於「當時在營運的站數」，覆蓋率門檻於是**永遠會過**，
        #   job_run 不再出現 skipped。這是無限 carry 的必然結果不是 bug
        #   （計劃 §4）。門檻留著是為了抓「整片沒搬進來」這種真故障。
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
        else:
            # sys_config.replay_predict = 0：重播既有預測，不打 endpoint。
            # 一定要印 —— 否則 log 上看不出這一格「為什麼沒有預測」
            print(f"── 跳過 Job B（replay_predict=0，沿用既有 origin={slot} 的預測）")
        return True, f"stations_ok={n_grid}"

    except Exception as e:
        job_run_repo.finish(run_id, "failed", error=f"{type(e).__name__}: {e}")
        raise
