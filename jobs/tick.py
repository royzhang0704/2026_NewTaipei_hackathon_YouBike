# ════════════════════════════════════════════════════════════
# jobs/tick.py —— 每分鐘輪詢（cron * * * * *），資料落後才補拉
#
# 取代原本「固定 :01 / :31 開跑」的排程。每分鐘做兩條判定：
#
#   ① 當下 slot 落後 → Job A（成功後同程序觸發 Job B）
#     expected = floor(有效now, 30min)        # 這一刻「應該」拉到哪一格
#     latest   = sys_config.current_slot      # 實際拉到哪一格
#     expected > latest  →  跑 Job A
#     否則               →  什麼都不做，安靜離開
#
#   ② backfill 自癒 → Job C（8/28 新增，計劃-排程自癒 §1）
#     backfill.due() 依序查 a 今日未補過／b 失敗退避／c 已過 08:00，
#     三道全過才叫 Job C（缺格率判定 d 在 Job C 自己裡面，
#     因為不成立時要留下一列 job_run 'skipped'）。
#     ★ 兩條判定互不相干，① 跑不跑都要評 ②。
#     ★ 順序是效能設計：a 成立率最高（一天只補一次），
#       絕大多數輪次只查 job_run 一筆就結束，不掃 level30。
#
# 為什麼比固定半點好：
#   - 機器睡著／斷網／PG 沒起來的那幾輪，醒來後「下一分鐘」就補上，
#     不必等到下一個半點（cron 不會替你補跑錯過的排程）
#   - 手動補跑、改時間、重啟都能自己收斂，不需要人記得去補
#
# ★ 補的是「當下這一格」，不是把中間漏掉的每一格都補回來 ——
#   即時 API 只回「現在」的水位，過去的格子它給不了。
#   中間的洞要靠歷史 API 回補（Job C / T8）。
#
# ★ 什麼都不做時「不印任何東西」：cron 每分鐘跑一次，
#   有輸出才寫 log（run_job.sh 負責），否則 log 一天會多 2,880 行廢話。
#   「輪詢還活著嗎」看 sys_config.last_tick，不看 log。
#
# 用法：
#   uv run python -m jobs.tick              # 兩條判定並執行（cron 用這個）
#   uv run python -m jobs.tick --status     # 只印狀態，不做事
#   uv run python -m jobs.tick --force      # 無視判定，強制跑一輪
# ════════════════════════════════════════════════════════════
import argparse
import sys
from datetime import timedelta

from app import config
from app.repository import sys_config_repo as sc
from app.repository.db import get_conn
from jobs import backfill, pull_realtime, replay_pull


def latest_slot():
    """已拉到的最新 slot。以 sys_config 為準，沒有值才回頭問 level30。

    ★ 回頭問 level30 是為了「第一次啟用」與「有人手動清了 sys_config」——
      沒有這道，current_slot 是 NULL 時會判定成落後而重拉一輪
      （不致命，但白花點數）。
    """
    v = sc.get_ts(sc.K_CURRENT_SLOT)
    if v is not None:
        return v
    with get_conn().cursor() as cur:
        cur.execute("SELECT max(slot) AS s FROM hackathon_backend_level30")
        return cur.fetchone()["s"]


def status() -> dict:
    now = sc.effective_now()
    expected = pull_realtime.floor_slot(now)
    latest = latest_slot()
    fe = sc.get_ts(sc.K_FORECAST_END)
    demo = sc.is_demo()
    # 判定②的 a~c。只查 job_run 一小段索引，每分鐘跑得起
    # ★ demo 回放不打 TDX，Job C（歷史回補）整條停用 —— 回放的資料
    #   來自 baseline_grid，缺格就是當年真實的缺格，沒有東西可補
    if demo:
        bf_due, bf_why = False, "demo 回放模式：Job C 停用"
    else:
        bf_due, bf_why = backfill.due(now)
    return {
        "backfill_due": bf_due, "backfill_why": bf_why,
        "now": now, "expected": expected, "latest": latest,
        "behind": None if latest is None else expected - latest,
        "forecast_end": fe,
        "forecast_left": None if fe is None else fe - now,
        "due": latest is None or expected > latest,
        "virtual": sc.is_virtual(),
        "demo": demo,
        "demo_until": sc.get_ts(sc.K_DEMO_UNTIL),
        "ended": sc.demo_ended(),
        "replay_predict": sc.replay_predict(),
        "on": sc.scheduler_on(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="每分鐘輪詢：資料落後才補拉")
    ap.add_argument("--status", action="store_true", help="只印狀態，不做事")
    ap.add_argument("--force", action="store_true", help="無視判定強制跑一輪")
    a = ap.parse_args()

    s = status()

    if a.status:
        warn = ("  ⚠⚠ 靜態虛擬時間生效中（排程會停在這裡）" if s["virtual"]
                else "  ⏸ 已走到 demo_until，時鐘停表中" if s["ended"]
                else f"  ▶ demo 回放中（×{config.DEMO_SPEED:g}）" if s["demo"] else "")
        print(f"有效 now      {s['now']}{warn}")
        print(f"應該拉到      {s['expected']}")
        behind = f"　落後 {s['behind']}" if s["behind"] else ""
        left = f"　還剩 {s['forecast_left']}" if s["forecast_left"] else ""
        print(f"實際拉到      {s['latest'] or '(無)'}{behind}")
        print(f"預測終點      {s['forecast_end'] or '(無)'}{left}")
        if s["demo"]:
            print(f"回放終點      {s['demo_until'] or '(無，一路跑下去)'}")
            print(f"回放觸發 JobB {'是' if s['replay_predict'] else '否（不打 endpoint）'}")
        print(f"排程開關      {'開' if s['on'] else '關'}")
        print(f"判定①拉當下  {'該補拉' if s['due'] else '不用動'}")
        print(f"判定②補歷史  {'該觸發 Job C' if s['backfill_due'] else '不觸發'}"
              f"　{s['backfill_why']}")
        lt = sc.get_ts(sc.K_LAST_TICK)
        print(f"上次輪詢      {lt or '(無)'}")
        return 0

    # ★ 每一輪都寫，包含什麼都沒做的那些 —— 這是「cron 還活著」的唯一證據
    sc.set(sc.K_LAST_TICK, s["now"])

    if not s["on"]:
        return 0                      # 總開關關著：安靜離開，不印不記

    do_a = s["due"] or a.force
    # ★ --force 不得凌駕 demo 斷路 —— 8/31 實測踩到：force 把 Job C 觸發
    #   出去真打了歷史 API（233 MB ≈ 12 點）。demo 中 Job C 無條件停用。
    do_c = (s["backfill_due"] or a.force) and not s["demo"]
    if not (do_a or do_c):
        return 0                      # 兩條判定都不成立：安靜離開

    rc = 0
    # ── 這裡開始才有輸出，run_job.sh 也才會寫 log ──
    print(f"── tick {s['now']}｜應拉 {s['expected']}｜實拉 {s['latest'] or '(無)'}"
          f"｜落後 {s['behind'] or '(未知)'}"
          + ("　⚠ 靜態虛擬時間生效中" if s["virtual"] else "")
          + ("　▶ demo 回放" if s["demo"] else ""))

    # ── 判定①　當下這一格 ──
    #   demo 回放走 Job A′（baseline_grid 搬一格），真排程走 Job A（打 TDX）
    if do_a:
        if s["behind"] and s["behind"] > timedelta(minutes=config.FREQ_MIN):
            # 落後不只一格 = 中間有洞。真排程的即時 API 補不回來（交給 Job C）；
            # demo 回放的洞都在 baseline_grid，Job A′ 一次整段搬齊
            print(f"   ⚠ 落後 {s['behind']}（不只一格）："
                  + ("連中間的洞一併回放" if s["demo"]
                     else "只補當下這一格，中間的洞交給判定②的 Job C"))
        if s["demo"]:
            # ★ trigger 交給 sys_config.replay_predict 決定 —— 已經有預測、
            #   只想重播一次時設 0，Job A′ 只搬 baseline_grid，不打 endpoint
            ok, msg = replay_pull.run(s["expected"], since=s["latest"],
                                      trigger=s["replay_predict"])
            print(f"── Job A′ 結束：{'成功' if ok else '未成功'} {msg}")
        else:
            ok, msg = pull_realtime.run(s["expected"])
            print(f"── Job A 結束：{'成功' if ok else '未成功'} {msg}")
        rc = rc or (0 if ok else 1)
    else:
        print("   判定①：資料是新的，不拉")

    # ── 判定②　歷史回補自癒 ──
    #   ★ 與判定① 互不相干：Job A 失敗不該擋掉補洞，
    #     Job C 失敗也不該讓 Job A 那輪看起來像沒跑。各自記自己的 job_run。
    if do_c:
        print(f"   判定②：{s['backfill_why']}　→ 觸發 Job C")
        try:
            ok, msg = backfill.run(s["now"])
            print(f"── Job C 結束：{'成功' if ok else '未成功'} {msg}")
            rc = rc or (0 if ok else 1)
        except Exception as e:
            # Job C 已在自己那列記 failed；不讓它把整條 tick 打斷
            print(f"── Job C 失敗（已記在它自己那列）："
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            rc = 1
    else:
        print(f"   判定②：{s['backfill_why']}")

    return rc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        # ★ 每分鐘跑的東西不能因為單次例外就整條排程停掉。
        #   例外要印出來（run_job.sh 會落 log），但 rc 給 1 就好。
        print(f"✗ tick 失敗 {type(e).__name__}: {e}", file=sys.stderr)
        raise SystemExit(1)
