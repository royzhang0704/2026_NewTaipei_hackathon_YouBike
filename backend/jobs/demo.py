# ════════════════════════════════════════════════════════════
# jobs/demo.py —— demo 回放：時鐘開關 ＋ **推進迴圈**
# 規格：meet/20260831/計劃-demo回放模式與重訓.md §2-2
#       meet/20260912/計劃-demo回放邏輯重整.md（本次改版的出處）
#
#   uv run python -m jobs.demo --start            # 設 demo 時鐘
#   uv run python -m jobs.demo --start --speed 30 # 起跑就指定流速
#   uv run python -m jobs.demo --start --reset    # ★ 先清預測再起跑（重跑用）
#   uv run python -m jobs.demo --run              # ★ 常駐迴圈（唯一的時鐘推進者）
#   uv run python -m jobs.demo --speed 1          # ★ 迴圈跑著也能改，另開終端下即生效
#   uv run python -m jobs.demo --status           # 虛擬時刻／回放進度
#   uv run python -m jobs.demo --stop             # 清時鐘與書籤
#
# ════════════════════════════════════════════════════════════
# ★★ 2026-09-12 改版三件事（計劃 §0.1 的四題定案）：
#
#   ① 流速搬進 DB（sys_config.demo_speed），env 的 DEMO_SPEED 降為預設值。
#      舊制每個進程各讀各的 —— uvicorn、jobs、另開終端的 --status 同一個 DB
#      卻算出差好幾倍的「現在」，畫面看起來就像卡住。
#      ⇒ 後端啟動不必再帶 DEMO_SPEED，它就只是提供 API。
#
#   ② 推進迴圈從 cron/jobs.tick 搬到這裡（--run）。tick.py 已刪除。
#      每推進一格做四件事，順序固定：
#        ① 搬資料　baseline_grid → level30（replay_pull）
#        ② 預測　　有預測 → 快轉通過；沒有 → **先降 1x** 再現算
#        ③ 風險　　先刪該 origin 的 risk_snapshot，再無條件重判
#        ④ 調度　　用本輪 origin 的風險現況收單
#
#   ③ 現算期間寫 sys_config.demo_predicting，前端據此上全頁遮罩。
#
# ⚠ 全檔最要緊的一條：**②的降速必須在打 endpoint 之前**。
#   1,600 站的批次預測真實要跑數十秒，×30 快轉下虛擬時鐘已經跳過十幾小時 ——
#   算出來的預測會掛在一個早就過去的 origin 上，前端拿不到，畫面空白。
#
# ⚠ 定案②：降速之後**不自動回快轉**。要回快轉是人的決定：另開終端下
#   `--speed 30`。（舊制的 demo_auto_slow / demo_tail_speed 兩鍵已退場。）
#
# ★ 2026-09-02：本檔不再碰 level30（預載與 --purge 都移除了）。
#   出處：meet/20260902/計劃-level30灌歷史與無限carry.md 決策 13。
#   理由：level30 的 04~07 月是「歷史真相的重採樣」，不是 demo 產生的狀態
#   —— reset demo 不該動它。單一出處改成 code_backend/sql/43_level30_carry.sql。
#
# --start 做四件事：
#   ① demo_t0_real = 現在、demo_t0_virtual = config.DEMO_VIRTUAL_T0
#   ② current_slot = 起點前一格 —— ★ 必要：殘留的真排程書籤比虛擬 now 晚，
#      不重設就會判定「不落後」而永遠不動
#   ③ 清 forecast_end（那是真排程的預測終點，對虛擬時間軸是謊言）
#   ④ 清 demo_predicting 兩鍵（上一場的殘留會讓前端一開場就是遮罩）
#
# --stop 清 demo 全部鍵 + current_slot + forecast_end + virtual_now（保險）。
# ════════════════════════════════════════════════════════════
import argparse
import signal
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import config
from app.repository import risk_repo
from app.repository import sys_config_repo as sc
from app.repository.db import get_conn
from jobs import replay_pull

TZ = ZoneInfo(config.TZ_TAIPEI)
STEP = timedelta(minutes=config.FREQ_MIN)

# ── 迴圈參數 ──────────────────────────────────────────────────
# 睡眠上限（真實秒）。★ 為什麼不是 60：×30 快轉下 60 秒真實 = 30 分鐘虛擬，
#   一輪就跨過一整格，逐格判定（②要不要降速）當場失效。
#   為什麼不固定 2 秒：1x 下要空轉 900 次才推一格。取「距下一格」與它的小者。
POLL_MAX_SEC = 2.0
# 租約長度（真實秒）。每輪續約。★ 要比「一輪最長耗時」長很多 ——
#   現算那一輪會卡住幾十秒不續約，TTL 太短會讓自己的租約在跑預測時過期，
#   另一個進程就能合法接手，變成兩個迴圈。15 分鐘遠大於一輪 1,600 站。
LEASE_TTL_SEC = 900
# last_tick 的寫入節流（真實秒）。見 step() 裡的說明。
TICK_WRITE_SEC = 20.0

_stop = False
_tick_written = 0.0     # 上次寫 last_tick 的 monotonic 秒（見 step 的節流）


def _on_signal(signum, _frame):
    """Ctrl-C / docker stop：設旗標，讓迴圈跑完這一輪再收工。

    ★ 不在 handler 裡清鍵或關連線 —— handler 可能落在 DB 交易中間。
      收尾統一在 run_loop 的 finally。
    """
    global _stop
    _stop = True
    print(f"\n── 收到訊號 {signal.Signals(signum).name}，這一輪跑完就收工…")


# ── 共用 ──────────────────────────────────────────────────────
def latest_slot() -> datetime | None:
    """已拉到的最新 slot。以 sys_config 為準，沒有值才回頭問 level30。

    ★ 回頭問 level30 是為了「第一次啟用」與「有人手動清了 sys_config」——
      沒有這道，current_slot 是 NULL 時會判定成落後而重拉一輪。
    """
    v = sc.get_ts(sc.K_CURRENT_SLOT)
    if v is not None:
        return v
    with get_conn().cursor() as cur:
        cur.execute("SELECT max(slot) AS s FROM hackathon_backend_level30")
        return cur.fetchone()["s"]


# ── 推進一格的四個階段 ────────────────────────────────────────
def stage_predict(origin: datetime) -> str:
    """②：這一格有預測就快轉通過，沒有就先降 1x 再現算。

    ★ 順序：降速 → 開遮罩 → 打 endpoint → 關遮罩。降速漏在後面的話，
      現算那幾十秒真實時間在 ×30 下等於十幾小時虛擬，預測寫進去就過期了。

    ★ 失敗不中斷：預測掛掉仍要走③④ —— 風險判定吃的是既有預測，
      這一格沒有新預測不代表風險不該重算（會判成 no_forecast，那是誠實的）。
    """
    if replay_pull.has_forecast(origin):
        print("   ② 預測：已有，快轉通過（不打 endpoint）")
        return "reuse"

    if not sc.replay_predict():
        # 純重播／endpoint 沒開。★ 一定要印 —— 否則 log 看不出這一格
        #   「為什麼沒有預測」，而那正是最常被問的一句。
        print("   ② 預測：⚠ 沒有，但 replay_predict=0 → 不現算也不降速")
        return "off"

    before = sc.demo_speed()
    if before > 1:
        sc.set_speed(1)                 # ★ 必須在打 endpoint 之前
        print(f"   ⏬ 這一格要現算 → 時鐘 ×{before:g} 降為 ×1"
              "（定案：就此維持 1x，要回快轉請另開終端下 --speed N）")

    # ★ 遮罩不在這裡開關了（9/12 二修）——改由 step() 包住整格。
    #   這裡自己開關的話，②做完到③做完之間會有一段沒遮罩的空窗，
    #   而那正是 risk_snapshot 被刪掉重寫的時候。
    try:
        from jobs import batch_predict
        print(f"   ② 預測：現算 → {batch_predict.run(origin, stages='predict')}")
        return "fresh"
    except Exception as e:              # noqa: BLE001
        print(f"   ② ⚠ 預測失敗（本輪其他階段照走）{type(e).__name__}: {e}")
        return "failed"


def stage_risk(origin: datetime) -> bool:
    """③：先清該 origin 的風險快照，再**無條件**重判。

    ★ 為什麼不走 batch_predict._maybe_risk：那支有「同 algo_ver 判過就跳過」
      的冪等閘。回放每推進一格都該重判 —— 上一格的判定剛被重寫，
      streak 遞推（risk_repo.prev）吃的是前一輪，沿用舊快照兩邊會對不起來。

    ★ 為什麼先刪不只靠 upsert 覆蓋：覆蓋只蓋得到「這輪也有算到」的站。
      這輪站數變少時，殘列會留著，同一個 origin 變成一半新一半舊的混合快照
      —— /alerts 吃的是「一個 origin 的全部列」，會顯示早就不成立的警示。
      理由全文見 risk_repo.delete_origin 的 docstring。

    ★ 失敗不中斷：風險判定掛掉仍要走④（收單吃的是最新一輪的風險現況，
      退回上一輪判總比不收單好）。回 False 讓④知道要退回。
    """
    from jobs import batch_predict

    try:
        n_del = risk_repo.delete_origin(origin)
        c = batch_predict.write_risk(origin)
        print(f"   ③ 風險：清 {n_del:,} 列後重判 {c['stations']} 站"
              f"（高 {c['risk_high']}／中 {c['risk_mid']}／低 {c['risk_low']}／"
              f"無 {c['risk_none']}／無預測 {c['risk_no_forecast']}）；"
              f"補車 {c['refill_stations']} 站 {c['refill_bikes']} 台／"
              f"取車 {c['remove_stations']} 站 {c['remove_bikes']} 台")
        return True
    except Exception as e:              # noqa: BLE001
        print(f"   ③ ⚠ 風險判定失敗（本輪其他階段照走）{type(e).__name__}: {e}")
        return False


def stage_dispatch(origin: datetime, risk_ok: bool) -> None:
    """④：依本輪風險現況收掉已完成／已失效的調度單。

    ★ 顯式傳 origin（③剛判完，不必再查 latest_risk_origin）。
      ⚠ ③失敗時**退回不傳** —— 硬塞一個沒判過風險的 origin，
      sweep 會拿到空的 snapshot，把所有 active 單誤判成「需求消失」而收掉。

    ★ 失敗不可讓整輪變 failed：調度單是使用者的決定，收不收得掉不影響
      資料與風險這兩個主產出；沒收掉的單下一輪會再被掃到（judge 是純比對）。
    """
    from jobs import dispatch_sweep

    try:
        c = dispatch_sweep.sweep(origin=origin if risk_ok else None)
        if c.get("note") or c["checked"] == 0:
            return
        print(f"   ④ 調度：檢查 {c['checked']} 筆 → 完成 {c['fulfilled']}／"
              f"失效 {c['invalid']}／續留 {c['active_remain']}")
    except Exception as e:              # noqa: BLE001
        print(f"   ④ ⚠ 調度收尾失敗（不影響本輪）{type(e).__name__}: {e}")


def step() -> bool:
    """判定要不要推進，要就走完四階段。回傳「這一輪有沒有做事」。

    ★ 什麼都不做時不印任何東西：迴圈每 2 秒醒一次，有輸出才寫 log，
      否則 log 一天會多幾萬行廢話。「迴圈還活著嗎」看 sys_config.last_tick
      與 demo_loop_lease，不看 log。
    """
    if not sc.scheduler_on():
        return False                    # 總開關關著：安靜離開

    now = sc.effective_now()

    # ★ last_tick 是「迴圈還活著」的唯一證據（healthz 的 tick_age_min 看它），
    #   但迴圈每 2 秒醒一次 —— 每輪都寫等於每小時 1,800 次無謂 UPDATE。
    #   節流成每 20 真實秒一次：tick_age 本來就以分鐘為單位，看不出差別。
    global _tick_written
    if time.monotonic() - _tick_written >= TICK_WRITE_SEC:
        sc.set(sc.K_LAST_TICK, now)
        _tick_written = time.monotonic()

    expected = replay_pull.floor_slot(now)
    latest = latest_slot()
    if latest is not None and expected <= latest:
        return False                    # 還沒跨到下一格

    behind = None if latest is None else expected - latest
    print(f"── 推進 {now}｜應拉 {expected}｜實拉 {latest or '(無)'}"
          f"｜落後 {behind or '(未知)'}"
          + ("　⚠ 靜態虛擬時間生效中" if sc.is_virtual() else ""))
    if behind and behind > STEP:
        # 落後不只一格 = 中間有洞。回放的洞都在 baseline_grid，
        # copy_range 一次整段搬齊，不需要另一支 job 回補。
        print(f"   ⚠ 落後 {behind}（不只一格）：連中間的洞一併回放")

    # ★★ 2026-09-12 二修：遮罩改成涵蓋**整格**，不再只蓋②現算那一段。
    #   原本只有「要打 endpoint」才開遮罩，於是已經有預測的格會直接放行 ——
    #   但③會先 DELETE 這個 origin 的整輪 risk_snapshot 再重判，那幾秒
    #   /alerts 讀到的是空的或半套快照，畫面閃一下空白警示；④收單同理。
    #   使用者回報「即便沒有預測，風險／調度還是會卡住一下」就是這個。
    #   ⇒ 從①搬資料到④收單全部包起來，前端在這段期間一律 loading。
    sc.set_predicting(expected)
    try:
        # ── ① 搬資料 ──
        ok, msg = replay_pull.run(expected, since=latest)
        if not ok:
            # 覆蓋率不足（該 slot 當年整片缺觀測）。書籤已在 replay_pull 裡前進，
            # 不然迴圈會每 2 秒重試同一個壞 slot，永遠卡住。
            print(f"   ① 搬資料未成功：{msg} → 這一格不做②③④")
            return True

        # ── ②③④ ──
        stage_predict(expected)
        risk_ok = stage_risk(expected)
        stage_dispatch(expected, risk_ok)
        return True
    finally:
        # ★ 放 finally：任何一階段炸掉都要關遮罩，否則畫面永遠鎖著。
        #   （另一道保險是 sys_config_repo.predicting 的 TTL 自癒。）
        sc.set_predicting(None)


# ── 常駐迴圈 ──────────────────────────────────────────────────
def _sleep_sec() -> float:
    """睡多久（真實秒）：距離下一格的真實時間，但不超過 POLL_MAX_SEC。

    ★ 不能只睡「距下一格」就好 —— 人可能在睡眠期間改流速或 --stop，
      上限讓迴圈至少每 2 秒回頭看一次 DB。
    """
    sp = sc.demo_speed()
    if sp <= 0:
        return POLL_MAX_SEC             # 停表：時鐘不走，純粹等人來改
    now = sc.effective_now()
    nxt = replay_pull.floor_slot(now) + STEP
    return max(0.2, min(POLL_MAX_SEC, (nxt - now).total_seconds() / sp))


def _nap(sec: float) -> None:
    """分段睡，讓 Ctrl-C 立刻有反應（一次睡 2 秒時最多拖 2 秒才停）。"""
    end = time.monotonic() + sec
    while not _stop and time.monotonic() < end:
        time.sleep(min(0.2, max(0.0, end - time.monotonic())))


def run_loop(force: bool = False) -> int:
    """常駐前景迴圈。這是 demo 期間**唯一**該推進虛擬時鐘的東西。"""
    if not sc.is_demo():
        print("✗ demo 未啟動（demo_t0_real / demo_t0_virtual 沒成對）"
              "—— 先跑：uv run python -m jobs.demo --start")
        return 1

    # ── 租約：擋住「兩個迴圈同時推時鐘」──
    # ★ 為什麼不是檔案鎖：檔案鎖只擋得住同一台機器同一個掛載點。租約在 DB，
    #   容器、另一台機器、忘了移除的 cron 全都擋得到 —— 而雙迴圈的症狀是
    #   job_run 同 slot 兩列、endpoint 被打兩次，**完全不會報錯**。
    who, exp = sc.lease_holder()
    if who and not force:
        print(f"✗ 已經有一個回放迴圈在跑：{who}（租約到 {exp}）")
        print("  確定那份已經死了 → uv run python -m jobs.demo --run --force")
        return 1
    if force and who:
        print(f"⚠ --force：搶走 {who} 的租約")
        sc.set(sc.K_DEMO_LEASE, None)
    if not sc.acquire_lease(LEASE_TTL_SEC):
        print("✗ 搶租約失敗（同一刻有別的迴圈也在啟動）")
        return 1

    # ★ 啟動先清殘留：上一份迴圈被 kill -9 時 demo_predicting 會留著，
    #   前端遮罩就永遠不關。（第二道防線是 healthz 的 TTL 自癒。）
    po, _ = sc.predicting()
    if po is not None or sc.get(sc.K_DEMO_PREDICTING):
        print(f"⚠ 清掉上一份迴圈殘留的 demo_predicting（{po or '已過期'}）")
    sc.set_predicting(None)

    # ★ 導向檔案時 Python 會用 4KB 區塊緩衝 stdout —— 常駐進程等於「跑了
    #   十分鐘 log 還是空的」，看起來就像掛了。改成行緩衝，`--run > run.log`
    #   與 `docker logs -f` 才看得到即時進度。（不想改碼的替代是 python -u。）
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    print(f"▶ 回放迴圈啟動｜虛擬 now = {sc.effective_now()}"
          f"｜流速 ×{sc.demo_speed():g}｜輪詢上限 {POLL_MAX_SEC:g}s")
    print("  停止：Ctrl-C　／　改流速（另開終端）：uv run python -m jobs.demo --speed N")

    try:
        while not _stop:
            try:
                sc.acquire_lease(LEASE_TTL_SEC)     # 續約（同一個 pid 必過）
                step()
            except Exception as e:                  # noqa: BLE001
                # ★ 單輪例外不可讓整個 demo 停掉。印出來（有人看著 log），
                #   睡一下再試 —— DB 短暫斷線是最常見的原因。
                print(f"✗ 這一輪失敗，繼續下一輪 {type(e).__name__}: {e}",
                      file=sys.stderr)
                _nap(POLL_MAX_SEC)
                continue
            _nap(_sleep_sec())
    finally:
        try:
            sc.set_predicting(None)
            sc.release_lease()
        except Exception as e:                      # noqa: BLE001
            print(f"⚠ 收尾清理失敗（下一份迴圈會自己處理）：{e}")
    print("■ 回放迴圈收工（時鐘鍵保留；要清請跑 --stop）")
    return 0


# ── 開關 ──────────────────────────────────────────────────────
def start(reset: bool = False, speed: float | None = None) -> int:
    if sc.is_demo() and not reset:
        print("✗ demo 已在進行中（先 --stop，或直接 --start --reset 重跑）")
        return 1
    if sc.is_virtual():
        print("✗ 靜態 virtual_now 設著（優先序比 demo 高，時鐘會不走）——"
              "先清：uv run python -m app.repository.sys_config_repo --set virtual_now -")
        return 1
    who, exp = sc.lease_holder()
    if who:
        print(f"✗ 還有回放迴圈在跑（{who}，租約到 {exp}）—— 先停掉它再 --start")
        return 1

    if reset:
        # demo 重跑：清掉上一輪的預測，回到全新起跑線。
        # ★ 只清 demo 視窗（4~5 月）—— 真排程寫的 2026-08 之後不碰，
        #   舊 MOCK／真排程預測列（origin 在 8 月）也不碰（D2 定案保留）。
        # ★★ 9/2 起 level30 不在這裡清（決策 13）。要重建歷史區請跑
        #     code_backend/sql/43_level30_carry.sql —— 它才是那段的單一出處。
        with get_conn().transaction(), get_conn().cursor() as cur:
            cur.execute("DELETE FROM hackathon_backend_forecast_history "
                        "WHERE origin >= %s AND origin < '2026-06-01'",
                        (config.DEMO_PRELOAD_FROM,))
            n_fc = cur.rowcount
            # ★★ 主檔與風險快照一定要跟著清（9/1）——
            #   不清主檔的下場：reset 完了，主檔那些 origin 還寫著
            #   predict_status='done'，迴圈第②步的 has_forecast 會說「已有預測」
            #   → 整輪快轉通過、永遠不現算，demo 畫面整片空白。
            #   這是這組改動最容易踩的坑。
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
              "-d youbike -v ON_ERROR_STOP=1 -f code_backend/sql/43_level30_carry.sql")
        return 1
    print(f"── context 檢查：{last_slot} 之前 {config.CONTEXT} 格內有 {n_ctx:,} 列")

    sc.set(sc.K_DEMO_T0_REAL, datetime.now(TZ).replace(tzinfo=None))
    sc.set(sc.K_DEMO_T0_VIRTUAL, t0v)
    sc.set(sc.K_CURRENT_SLOT, last_slot)
    sc.set(sc.K_FORECAST_END, None)
    sc.set_predicting(None)             # ★ 上一場的殘留會讓前端一開場就是遮罩
    # ★ 流速要在兩鍵寫好之後才設 —— set_speed 會重新錨定，錨的就是剛設的 t0。
    if speed is not None:
        sc.set_speed(speed)
    print(f"✓ demo 開始：虛擬 now = {t0v}（流速 ×{sc.demo_speed():g}）"
          f"｜current_slot = {last_slot}")
    print("  接著開迴圈：uv run python -m jobs.demo --run")
    return 0


def set_speed(v: float) -> int:
    """改流速。★ 迴圈跑著也能下 —— 值在 DB，下一輪就吃到。"""
    if not sc.is_demo():
        print("✗ demo 未啟動，沒有時鐘可以調速")
        return 1
    before = sc.demo_speed()
    now_before = sc.effective_now()
    after = sc.set_speed(v)
    print(f"✓ 流速 ×{before:g} → ×{after:g}"
          f"（已重新錨定，虛擬時間停在 {now_before} 沒有跳）")
    if after == 0:
        print("  ⚠ 0 = 停表：時鐘不走，迴圈不會再推進任何一格")
    return 0


def stop() -> int:
    if not sc.is_demo():
        print("（demo 未在進行，仍照清一遍鍵值）")
    who, exp = sc.lease_holder()
    if who:
        print(f"⚠ 還有回放迴圈在跑（{who}，租約到 {exp}）——"
              "它下一輪會發現時鐘鍵沒了而空轉，請一併把它停掉（Ctrl-C）")
    for k in (sc.K_DEMO_T0_REAL, sc.K_DEMO_T0_VIRTUAL, sc.K_DEMO_SPEED,
              sc.K_DEMO_UNTIL, sc.K_DEMO_PREDICTING, sc.K_DEMO_PREDICTING_SINCE,
              sc.K_CURRENT_SLOT, sc.K_FORECAST_END, sc.K_VIRTUAL_NOW):
        sc.set(k, None)
    print("✓ 已清 demo_t0_real / demo_t0_virtual / demo_speed / demo_until /"
          " demo_predicting(2) / current_slot / forecast_end / virtual_now")

    # ★ 9/2 起 --purge 已移除（決策 13）：level30 不歸本檔管。
    #   要重建歷史區跑 sql/43_level30_carry.sql，它自己會 DELETE 再重灌。
    print("  level30 不動（歷史區的出處是 sql/43_level30_carry.sql；"
          "forecast_history 一律保留，model_job 欄可區分）")
    return 0


def status() -> int:
    now = sc.effective_now()
    cur_slot = sc.get_ts(sc.K_CURRENT_SLOT)
    fe = sc.get_ts(sc.K_FORECAST_END)
    until = sc.get_ts(sc.K_DEMO_UNTIL)
    expected = replay_pull.floor_slot(now)
    latest = latest_slot()
    mode = ("靜態 virtual_now（時鐘不走！）" if sc.is_virtual()
            else f"demo 回放（×{sc.demo_speed():g}）⏸ 已到 demo_until，停表中"
                 if sc.demo_ended()
            else f"demo 回放（×{sc.demo_speed():g}）" if sc.is_demo()
            else "真實時間（demo 未啟動）")
    who, exp = sc.lease_holder()
    po, psince = sc.predicting()
    print(f"模式       {mode}")
    print(f"有效 now   {now}")
    print(f"回放迴圈   {who or '(沒有在跑)'}" + (f"　租約到 {exp}" if who else ""))
    print(f"現算中     {po or '否'}" + (f"（自 {psince} 真實時間起）" if po else ""))
    print(f"回放終點   {until or '(無，一路跑下去)'}")
    print(f"觸發 Job B {'是' if sc.replay_predict() else '否（replay_predict=0，不打 endpoint）'}")
    print(f"應拉／實拉 {expected} ／ {latest or '(無)'}"
          + (f"　落後 {expected - latest}" if latest and expected > latest else "　（是新的）"))
    print(f"預測終點   {fe or '(無)'}")
    print(f"回放到     {cur_slot or '(無)'}")
    lt = sc.get_ts(sc.K_LAST_TICK)
    print(f"上次推進   {lt or '(無)'}")
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
    ap = argparse.ArgumentParser(description="demo 回放：時鐘開關與推進迴圈")
    # ★ 刻意不設 required=True：`--speed N` 要能單獨下（demo 進行中調速是
    #   最常用的一條指令）。沒給任何動作旗標時的預設行為是 --status。
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--start", action="store_true", help="設 demo 時鐘")
    g.add_argument("--run", action="store_true",
                   help="常駐迴圈：唯一的時鐘推進者（前景，Ctrl-C 收工）")
    g.add_argument("--stop", action="store_true", help="清時鐘與書籤")
    g.add_argument("--status", action="store_true", help="虛擬時刻／回放進度")
    g.add_argument("--step", action="store_true",
                   help="只推進一格就離開（除錯用，不搶租約）")
    ap.add_argument("--speed", type=float, metavar="N",
                    help="流速倍率。單獨下 = 立即改（會重新錨定，時鐘不跳）；"
                         "與 --start 併用 = 起跑值")
    ap.add_argument("--reset", action="store_true",
                    help="與 --start 併用：先清 demo 視窗的預測再起跑（重跑）")
    ap.add_argument("--force", action="store_true",
                    help="與 --run 併用：搶走別人的租約（確定那份已經死了才用）")
    a = ap.parse_args()

    if a.start:
        return start(a.reset, a.speed)
    if a.run:
        return run_loop(a.force)
    if a.stop:
        return stop()
    if a.step:
        step()
        return 0
    if a.speed is not None:             # 單獨 --speed N
        return set_speed(a.speed)
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
