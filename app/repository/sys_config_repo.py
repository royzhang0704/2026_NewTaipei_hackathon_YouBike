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

    回台北 naive datetime。★ 所有 job 與 API 都該問這支，
    不要各自呼叫 datetime.now()。
    """
    v = get_ts(K_VIRTUAL_NOW)
    if v is not None:
        return v
    t0r, t0v = get_ts(K_DEMO_T0_REAL), get_ts(K_DEMO_T0_VIRTUAL)
    if t0r is not None and t0v is not None:
        real = datetime.now(TZ).replace(tzinfo=None)
        return t0v + (real - t0r) * config.DEMO_SPEED
    return datetime.now(TZ).replace(tzinfo=None)


def is_virtual() -> bool:
    """現在是不是凍結在靜態虛擬時間 —— 呼叫端據此在輸出加警告。
    ★ demo 回放不算：它的時鐘會走，用 is_demo() 判。"""
    return get_ts(K_VIRTUAL_NOW) is not None


def is_demo() -> bool:
    """demo 回放模式是否生效（兩鍵成對才算 —— 只設一鍵視為沒設）。"""
    return (get_ts(K_DEMO_T0_REAL) is not None
            and get_ts(K_DEMO_T0_VIRTUAL) is not None)


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
           else f"  ▶ demo 回放中（×{config.DEMO_SPEED:g}）" if is_demo()
           else "  （真實時間）")
    print(f"\n有效 now = {effective_now()}{tag}")
    print(f"排程開關 = {'開' if scheduler_on() else '關'}\n")
    for r in all_rows():
        print(f"  {r['key']:14} {str(r['value'] or '(未設定)'):22} "
              f"{r['updated_at'].astimezone(TZ):%m-%d %H:%M:%S}")
