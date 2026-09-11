# ════════════════════════════════════════════════════════════
# sys_config_repo —— hackathon_backend_sys_config 的讀寫與轉型
#
# 值在 DB 一律 text，轉型集中在這裡，別讓每個呼叫端各轉一次。
#
# ★ effective_now() 是整個系統唯一該問「現在幾點」的地方。
#   直接呼叫 datetime.now() 的地方會繞過 virtual_now，demo 時就會有
#   一半的模組活在現在、一半活在虛擬時間 —— 那種不一致最難查。
# ════════════════════════════════════════════════════════════
from datetime import datetime
from zoneinfo import ZoneInfo

from app import config
from app.repository.db import get_conn

TZ = ZoneInfo(config.TZ_TAIPEI)

# 鍵名常數：打錯字只會讀到 None（安靜地當成沒設定），所以不用字面值
K_CURRENT_SLOT = "current_slot"
K_FORECAST_END = "forecast_end"
K_SCHEDULER_ON = "scheduler_on"
K_VIRTUAL_NOW = "virtual_now"
K_LAST_TICK = "last_tick"
# demo 回放模式（8/31）：兩鍵成對出現才生效，effective_now 以差值推算
K_DEMO_T0_REAL = "demo_t0_real"        # demo 啟動時的真實時間
K_DEMO_T0_VIRTUAL = "demo_t0_virtual"  # demo 啟動時的虛擬起點（2026-05-01）
# 9/1 新增：讓回放能「只搬資料、跑到某一刻就停」，全程不碰 endpoint
K_DEMO_UNTIL = "demo_until"            # 虛擬時鐘終點；到點停表（空 = 一路跑下去）
K_REPLAY_PREDICT = "replay_predict"    # 回放要不要觸發 Job B（0 = 不打 endpoint）
# 9/11 新增：走到 demo_until 之後不停表，改用這個倍率續走（0／未設 = 停表，原行為）
K_DEMO_TAIL_SPEED = "demo_tail_speed"
# 9/11 新增：已預測過的格快轉通過，一走到「要現算」的格就把 demo_until 標在當下
#   → 時鐘自動降為 tail 速度。人不必事先算終點落在哪一格。
K_DEMO_AUTO_SLOW = "demo_auto_slow"

_TS = "%Y-%m-%d %H:%M:%S"


def get(key: str) -> str | None:
    """空字串視同未設定 —— 手動 UPDATE 時打成 '' 的機率不低。"""
    with get_conn().cursor() as cur:
        cur.execute("SELECT value FROM hackathon_backend_sys_config WHERE key = %s",
                    (key,))
        row = cur.fetchone()
    v = row["value"] if row else None
    return v if v not in (None, "") else None


def set(key: str, value) -> None:
    """寫值（datetime 自動轉台北 naive 字串；None 代表清空）。"""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(TZ).replace(tzinfo=None)
        value = value.strftime(_TS)
    with get_conn().cursor() as cur:
        cur.execute(
            "INSERT INTO hackathon_backend_sys_config (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            (key, None if value is None else str(value)))


def get_ts(key: str) -> datetime | None:
    """取時間值，回台北 naive datetime（對齊 slot 慣例）。"""
    v = get(key)
    if v is None:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None                     # 手動填壞了就當沒設定，不讓排程炸掉
    return d.replace(tzinfo=None) if d.tzinfo is None else d.astimezone(TZ).replace(tzinfo=None)


def all_rows() -> list[dict]:
    with get_conn().cursor() as cur:
        cur.execute("SELECT key, value, note, updated_at "
                    "FROM hackathon_backend_sys_config ORDER BY key")
        return cur.fetchall()


# ── 語意包裝 ──────────────────────────────────────────────────
def effective_now() -> datetime:
    """系統的「現在」，三段判定（8/31 demo 回放模式）：

      ① virtual_now 有值 → 用它（靜態，除錯用，優先序最高 ——
         demo 中要凍結在某一刻檢查狀態時，設它就能蓋過 demo 時鐘）
      ② demo 兩鍵成對 → demo_t0_virtual + (真實now − demo_t0_real) × DEMO_SPEED
         （會走的虛擬時鐘；靜態 virtual_now 會讓 tick 永遠判定「資料是新的」，
           這是 8/28 踩過的坑，所以 demo 用 offset 制）
      ③ 都沒有 → 真實台北時間

    ②走到 demo_until 之後分兩種（9/11）：demo_tail_speed = 0／未設 → 停表；
    > 0 → 以該倍率續走（前段快轉看完，後段接著跑並持續產生新預測）。

    回台北 naive datetime。★ 所有 job 與 API 都該問這支，
    不要各自呼叫 datetime.now()。
    """
    v = get_ts(K_VIRTUAL_NOW)
    if v is not None:
        return v
    t0r, t0v = get_ts(K_DEMO_T0_REAL), get_ts(K_DEMO_T0_VIRTUAL)
    if t0r is not None and t0v is not None:
        real = datetime.now(TZ).replace(tzinfo=None)
        v = t0v + (real - t0r) * config.DEMO_SPEED
        # ★ demo_until 在這裡處理而不是在 tick 的判定：只改一處，
        #   tick 兩條判定、healthz 的 now/data_age 全都跟著一致，
        #   不會出現「時鐘走過頭但資料停住」的兩套現在。
        until = get_ts(K_DEMO_UNTIL)
        if until is not None and v > until:
            tail = tail_speed()
            # tail = 0：停表（9/1 起的原行為）。到點後 expected == latest，
            # tick 每輪安靜離開 = 卡在那裡，這是「純重播、放完就停」要的。
            if tail <= 0:
                return until
            # tail > 0：到點後改用 tail 倍率續走。
            # ★ 到點對應的真實時刻用反推，不另存一個鍵 —— 不必在到點那一刻
            #   有人（或某支程序）去記錄，重啟、換容器都算得出同一個值。
            real_at_until = t0r + (until - t0v) / config.DEMO_SPEED
            return until + (real - real_at_until) * tail
        return v
    return datetime.now(TZ).replace(tzinfo=None)


def is_virtual() -> bool:
    """現在是不是凍結在靜態虛擬時間 —— 呼叫端據此在輸出加警告。
    ★ demo 回放不算：它的時鐘會走，用 is_demo() 判。"""
    return get_ts(K_VIRTUAL_NOW) is not None


def is_demo() -> bool:
    """demo 回放模式是否生效（兩鍵成對才算 —— 只設一鍵視為沒設）。"""
    return (get_ts(K_DEMO_T0_REAL) is not None
            and get_ts(K_DEMO_T0_VIRTUAL) is not None)


def replay_predict() -> bool:
    """demo 回放要不要觸發 Job B（打 endpoint）。

    未設定時預設「要」—— 既有行為不因為新增這個鍵而改變。
    ★ 設 0 是為了「已經有預測、只想重播一次」的場合。
      不要改用 ENDPOINT_MOCK=1 達成同一件事：batch_predict 寫入是
      ON CONFLICT DO UPDATE，mock 會逐格覆蓋掉既有的真預測。
    """
    v = get(K_REPLAY_PREDICT)
    return True if v is None else v.strip() not in ("0", "false", "off", "no")


def tail_speed() -> float:
    """走到 demo_until 之後的續走倍率。0 或未設 = 停表（9/1 起的原行為）。

    ★ 與 DEMO_SPEED 的分工：DEMO_SPEED 是環境變數、管 demo_until 之前那段；
      這個鍵在 DB、只管到點之後。分開是因為前段常要快轉看完整天，
      後段是「接上去繼續跑」，兩者要的速度不一樣。
    """
    v = get(K_DEMO_TAIL_SPEED)
    if v is None:
        return 0.0
    try:
        return max(0.0, float(v))
    except ValueError:
        return 0.0                      # 填壞了當沒設定，不讓排程炸掉


def auto_slow() -> bool:
    """是否啟用「遇到要現算的格就自動降速」。未設 = 關（既有回放不受影響）。

    ★ 語意上的分工：demo_until 原本是人指定的終點，開了這個鍵之後改由
      Job A′ 在第一次遇到未預測的 origin 時就地標記（標當下、不標那格的
      origin —— 標 origin 會讓 effective_now 往回跳，前端時鐘倒退）。
    """
    v = get(K_DEMO_AUTO_SLOW)
    return False if v is None else v.strip() not in ("0", "false", "off", "no")


def demo_tailing() -> bool:
    """是否已過 demo_until 且正在以 tail 倍率續走。給輸出加註用。"""
    until = get_ts(K_DEMO_UNTIL)
    return (until is not None and is_demo() and tail_speed() > 0
            and effective_now() > until)


def demo_ended() -> bool:
    """demo 回放是否已經走到 demo_until 而且停表。純粹給輸出加註用。

    ★ tail_speed > 0 時時鐘是會走的，不算「結束」—— 少了這個條件，
      --status 會在續走中還印「停表中」。
    """
    until = get_ts(K_DEMO_UNTIL)
    return (until is not None and is_demo() and tail_speed() <= 0
            and effective_now() >= until)


def scheduler_on() -> bool:
    """總開關。未設定時預設「開」（新環境不該因為忘了設而不跑）。"""
    v = get(K_SCHEDULER_ON)
    return True if v is None else v.strip() not in ("0", "false", "off", "no")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="查看/設定 sys_config")
    p.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"),
                   help="設定一個鍵（VALUE 給 - 代表清空）")
    a = p.parse_args()

    if a.set:
        k, v = a.set
        set(k, None if v == "-" else v)
        print(f"已設定 {k} = {v if v != '-' else '(清空)'}")

    tag = ("  ⚠⚠ 靜態虛擬時間生效中（排程會停在這裡，用完請清掉）" if is_virtual()
           else "  ⏸ demo 已走到 demo_until，時鐘停表中" if demo_ended()
           else f"  ▶▶ 已過 demo_until，續走中（×{tail_speed():g}）" if demo_tailing()
           else f"  ▶ demo 回放中（×{config.DEMO_SPEED:g}）" if is_demo()
           else "  （真實時間）")
    print(f"\n有效 now = {effective_now()}{tag}")
    print(f"排程開關 = {'開' if scheduler_on() else '關'}"
          f"｜回放觸發 Job B = {'是' if replay_predict() else '否（不打 endpoint）'}\n")
    for r in all_rows():
        print(f"  {r['key']:14} {str(r['value'] or '(未設定)'):22} "
              f"{r['updated_at'].astimezone(TZ):%m-%d %H:%M:%S}")
