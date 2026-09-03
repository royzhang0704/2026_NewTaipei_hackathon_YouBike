# ════════════════════════════════════════════════════════════
# jobs/predict_range.py —— 把一整段 origin 逐輪丟給 endpoint（Job B 的外層迴圈）
#
# 用途：level30 已經有歷史資料（sql/43_level30_carry.sql 灌的 04-01~08-01），
# 要對某一整天／某一段時間「每一格 origin × 全站」補出預測與風險判定時用。
# 不是排程用的 —— cron 走 tick.py，這支是人手動補歷史區的驅動器。
#
# ★ 零新預測邏輯：每一輪原封不動呼叫 batch_predict.run()。
#   組 payload、冪等、主檔 forecast_run、風險判定、job_run 稽核全部沿用，
#   這裡只負責「決定要跑哪些 origin、照順序跑、把錢花在明處」。
#
# ★ origin 必須由舊到新：risk_service 的 streak 靠上一輪遞推
#   （batch_predict.write_risk 讀 risk_repo.prev(origin - 30m)），
#   跳著跑會把連續性算錯。這支只走 ascending，不提供反序。
#
# ★ --dry-run 不走 batch_predict.run()，只走它的 collect()。
#   理由（9/3 查證）：batch_predict.py 的 --dry-run 沒有實作「不打 endpoint」——
#   全檔只有 :292（跳過 job_run）與 :314（繞過冪等）兩處用到 dry_run，
#   predict() 與 upsert() 都沒有守衛。帶著它跑會照樣 invoke、照樣寫表，
#   而且繞過冪等後打得比不帶更多。那支是 tick/Job A 共用的線上路徑，
#   要修得先立計劃，所以這裡不呼叫它、自己接 collect() 就停。
#
# ★ forecast_end 會被還原：batch_predict.run() 每輪都會把 sys_config 的
#   forecast_end 設成該 origin + 3 小時。補 5 月的歷史時那是往回寫，
#   會讓 /healthz 的 forecast_left_min 變成大負數。迴圈結束（含中斷、
#   例外）一律把原值放回去。--dry-run 不會動它。
#
# ⚠ 真 endpoint 按秒計費（ml.m5.large）。48 個 origin × ~1,527 站 ÷ 50
#   ≈ 1,500 次 invoke。先 --dry-run 看清楚要打幾次再放手。
#
# 用法：
#   uv run python -m jobs.predict_range --from '2026-05-01' --to '2026-05-02' --dry-run
#   uv run python -m jobs.predict_range --from '2026-05-01 00:00' --limit 50   # 真打，每輪只 50 站
#   uv run python -m jobs.predict_range --from '2026-05-01' --to '2026-05-02' --force
# ════════════════════════════════════════════════════════════
import argparse
import sys
import time
from datetime import datetime, timedelta

from app import config
from app.repository import sys_config_repo
from app.repository.db import get_conn
from jobs import batch_predict


def origins(start: datetime, end: datetime) -> list[datetime]:
    """[start, end) 之間、對齊 FREQ_MIN 的 origin 清單（由舊到新）。"""
    step = timedelta(minutes=config.FREQ_MIN)
    if start.minute % config.FREQ_MIN or start.second or start.microsecond:
        raise SystemExit(f"✗ --from {start} 沒有對齊 {config.FREQ_MIN} 分格線")
    out, t = [], start
    while t < end:
        out.append(t)
        t += step
    if not out:
        raise SystemExit(f"✗ 區間是空的（{start} ~ {end}）")
    return out


def coverage(slots: list[datetime]) -> dict:
    """實查 level30 這段區間有沒有資料 —— 沒有的話 build_payload 會逐站丟
    INSUFFICIENT_HISTORY，整輪 0 站可打，白跑一趟。

    ★ 查的是 anchor 那一格本身；context 的 48 格由 build_payload 自己驗
      （h["first_slot"] > h["start"] 就擋）。
    """
    with get_conn().cursor() as cur:
        cur.execute("""
            SELECT slot, count(*) AS n
              FROM hackathon_backend_level30
             WHERE slot >= %s AND slot <= %s
             GROUP BY slot
        """, (slots[0], slots[-1]))
        have = {r["slot"]: r["n"] for r in cur.fetchall()}
    return {"have": have, "empty": [s for s in slots if not have.get(s)]}


def dry_one(slot: datetime, limit: int | None, batch: int) -> str:
    """--dry-run 的單輪：只到 collect() 為止，一次 invoke 都不發、一列都不寫。

    ★ 用的是 batch_predict 自己的 eligible_stations() 與 collect()，
      所以「可打幾站、被什麼理由跳過」與真跑逐字相同 —— 這是驗證的重點，
      dry-run 若走第二份篩選邏輯就驗不到真跑會發生什麼事。
    """
    stations = batch_predict.eligible_stations()
    if limit:
        stations = stations[:limit]
    items, stats, skips = batch_predict.collect(stations, slot)
    n_inv = -(-len(items) // batch)
    return (f"對象 {len(stations)} 站 → 可打 {len(items)}"
            f"（跳過 {len(skips)}：{stats}）｜將發 {n_inv} 次 invoke")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Job B 的外層迴圈：一段 origin 逐輪批打（歷史區補預測用）")
    ap.add_argument("--from", dest="frm", required=True,
                    help="起點 origin（含）；ISO，例 '2026-05-01' 或 '2026-05-01 00:00'")
    ap.add_argument("--to", dest="to", default=None,
                    help="終點（不含）；預設 = 起點 + 1 天")
    ap.add_argument("--batch", type=int, default=config.PREDICT_BATCH_SIZE)
    ap.add_argument("--limit", type=int, default=None,
                    help="每輪只打前 N 站（驗證用；正式跑不要帶）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只組 payload 不打 endpoint、不寫任何表（走 collect() 就停，"
                         "不是 batch_predict 的同名旗標 —— 那支的沒有實作，見檔頭）")
    ap.add_argument("--force", action="store_true",
                    help="無視站層冪等重打（換模型／換資料口徑時要帶）")
    ap.add_argument("--keep-going", action="store_true",
                    help="單輪例外不中止，記下來繼續（預設是停 —— "
                         "系統性錯誤時不該繼續燒 endpoint）")
    a = ap.parse_args()

    start = datetime.fromisoformat(a.frm)
    end = datetime.fromisoformat(a.to) if a.to else start + timedelta(days=1)
    slots = origins(start, end)

    cov = coverage(slots)
    print(f"── {len(slots)} 個 origin：{slots[0]} ~ {slots[-1]}")
    print(f"   endpoint={config.ENDPOINT_NAME}｜mock={config.MOCK}"
          f"｜batch={a.batch}｜force={a.force}｜algo={config.RISK_ALGO_VER}")
    n_st = max(cov["have"].values()) if cov["have"] else 0
    print(f"   level30 有資料的 origin {len(cov['have'])}/{len(slots)}"
          f"（最多 {n_st} 站/格）"
          + (f"　⚠ 空的 {len(cov['empty'])} 格：{cov['empty'][:3]}…"
             if cov["empty"] else ""))
    est = len(slots) * -(-min(n_st, a.limit or n_st) // a.batch)
    print(f"   預估 invoke 次數 ≈ {est}"
          + ("（--dry-run：一次都不會真的發）" if a.dry_run else "（真 endpoint 按秒計費）"))
    if cov["empty"] and not a.dry_run:
        print("   ⚠ 空格會整輪 0 站可打；要先補 level30 就現在中斷（Ctrl-C）")

    # ★ 補歷史會把 forecast_end 往回寫，跑完要放回去（見檔頭）
    saved_end = None if a.dry_run else sys_config_repo.get(sys_config_repo.K_FORECAST_END)

    t0, done, failed = time.time(), [], []
    try:
        for i, slot in enumerate(slots, 1):
            el = time.time() - t0
            eta = f"｜ETA {(el / (i - 1) * (len(slots) - i + 1)) / 60:.0f}m" if i > 1 else ""
            print(f"\n[{i}/{len(slots)}] origin {slot}（已花 {el / 60:.1f}m{eta}）")
            try:
                if a.dry_run:
                    msg = dry_one(slot, a.limit, a.batch)
                else:
                    msg = batch_predict.run(slot, a.batch, a.limit, False, a.force)
                print(f"   {msg}")
                done.append(slot)
            except Exception as e:                        # noqa: BLE001
                failed.append((slot, f"{type(e).__name__}: {e}"))
                print(f"   ✗ {failed[-1][1]}", file=sys.stderr)
                if not a.keep_going:
                    print("   中止（要跳過失敗繼續請帶 --keep-going）", file=sys.stderr)
                    break
    except KeyboardInterrupt:
        print("\n⚠ 手動中斷 —— 已完成的 origin 都已落表，重跑時冪等會跳過")
    finally:
        if saved_end is not None:
            sys_config_repo.set(sys_config_repo.K_FORECAST_END, saved_end)
            print(f"\n   forecast_end 已還原為 {saved_end}")

    print(f"\n✓ 成功 {len(done)}／失敗 {len(failed)}／共 {len(slots)} 個 origin，"
          f"耗時 {(time.time() - t0) / 60:.1f} 分")
    for s, e in failed:
        print(f"   ✗ {s}  {e}")

    if not a.dry_run and done:
        with get_conn().cursor() as cur:
            cur.execute("""
                SELECT count(DISTINCT origin) AS origin數, count(*) AS 列數,
                       count(DISTINCT station_uid) AS 站數,
                       count(*) FILTER (WHERE NOT (q19 <= q50 AND q50 <= q90)) AS 分位數交叉
                  FROM hackathon_backend_forecast_history
                 WHERE origin >= %s AND origin <= %s""", (slots[0], slots[-1]))
            r = cur.fetchone()
            cur.execute("""
                SELECT count(DISTINCT origin) AS origin數, count(*) AS 列數
                  FROM hackathon_backend_risk_snapshot
                 WHERE origin >= %s AND origin <= %s""", (slots[0], slots[-1]))
            k = cur.fetchone()
        print(f"  forecast_history：{r['origin數']} origin / {r['站數']} 站 / "
              f"{r['列數']} 列｜q19≤q50≤q90 交叉 {r['分位數交叉']} 列")
        print(f"  risk_snapshot   ：{k['origin數']} origin / {k['列數']} 列")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
