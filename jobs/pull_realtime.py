# ════════════════════════════════════════════════════════════
# jobs/pull_realtime.py —— Job A：每 30 分拉 TDX 即時水位（cron :01 / :31）
#
# 對應：計劃-TDX排程與初始化.md §3「Job A」五步
#   ① token 快取（client 內建）
#   ② GET Availability（gzip / $select 五欄 / $top=10000 / 429 退避）
#   ③ 逐站 upsert actual_history + level30（同一交易）
#   ④ 寫入站數 ≥ 主檔 × 80% → 同程序觸發 Job B；否則記 skipped 不觸發
#   ⑤ 快照裡主檔沒有的新站 → insert 主檔 cat=NULL，記 log
#
# ★ 兩張表刻意不一致（這是設計，不是 bug）：
#   actual_history 照寫每一站的快照（審計證據）；
#   level30 只寫「新鮮」的站 —— SrcUpdateTime 距 slot 超過 3 小時
#   （= carry-forward 上限 6 格）就不寫列，讓它在 level30 是缺格 NULL。
#   站台斷訊時 TDX 仍回舊值，照寫會變成一條假的水平線餵進模型。
#
# ★ 不加 ServiceStatus 過濾：重採樣的守門規則只看「時間可解析 + 兩個數值欄
#   是純數字」（計劃 §3 規則 0）。這裡自己多加一道 = 換了輸入分佈，
#   違反硬性約束 1。service_status 只記進 actual_history 供事後查。
#
# 用法：
#   uv run python -m jobs.pull_realtime --dry-run    # 拉了不寫，印統計
#   uv run python -m jobs.pull_realtime              # 正式一輪（會觸發 Job B）
#   uv run python -m jobs.pull_realtime --no-trigger # 只拉不預測
#   uv run python -m jobs.pull_realtime --top 3      # 開發期小量測試
# ════════════════════════════════════════════════════════════
import argparse
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import config
from app.repository import hist_repo, job_run_repo, sys_config_repo
from app.repository.db import get_conn
from app.tdx import client

JOB_NAME = "pull_realtime"
TZ = ZoneInfo(config.TZ_TAIPEI)


def floor_slot(now: datetime | None = None) -> datetime:
    """台北時間 floor 至 :00/:30，回 naive timestamp（level30 既有慣例）。

    ★ 一律先轉台北再 floor。cron 的 TZ 未必等於開發機，
      用系統本地時間算出來的 slot 會整批偏移，而且不會報錯。
    """
    # ★ 預設值走 sys_config.effective_now()，virtual_now 設了就跟著虛擬時間走。
    #   不要在這裡直接 datetime.now() —— 那會讓排程與 API 活在不同時間。
    t = now or sys_config_repo.effective_now()
    t = t.astimezone(TZ) if t.tzinfo else t.replace(tzinfo=TZ)
    return t.replace(minute=t.minute // 30 * 30, second=0,
                     microsecond=0, tzinfo=None)


def parse_rows(raw: list, slot: datetime) -> tuple[list, dict]:
    """TDX 列 → 寫入用的 dict，並判定新鮮度。

    回傳 (records, stats)。每筆 record 帶 fresh 旗標：
    fresh=False 的只寫 actual_history，不寫 level30。
    """
    slot_aware = slot.replace(tzinfo=TZ)
    limit = timedelta(hours=config.STALE_MAX_HOURS)
    recs, stats = [], {"stale": 0, "no_avail": 0, "bad_time": 0, "no_uid": 0}

    for r in raw:
        uid = r.get("StationUID")
        if not uid:
            stats["no_uid"] += 1
            continue

        src = r.get("SrcUpdateTime")
        age = None
        if src:
            try:
                # ★ SrcUpdateTime 帶 +08:00，fromisoformat 解出來是 aware，
                #   要和 aware 的 slot 比 —— 混用 naive 會 TypeError（好事，
                #   比默默算錯 8 小時好）
                age = slot_aware - datetime.fromisoformat(src)
            except ValueError:
                stats["bad_time"] += 1
                src = None

        avail = r.get("AvailableRentBikes")
        if avail is None:
            stats["no_avail"] += 1

        # 新鮮 = 有 avail + 有可解析的來源時間 + 沒過期
        # （src 缺失時無法判斷新鮮度，保守當成不新鮮）
        fresh = avail is not None and age is not None and age <= limit
        if avail is not None and age is not None and age > limit:
            stats["stale"] += 1

        recs.append({
            "station_uid": uid,
            "slot": slot,
            "avail": avail,
            "return_slots": r.get("AvailableReturnBikes"),
            "service_status": r.get("ServiceStatus"),
            "src_update_time": src,
            "fresh": fresh,
        })
    return recs, stats


def insert_new_stations(recs: list) -> list[str]:
    """快照裡主檔沒有的站 → insert 主檔 cat=NULL（步驟 ⑤）。

    只填 station_uid / town_code / last_seen —— 站名座標容量要打
    /v2/Bike/Station 才有，那是 sync_stations 的事，這裡不多打一支 API。
    ★ 必須先做：level30 的 docks 要 join 主檔 capacity，
      主檔沒這站就寫不進 level30。
    """
    with get_conn().cursor() as cur:
        cur.execute("SELECT station_uid FROM hackathon_backend_station")
        known = {r["station_uid"] for r in cur.fetchall()}
        new = [r["station_uid"] for r in recs if r["station_uid"] not in known]
        if new:
            cur.executemany(
                "INSERT INTO hackathon_backend_station "
                "       (station_uid, town_code, last_seen, cat) "
                "VALUES (%s, %s, current_date, NULL) "
                "ON CONFLICT (station_uid) DO NOTHING",
                [(uid, uid[7:9] if len(uid) >= 9 else None) for uid in new])
    return new


def write(recs: list) -> tuple[int, int]:
    """同一交易寫 actual_history + level30，回 (快照列數, level30 列數)。

    ★ 同交易的理由：level30 是模型的輸入，actual_history 是它的證據。
      只寫成一半 = 有預測卻查不到依據，或反過來。
    """
    fresh = [r for r in recs if r["fresh"]]
    with get_conn().transaction(), get_conn().cursor() as cur:
        cur.executemany(
            """
            INSERT INTO hackathon_backend_actual_history
                   (station_uid, slot, avail, return_slots,
                    service_status, src_update_time)
            VALUES (%(station_uid)s, %(slot)s, %(avail)s, %(return_slots)s,
                    %(service_status)s, %(src_update_time)s)
            ON CONFLICT (station_uid, slot) DO UPDATE SET
                   avail = EXCLUDED.avail,
                   return_slots = EXCLUDED.return_slots,
                   service_status = EXCLUDED.service_status,
                   src_update_time = EXCLUDED.src_update_time,
                   fetched_at = now()
            """, recs)
        n_snap = cur.rowcount

        # docks 取主檔 capacity（重採樣規則第 4 條：docks 按日 join 容量，
        # 不做 carry-forward）。is_observed=1 = 這格是實測不是填補值。
        cur.executemany(
            """
            INSERT INTO hackathon_backend_level30
                   (station_uid, slot, avail, docks, is_observed)
            SELECT %(station_uid)s, %(slot)s, %(avail)s, s.capacity, 1
              FROM hackathon_backend_station s
             WHERE s.station_uid = %(station_uid)s
            ON CONFLICT (station_uid, slot) DO UPDATE SET
                   avail = EXCLUDED.avail,
                   docks = EXCLUDED.docks,
                   is_observed = 1,
                   is_imputed = 0     -- 實測進來就蓋掉先前的週期補值
            """, fresh)
        n_grid = cur.rowcount
    return n_snap, n_grid


def master_count() -> int:
    with get_conn().cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM hackathon_backend_station")
        return cur.fetchone()["n"]


def trigger_job_b(slot: datetime) -> str:
    """同程序呼叫 Job B（不是 DB trigger —— 失敗語意清楚、無跨程序時序問題）。

    回傳一句話結果，記進 Job A 的 detail。Job B 自己會記一列 job_run，
    所以這裡吞掉例外不會讓失敗消失。
    """
    try:
        from jobs import batch_predict
    except ImportError:
        return "batch_predict 尚未實作（T5），略過"
    try:
        return batch_predict.run(slot)
    except Exception as e:                      # Job B 的失敗不該蓋掉 Job A 的成功
        return f"Job B 失敗（已記在它自己那列）：{type(e).__name__}: {e}"


def run(slot: datetime, top: int = config.TDX_TOP_ALL,
        trigger: bool = True, dry_run: bool = False) -> tuple[bool, str]:
    """跑一輪 Job A。回傳 (是否成功, 一句話摘要)。

    抽成獨立函式的理由：jobs/tick.py（每分鐘輪詢）要直接呼叫它，
    不該去 subprocess 自己一次 —— 同 process 才拿得到成功與否，
    也才能接著同程序觸發 Job B。
    """
    # ★ demo 斷路的第二道防線（8/31，與 backfill.run 同理）
    if sys_config_repo.is_demo():
        raise RuntimeError("demo 回放模式生效中，Job A（TDX 即時 API）停用，"
                           "回放走 jobs/replay_pull.py")
    run_id = None if dry_run else job_run_repo.start(JOB_NAME, slot)
    try:
        raw, bytes_in = client.get(config.TDX_PATH_AVAILABILITY,
                                   {"$select": config.TDX_SELECT_AVAILABILITY,
                                    "$top": top})
        recs, stats = parse_rows(raw, slot)
        n_fresh = sum(r["fresh"] for r in recs)
        total = master_count()
        need = int(total * config.JOB_A_MIN_COVERAGE)

        print(f"   {client.LAST_ENCODING}")
        print(f"   TDX {len(raw)} 列 → 可用 {len(recs)}｜新鮮 {n_fresh}"
              f"（過期 {stats['stale']}／無 avail {stats['no_avail']}"
              f"／時間壞 {stats['bad_time']}／無 UID {stats['no_uid']}）")
        print(f"   主檔 {total} 站，門檻 {config.JOB_A_MIN_COVERAGE:.0%} = {need} 站")

        if dry_run:
            ok = n_fresh >= need
            return ok, (f"--dry-run：判定會是 "
                        f"{'success → 觸發 Job B' if ok else 'skipped → 不觸發'}")

        new_uids = insert_new_stations(recs)
        if new_uids:
            print(f"   ＋主檔新增 {len(new_uids)} 站（cat=NULL）：{new_uids[:5]}"
                  f"{' …' if len(new_uids) > 5 else ''}")
            print("     ⚠ 這些站還沒有 proxy，跑 sync_stations 補齊站名座標與代理")

        n_snap, n_grid = write(recs)
        print(f"── 寫入 actual_history {n_snap} 列｜level30 {n_grid} 列")

        # ── 週期補值（計劃-排程自癒 §2；8/28 定案值落表）──
        #   當日缺格歷史 API 補不到（每日 08:00 才更新至昨日），
        #   先用一週前同 slot 的真值把 48 格 context 填起來，標 is_imputed=1。
        #   ★ 放在寫入之後：先有這一輪的真值，補值的骨架才長得到現在。
        #   ★ 真值一律優先 —— 上面的 upsert 會把 is_imputed 蓋回 0，
        #     明天 Job C 補到真值時也一樣（hist_repo._fill_level30）。
        imp = hist_repo.impute_weekly(
            slot.date() - timedelta(days=config.IMPUTE_WINDOW_DAYS - 1), slot.date())
        print(f"── 週期補值 {imp['filled']} 格（取 {config.IMPUTE_LOOKBACK_DAYS} 天前同 slot，"
              f"涉及 {imp['stations']} 站）")

        detail = {"fetched": len(raw), "fresh": n_fresh,
                  "new_stations": len(new_uids), "imputed": imp["filled"], **stats}
        if sys_config_repo.is_virtual():
            detail["virtual_now"] = str(sys_config_repo.effective_now())

        # ── 步驟 ④　80% 判定 ──
        if n_grid < need:
            msg = (f"覆蓋率不足：level30 寫入 {n_grid} < 主檔 {total} × "
                   f"{config.JOB_A_MIN_COVERAGE:.0%} = {need}，不觸發 Job B")
            print(f"   ✗ {msg}")
            job_run_repo.finish(run_id, "skipped", stations_ok=n_grid,
                                rows_written=n_snap + n_grid, bytes_in=bytes_in,
                                error=msg, detail=detail)
            return False, msg

        # Job A 先結案再觸發 Job B —— 兩件事各記一列，互不遮蔽
        job_run_repo.finish(run_id, "success", stations_ok=n_grid,
                            rows_written=n_snap + n_grid, bytes_in=bytes_in,
                            detail=detail)
        # ★ 書籤：每分鐘輪詢那支就是拿它跟 floor(now,30min) 比
        sys_config_repo.set(sys_config_repo.K_CURRENT_SLOT, slot)
        print(f"✓ Job A success（stations_ok={n_grid}, bytes_in={bytes_in:,}）")

        if trigger:
            print(f"── 觸發 Job B（origin={slot}）")
            print(f"   {trigger_job_b(slot)}")
        else:
            print("── 不觸發 Job B")
        return True, f"stations_ok={n_grid}, bytes_in={bytes_in:,}"

    except Exception as e:
        if run_id:
            job_run_repo.finish(run_id, "failed", error=f"{type(e).__name__}: {e}")
        raise


def main() -> int:
    # ★ demo 回放斷路（8/31）：回放期間不打 TDX、不寫真值表 ——
    #   tick 已分流到 Job A′，這道擋的是「有人手動跑這支」
    if sys_config_repo.is_demo():
        print("✗ demo 回放模式生效中，TDX job 停用"
              "（uv run python -m jobs.demo --stop 後恢復）", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(description="Job A：拉 TDX 即時水位")
    ap.add_argument("--dry-run", action="store_true",
                    help="拉了不寫 DB、不記 job_run、不觸發 Job B")
    ap.add_argument("--no-trigger", action="store_true", help="只拉不預測")
    ap.add_argument("--top", type=int, default=config.TDX_TOP_ALL,
                    help=f"$top（預設 {config.TDX_TOP_ALL}；開發期測試用 3）")
    ap.add_argument("--slot", default=None,
                    help="覆寫 slot（ISO 格式，補跑用；預設 floor(有效now,30min)）")
    a = ap.parse_args()

    slot = datetime.fromisoformat(a.slot) if a.slot else floor_slot()
    print(f"── slot {slot}（台北時間）"
          + ("  ⚠ 虛擬時間生效中" if sys_config_repo.is_virtual() else ""))

    for st in job_run_repo.stale_running(JOB_NAME):
        print(f"   ⚠ 上輪中斷殘列 id={st['id']} slot={st['slot']} "
              f"started_at={st['started_at']}")

    try:
        ok, _ = run(slot, a.top, trigger=not a.no_trigger, dry_run=a.dry_run)
    except Exception as e:
        print(f"✗ {type(e).__name__}: {e}", file=sys.stderr)
        raise
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
