# ════════════════════════════════════════════════════════════
# jobs/backfill.py —— Job C：歷史 API 回補 level30 的洞（自癒）
#
# 對應：meet/20260828/計劃-排程自癒與level30滾動視窗.md §1（T7 改版規格）
#
# ★ 8/28 改版：從「cron 每日 08:30 定時」改成「tick 每分鐘判定後觸發」。
#   理由是機器不會 24 小時開機 —— 固定 08:30 那一分鐘沒開機，
#   洞就要再等 24 小時。cron 不會替你補跑錯過的排程。
#   筆電闔上三天，開機後下一分鐘 tick 就把三天的洞一次補回。
#
# ══ 兩個繞不過的資料源限制（所有設計的前提，計劃 §0）══
#   1. 即時 API 只回「當下」—— Job A 在 6:00 跑一百次也拿不到 5:00 的值。
#      補洞唯一來源是歷史 API。
#   2. 歷史 API 每日 08:00 才更新至「昨日」。
#      → 當日缺格當天補不到，最快隔天 08:00 後自癒。
#      既定規則「缺格 NULL 不擋預測」承接這段空窗：品質打折但服務不斷。
#
# ══ 四道判定（由便宜到貴，順序本身就是效能設計）══
#   a. 今日尚未有 job_run(backfill, success)      ← 查 job_run，最便宜
#   b. 退避：今日最後一次 failed 距今 ≥ 1 小時
#      （a/b 的「今日／距今」一律看**真實**台北時間，不看 virtual_now，
#        理由見 due() 的 docstring —— 節流是真實世界的事）
#   c. 有效now（台北）≥ 08:00                      ← 歷史 API 更新時間
#   d. 視窗內（昨日往前 10 天）有單日缺格率 > 10%   ← 要掃 level30，最貴
#
#   ★ a~c 在 due() 裡，tick 每分鐘只跑這三道 —— 絕大多數輪次查一筆
#     job_run 就結束，不碰 level30。
#   ★ d 刻意留在 run() 裡而不在 due()：判定 d 不成立時要留下一列
#     job_run 'skipped'（T7 原驗收 3），寫在 due() 就沒有那列了。
#     代價是 d 不成立那天會多跑一次缺格掃描（實測 9 秒），一天一次可接受。
#
# 用法：
#   uv run python -m jobs.backfill --status      # 只印判定與缺格率
#   uv run python -m jobs.backfill --dry-run     # 印判定/日期區間/URL，不真打
#   uv run python -m jobs.backfill               # 真跑（★ 會打歷史 API 扣點）
#   uv run python -m jobs.backfill --force       # 跳過 a~c，仍受 d 與護欄約束
#   uv run python -m jobs.backfill --days 3      # 縮小視窗（省點數的手動補洞）
# ════════════════════════════════════════════════════════════
import argparse
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import config
from app.repository import hist_repo, job_run_repo, sys_config_repo
from app.tdx import client

JOB_NAME = "backfill"
_TZ = ZoneInfo(config.TZ_TAIPEI)


# ════════════════════════════════════════════════════════════
# 判定
# ════════════════════════════════════════════════════════════
def due(now: datetime | None = None) -> tuple[bool, str]:
    """判定 a~c（便宜的三道，只查 job_run）。回 (要不要跑, 一句話原因)。

    tick 每分鐘呼叫這支。三道都成立才會去叫 run()——
    run() 裡還有判定 d（缺格率）與用量護欄兩關。

    ★★ 時間基準刻意分成兩種，這是 virtual_now 的坑，寫錯不會報錯：
        a / b（節流）用**真實台北時間** —— 它們問的是「這台機器今天
              實際打過幾次歷史 API、上次失敗是多久以前」。job_run.started_at
              本來就是真實時間戳，拿虛擬時間去比會查不到今天的列，
              「一天只補一次」的短路就安靜失效，變成每分鐘打一次 API。
        c / 視窗用**有效now** —— 它們問的是「要補哪幾天的資料」，
              那是資料時間，demo 設 virtual_now 就該跟著走（計劃 §1 ②c）。
    """
    now = now or sys_config_repo.effective_now()
    # ★ 節流一律看真實時間，理由見上面的 docstring
    today = datetime.now(_TZ).date()
    runs = job_run_repo.runs_on_day(JOB_NAME, today)

    # a. 今日已補過就不再補（成立率最高，先短路）
    if any(r["status"] == "success" for r in runs):
        return False, f"a：今日（{today}，真實日期）已有 backfill success"

    # b. 失敗退避 —— 沒有這道，PG 掛掉時會每分鐘重試並每次都打一次歷史 API
    fails = [r for r in runs if r["status"] == "failed"]
    if fails:
        # runs_on_day 已按 started_at DESC 排序，取第一筆就是最後一次失敗
        last_fail = fails[0]["started_at"]
        # started_at 帶時區，now 是台北 naive —— 轉齊了再比，混用會 TypeError
        age = datetime.now(last_fail.tzinfo) - last_fail
        wait = timedelta(minutes=config.BACKFILL_RETRY_AFTER_MIN)
        if age < wait:
            return False, (f"b：今日最後一次 failed 在 {age.total_seconds() / 60:.0f} 分鐘前，"
                           f"未滿 {config.BACKFILL_RETRY_AFTER_MIN} 分鐘退避")

    # c. 歷史 API 每日 08:00 才更新至昨日，早於這個時間拉是白花點數
    if now.hour < config.TDX_HIST_READY_HOUR:
        return False, (f"c：現在 {now:%H:%M} 早於 {config.TDX_HIST_READY_HOUR:02d}:00，"
                       f"歷史 API 還沒更新到昨日")

    return True, f"a~c 全過（今日 {today} 尚未補、已過 {config.TDX_HIST_READY_HOUR:02d}:00）"


def window(now: datetime | None = None,
           days: int = config.BACKFILL_WINDOW_DAYS) -> tuple[date, date]:
    """回補視窗 [d0, d1]，含頭含尾。

    ★ d1 = 昨日，不含今日 —— 歷史 API 今天拿不到今天（限制 2）。
    ★ 視窗長度預設 10 天，與 seed 對齊（計劃 §2）：context 最舊格
      是「當下 −24h」，週期補值要再回看 7 天找上週值，最舊需求 ≈ 8 天前，
      7 天會讓視窗邊緣查不到，10 天留 2 天餘裕。
    ★ 視窗也是 retention 的內側 —— 不去補已被 retention 刪掉的區間，
      否則補了又刪、白花點數（計劃 §4）。
    """
    now = now or sys_config_repo.effective_now()
    d1 = now.date() - timedelta(days=1)
    return d1 - timedelta(days=days - 1), d1


def chunk_dates(days: list[date],
                max_days: int = config.TDX_HIST_MAX_DAYS) -> list[tuple[date, date]]:
    """把要補的日期切成幾刀 Dates 區間（連續才合刀，一刀最多 max_days 天）。

    ★ 只拉真的有缺的日子 —— 直接拿 min~max 一次拉會把中間補好的日子
      也重拉一遍。歷史服務 20 MB/1 點，單日未壓縮約 30 MB，
      多拉一天就是多 1.5 點（gzip 後約 0.15 點）。
    ★ 一刀最多 7 天是 API 限制（swagger 查證）；給 8 天不會報錯，
      只會安靜地少回幾天 —— 那種錯最難查，所以在這裡就切好。
    """
    out: list[tuple[date, date]] = []
    for d in sorted(days):
        if out and d == out[-1][1] + timedelta(days=1) \
                and (out[-1][1] - out[-1][0]).days + 1 < max_days:
            out[-1] = (out[-1][0], d)          # 接得上且還沒滿刀 → 併進去
        else:
            out.append((d, d))
    return out


def _dates_param(d0: date, d1: date) -> str:
    """歷史 API 的 Dates 參數：單日給日期本身，多日給 起~迄。"""
    return f"{d0}" if d0 == d1 else f"{d0}~{d1}"


def _guard() -> tuple[bool, str]:
    """用量護欄：本月估算點數超過上限就自停 Job C，只保 Job A/B。

    對應計劃-TDX排程與初始化.md §3「用量護欄」（105% 停權規則）。
    ★ 停的只有 Job C —— 常態的 Job A/B 一個月才 1~2 點，
      為了護欄把它們一起停掉，等於用「沒資料」換「沒超點」。
    """
    u = job_run_repo.month_usage()
    if u["points"] > config.BACKFILL_POINT_LIMIT:
        return False, (f"本月估算已用 {u['points']} 點 > 上限 "
                       f"{config.BACKFILL_POINT_LIMIT}，自停 Job C（Job A/B 不受影響）")
    return True, f"本月估算 {u['points']} 點 / 上限 {config.BACKFILL_POINT_LIMIT}"


# ════════════════════════════════════════════════════════════
# 本體
# ════════════════════════════════════════════════════════════
def run(now: datetime | None = None, days: int = config.BACKFILL_WINDOW_DAYS,
        dry_run: bool = False) -> tuple[bool, str]:
    """跑一輪 Job C。回 (是否成功, 一句話摘要)。

    抽成獨立函式的理由與 Job A 相同：tick 要同 process 呼叫它，
    才拿得到成功與否。
    """
    # ★ demo 斷路的第二道防線（8/31）：tick --force 曾繞過 status() 的閘門
    #   真打出 233 MB 歷史 API。斷路放進 run() 本身，呼叫端擋漏也打不出去。
    if sys_config_repo.is_demo():
        raise RuntimeError("demo 回放模式生效中，Job C（TDX 歷史 API）停用")
    now = now or sys_config_repo.effective_now()
    d0, d1 = window(now, days)
    # slot 記「視窗末日 00:00」= 這輪負責到哪一天（計劃 §3：Job C 的 slot = 目標日 00:00）
    slot = datetime.combine(d1, datetime.min.time())

    ok, gmsg = _guard()
    print(f"   用量護欄：{gmsg}")
    if not ok:
        print(f"   ⚠ {gmsg}")
        if not dry_run:
            rid = job_run_repo.start(JOB_NAME, slot)
            job_run_repo.finish(rid, "skipped", error=gmsg)
        return False, gmsg

    # ── 判定 d：缺格率 ──
    rep = hist_repo.gap_report(d0, d1)
    print(f"   視窗 {d0} ~ {d1}（{days} 天）｜現役站 {rep['stations']}"
          f"｜整體缺格率 {rep['gap_rate']:.2%}")
    for d in rep["days"]:
        mark = "←補" if d["d"] in rep["gap_days"] else "   "
        print(f"     {d['d']}  {d['have']:>7,} / {d['expect']:>7,}"
              f"  缺格 {d['gap_rate']:>7.2%} {mark}")

    if not rep["gap_days"]:
        msg = (f"缺格率全部 ≤ {config.BACKFILL_GAP_THRESHOLD:.0%}，不需回補"
               f"（視窗 {d0}~{d1}）")
        print(f"   ✓ {msg}")
        if not dry_run:
            rid = job_run_repo.start(JOB_NAME, slot)
            job_run_repo.finish(rid, "skipped", error=msg,
                                detail={"gap_rate": rep["gap_rate"], "days": days})
        return True, msg

    chunks = chunk_dates(rep["gap_days"])
    url = config.TDX_HIST_BASE + config.TDX_PATH_HIST_AVAIL
    print(f"   要補 {len(rep['gap_days'])} 天，分 {len(chunks)} 刀"
          f"（一刀最多 {config.TDX_HIST_MAX_DAYS} 天）：")
    for c0, c1 in chunks:
        print(f"     {url}?Dates={_dates_param(c0, c1)}"
              f"&$format=CSV&$top={config.TDX_HIST_TOP}")

    if dry_run:
        return True, (f"--dry-run：會打 {len(chunks)} 刀補 {len(rep['gap_days'])} 天"
                      f"（{rep['gap_days'][0]} ~ {rep['gap_days'][-1]}）")

    # ── 真打 ──
    rid = job_run_repo.start(JOB_NAME, slot)
    try:
        total_bytes, rows, detail = 0, 0, {"window": f"{d0}~{d1}",
                                           "gap_rate_before": rep["gap_rate"],
                                           "chunks": []}
        for c0, c1 in chunks:
            print(f"── 拉 {_dates_param(c0, c1)}")
            text, bytes_in = client.get_text(
                config.TDX_PATH_HIST_AVAIL,
                {"Dates": _dates_param(c0, c1), "$format": "CSV",
                 "$top": config.TDX_HIST_TOP},
                base=config.TDX_HIST_BASE)
            total_bytes += bytes_in
            print(f"   {client.LAST_ENCODING}")

            st = hist_repo.stage_csv(text)
            print(f"   staging {st['rows']:,} 列（截斷 {st['short']}）")
            s = hist_repo.ingest(c0, c1)
            rows += s["actual_new"] + s["level30_new"]
            print(f"   守門 {s['gated']:,} 過／{s['dropped']:,} 擋"
                  f"｜格 {s['obs']:,}｜{s['stations']} 站")
            print(f"   actual_history +{s['actual_new']:,}｜level30 +{s['level30_new']:,}")
            detail["chunks"].append({"dates": _dates_param(c0, c1),
                                     "staged": st["rows"], "dropped": s["dropped"],
                                     "actual_new": s["actual_new"],
                                     "level30_new": s["level30_new"],
                                     "bytes_in": bytes_in})

        after = hist_repo.gap_report(d0, d1)
        detail["gap_rate_after"] = after["gap_rate"]
        print(f"── 補完缺格率 {rep['gap_rate']:.2%} → {after['gap_rate']:.2%}")

        job_run_repo.finish(rid, "success", stations_ok=None, rows_written=rows,
                            bytes_in=total_bytes, detail=detail)
        msg = (f"補 {len(rep['gap_days'])} 天／{len(chunks)} 刀，寫入 {rows:,} 列，"
               f"bytes_in={total_bytes:,}，缺格率 {rep['gap_rate']:.2%}"
               f" → {after['gap_rate']:.2%}")
        print(f"✓ Job C success（{msg}）")
        return True, msg

    except Exception as e:
        job_run_repo.finish(rid, "failed", bytes_in=None,
                            error=f"{type(e).__name__}: {e}")
        raise


def main() -> int:
    # ★ demo 回放斷路（8/31）：歷史 API 貴 150 倍，回放期間更不能誤打
    if sys_config_repo.is_demo():
        print("✗ demo 回放模式生效中，TDX job 停用"
              "（uv run python -m jobs.demo --stop 後恢復）", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(description="Job C：歷史 API 回補 level30 的洞")
    ap.add_argument("--status", action="store_true", help="只印判定與缺格率，不做事")
    ap.add_argument("--dry-run", action="store_true",
                    help="印判定、日期區間與 URL，不真打、不寫 DB、不記 job_run")
    ap.add_argument("--force", action="store_true",
                    help="跳過判定 a~c（仍受判定 d 與用量護欄約束）")
    ap.add_argument("--days", type=int, default=config.BACKFILL_WINDOW_DAYS,
                    help=f"視窗天數（預設 {config.BACKFILL_WINDOW_DAYS}）")
    a = ap.parse_args()

    now = sys_config_repo.effective_now()
    d, why = due(now)
    print(f"── 有效 now {now}"
          + ("  ⚠ 虛擬時間生效中" if sys_config_repo.is_virtual() else ""))
    print(f"   判定 a~c：{'觸發' if d else '不觸發'}　{why}")

    if a.status:
        d0, d1 = window(now, a.days)
        rep = hist_repo.gap_report(d0, d1)
        print(f"   用量護欄：{_guard()[1]}")
        print(f"── 視窗 {d0} ~ {d1}｜現役站 {rep['stations']}"
              f"｜整體缺格率 {rep['gap_rate']:.2%}")
        for x in rep["days"]:
            bar = "█" * int(x["gap_rate"] * 30)
            print(f"     {x['d']}  {x['have']:>7,} / {x['expect']:>7,}"
                  f"  {x['gap_rate']:>7.2%} {bar}")
        print(f"   判定 d：{'觸發' if rep['gap_days'] else '不觸發'}"
              f"（{len(rep['gap_days'])} 天超過 {config.BACKFILL_GAP_THRESHOLD:.0%}）")
        return 0

    if not (d or a.force or a.dry_run):
        return 0

    for st in job_run_repo.stale_running(JOB_NAME):
        print(f"   ⚠ 上輪中斷殘列 id={st['id']} slot={st['slot']} "
              f"started_at={st['started_at']}")

    try:
        ok, _ = run(now, a.days, dry_run=a.dry_run)
    except Exception as e:
        print(f"✗ {type(e).__name__}: {e}", file=sys.stderr)
        raise
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
