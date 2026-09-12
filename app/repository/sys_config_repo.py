# ════════════════════════════════════════════════════════════
# sys_config_repo —— hackathon_backend_sys_config 的讀寫與轉型
#
# 值在 DB 一律 text，轉型集中在這裡，別讓每個呼叫端各轉一次。
#
# ★ effective_now() 是整個系統唯一該問「現在幾點」的地方。
#   直接呼叫 datetime.now() 的地方會繞過 virtual_now，demo 時就會有
#   一半的模組活在現在、一半活在虛擬時間 —— 那種不一致最難查。
#
# ★★ 2026-09-12 改：**流速搬進 DB**（demo_speed）。
#   出處：meet/20260912/計劃-demo回放邏輯重整.md §3-1。
#   舊制 config.DEMO_SPEED 是環境變數，uvicorn／jobs／另開終端的 --status
#   各讀各的，同一個 DB 算出差好幾倍的「現在」，畫面看起來像卡住
#   （COMMANDS.md 舊版 §2「流速的坑」自承的問題）。現在 env 只當預設值。
#   ⚠ 改速度一律走 set_speed() —— 它會先重新錨定再寫值。直接 set() 那個鍵
#     會讓虛擬時間瞬間跳走（now = t0v + 經過 × 速度）。
#
# ★★ 同日退場：demo_tail_speed／demo_auto_slow 兩鍵。
#   「遇到要現算的格就降為 1x」從可選旗標變成 demo.py 每格必做的事，
#   速度來源收斂成 demo_speed 一個。demo_until 保留，語意簡化為單純停表。
# ════════════════════════════════════════════════════════════
import os
import socket
from datetime import datetime, timedelta
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
# 9/12 新增：流速（取代環境變數 DEMO_SPEED）。改值一律走 set_speed()
K_DEMO_SPEED = "demo_speed"
# 9/12 新增：正在現算的 origin —— 前端靠它上全頁遮罩「最新資料載入中」
K_DEMO_PREDICTING = "demo_predicting"
K_DEMO_PREDICTING_SINCE = "demo_predicting_since"   # 寫入當下的**真實**時間
# 9/12 新增：回放迴圈的租約。兩個 demo --run 同時推時鐘不會報錯，只會
#   默默打兩次 endpoint、寫兩列 job_run —— 用租約在啟動時就擋掉
K_DEMO_LEASE = "demo_loop_lease"       # 值格式：'pid@host|expires_real'

_TS = "%Y-%m-%d %H:%M:%S"

# 遮罩的保險絲：demo_predicting 距今超過這麼久（真實分鐘）就當作殘留。
# ★ 迴圈被 kill -9 時這個鍵會留著 → 前端遮罩永遠不關、demo 直接開天窗。
#   一輪 1,600 站的預測跑不到 10 分鐘，超過就是沒人來清。
# ★ 2026-09-12 二修：10 → 15。遮罩改成涵蓋整格（搬資料＋預測＋風險＋收單，
#   見 jobs/demo.py step()），最長那一輪比純預測久。TTL 若在推進中途燒斷，
#   遮罩會提早關掉、露出正在被刪掉重寫的 risk_snapshot —— 正是要避免的事。
PREDICTING_TTL_MIN = 15


def get(key: str) -> str | None:
    """空字串視同未設定 —— 手動 UPDATE 時打成 '' 的機率不低。"""
    with get_conn().cursor() as cur:
        cur.execute("SELECT value FROM hackathon_backend_sys_config WHERE key = %s",
                    (key,))
        row = cur.fetchone()
    v = row["value"] if row else None
    return v if v not in (None, "") else None


def get_many(keys: tuple[str, ...]) -> dict[str, str | None]:
    """一次取多鍵，省往返。★ effective_now() 用它把 4 次查詢併成 1 次 ——
    那支在每個 API 的熱路徑上，流速改讀 DB 之後不併會很痛。"""
    with get_conn().cursor() as cur:
        cur.execute("SELECT key, value FROM hackathon_backend_sys_config "
                    "WHERE key = ANY(%s)", (list(keys),))
        got = {r["key"]: r["value"] for r in cur.fetchall()}
    return {k: (got.get(k) if got.get(k) not in (None, "") else None) for k in keys}


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


def _to_ts(v: str | None) -> datetime | None:
    if v is None:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None                     # 手動填壞了就當沒設定，不讓排程炸掉
    return d.replace(tzinfo=None) if d.tzinfo is None else d.astimezone(TZ).replace(tzinfo=None)


def get_ts(key: str) -> datetime | None:
    """取時間值，回台北 naive datetime（對齊 slot 慣例）。"""
    return _to_ts(get(key))


def all_rows() -> list[dict]:
    with get_conn().cursor() as cur:
        cur.execute("SELECT key, value, note, updated_at "
                    "FROM hackathon_backend_sys_config ORDER BY key")
        return cur.fetchall()


# ── 語意包裝 ──────────────────────────────────────────────────
_CLOCK_KEYS = (K_VIRTUAL_NOW, K_DEMO_T0_REAL, K_DEMO_T0_VIRTUAL,
               K_DEMO_UNTIL, K_DEMO_SPEED)


def _real_now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def _speed_of(v: str | None) -> float:
    """流速字串 → 倍率。填壞或未設 → config.DEMO_SPEED（env 只是預設值）。
    ★ 下限 0 不是 0.01：0 的語意是「停表」，要留給人手動凍結用。"""
    if v is None:
        return float(config.DEMO_SPEED)
    try:
        return max(0.0, float(v))
    except ValueError:
        return float(config.DEMO_SPEED)


def demo_speed() -> float:
    """目前的回放流速（真實 1 秒 = 虛擬 demo_speed 秒）。

    ★ 9/12 起真相在 DB，不在環境變數。config.DEMO_SPEED 只在這個鍵
      沒設定時當預設值 —— 所以 uvicorn 不必再帶 DEMO_SPEED 啟動。
    """
    return _speed_of(get(K_DEMO_SPEED))


def set_speed(v: float) -> float:
    """改流速，並**先重新錨定**時鐘。回傳改完的值。

    ★ 這三行的順序是全檔最要緊的一段：
        ① 用「舊速度」算出當下虛擬時刻
        ② 把 t0_virtual / t0_real 挪到此刻
        ③ 才寫新速度
      少了①②，now = t0v + (real − t0r) × 速度 會在寫值那一瞬間跳走
      —— ×5 改 ×30 且已跑了 10 分鐘真實，虛擬時間會當場往前噴 4 小時。
      舊制把這件事寫在 COMMANDS.md 叫人自己記得，沒有任何程式替人做。

    ★ 非 demo 模式（兩鍵沒成對）只寫值不錨定 —— 沒有時鐘可以錨。
    """
    v = max(0.0, float(v))
    if is_demo():
        now_v = effective_now()         # ① 舊速度下的當下
        set(K_DEMO_T0_VIRTUAL, now_v)   # ②
        set(K_DEMO_T0_REAL, _real_now())
    set(K_DEMO_SPEED, f"{v:g}")         # ③
    return v


def effective_now() -> datetime:
    """系統的「現在」，三段判定（8/31 demo 回放模式）：

      ① virtual_now 有值 → 用它（靜態，除錯用，優先序最高 ——
         demo 中要凍結在某一刻檢查狀態時，設它就能蓋過 demo 時鐘）
      ② demo 兩鍵成對 → demo_t0_virtual + (真實now − demo_t0_real) × demo_speed
         （會走的虛擬時鐘；靜態 virtual_now 會讓 tick 永遠判定「資料是新的」，
           這是 8/28 踩過的坑，所以 demo 用 offset 制）
      ③ 都沒有 → 真實台北時間

    ②走到 demo_until 就停表（9/12 起沒有 tail 倍率這回事 —— 「到點後改速度」
    等於 set_speed()，不需要第二個速度來源）。

    回台北 naive datetime。★ 所有 job 與 API 都該問這支，
    不要各自呼叫 datetime.now()。

    ★ 一次 get_many 取齊 5 鍵。這支被每個 API 呼叫，流速改讀 DB 之後
      逐鍵查會變成 5 次往返 —— 不併的話這筆改動會直接反映在回應時間上。
    """
    r = get_many(_CLOCK_KEYS)
    v = _to_ts(r[K_VIRTUAL_NOW])
    if v is not None:
        return v
    t0r, t0v = _to_ts(r[K_DEMO_T0_REAL]), _to_ts(r[K_DEMO_T0_VIRTUAL])
    if t0r is not None and t0v is not None:
        v = t0v + (_real_now() - t0r) * _speed_of(r[K_DEMO_SPEED])
        # ★ demo_until 在這裡處理而不是在呼叫端的判定：只改一處，
        #   回放迴圈的判定、healthz 的 now/data_age 全都跟著一致，
        #   不會出現「時鐘走過頭但資料停住」的兩套現在。
        until = _to_ts(r[K_DEMO_UNTIL])
        if until is not None and v > until:
            return until
        return v
    return _real_now()


def is_virtual() -> bool:
    """現在是不是凍結在靜態虛擬時間 —— 呼叫端據此在輸出加警告。
    ★ demo 回放不算：它的時鐘會走，用 is_demo() 判。"""
    return get_ts(K_VIRTUAL_NOW) is not None


def is_demo() -> bool:
    """demo 回放模式是否生效（兩鍵成對才算 —— 只設一鍵視為沒設）。"""
    r = get_many((K_DEMO_T0_REAL, K_DEMO_T0_VIRTUAL))
    return (_to_ts(r[K_DEMO_T0_REAL]) is not None
            and _to_ts(r[K_DEMO_T0_VIRTUAL]) is not None)


def replay_predict() -> bool:
    """demo 回放要不要觸發 Job B（打 endpoint）。

    未設定時預設「要」—— 既有行為不因為新增這個鍵而改變。
    ★ 設 0 是為了「已經有預測、只想重播一次」與「endpoint 沒開」的場合：
      9/12 起它的作用是讓迴圈第②步**不降速也不預測**，只印一行警告。
      不要改用 ENDPOINT_MOCK=1 達成同一件事：batch_predict 寫入是
      ON CONFLICT DO UPDATE，mock 會逐格覆蓋掉既有的真預測。
    """
    v = get(K_REPLAY_PREDICT)
    return True if v is None else v.strip() not in ("0", "false", "off", "no")


def demo_ended() -> bool:
    """demo 回放是否已經走到 demo_until 而停表。純粹給輸出加註用。"""
    until = get_ts(K_DEMO_UNTIL)
    return until is not None and is_demo() and effective_now() >= until


def scheduler_on() -> bool:
    """總開關。未設定時預設「開」（新環境不該因為忘了設而不跑）。"""
    v = get(K_SCHEDULER_ON)
    return True if v is None else v.strip() not in ("0", "false", "off", "no")


# ── 現算中的旗標（前端全頁遮罩）────────────────────────────────
def set_predicting(origin: datetime | None) -> None:
    """進入／離開「現算中」。origin=None 代表算完了。

    ★ 同時寫真實時間戳，給 predicting() 的 TTL 用。用真實時間不是虛擬時間
      —— 保險絲要量的是「這個進程死多久了」，那是真實世界的事。
    """
    set(K_DEMO_PREDICTING, origin)
    set(K_DEMO_PREDICTING_SINCE, _real_now() if origin is not None else None)


def predicting() -> tuple[datetime | None, datetime | None]:
    """(正在算的 origin, 開始的真實時間)。沒在算 → (None, None)。

    ★ 殘留自癒：since 距真實現在超過 PREDICTING_TTL_MIN 就當作沒有。
      迴圈被 kill -9 時這兩鍵會留著，沒有這道保險前端遮罩會永遠不關。
      （另一道在 demo.py --run 的啟動流程：起跑先清。）
    """
    r = get_many((K_DEMO_PREDICTING, K_DEMO_PREDICTING_SINCE))
    origin = _to_ts(r[K_DEMO_PREDICTING])
    if origin is None:
        return None, None
    since = _to_ts(r[K_DEMO_PREDICTING_SINCE])
    if since is not None and _real_now() - since > timedelta(minutes=PREDICTING_TTL_MIN):
        return None, since              # 殘留：不回報 origin，但把 since 留著給人看
    return origin, since


# ── 回放迴圈租約（單一實例）────────────────────────────────────
def _me() -> str:
    return f"{os.getpid()}@{socket.gethostname()}"


def acquire_lease(ttl_sec: int) -> bool:
    """搶／續回放迴圈的租約。搶到回 True。

    ★ 為什麼不是檔案鎖：檔案鎖只擋得住同一台機器同一個掛載點。租約在 DB，
      容器、另一台機器、cron 起的那份都擋得到 —— 而「兩個迴圈同時推時鐘」
      的症狀是 job_run 同 slot 兩列、endpoint 被打兩次，**完全不會報錯**。

    ★ 為什麼是一句 SQL 而不是「先讀再寫」：兩個進程同時啟動會雙雙讀到空值、
      雙雙寫入，兩個都以為自己搶到。INSERT..ON CONFLICT..WHERE 是原子的。

    ★ 過期比較直接比字串：值是 'YYYY-MM-DD HH:MM:SS' 固定寬度，
      字典序等於時序。省掉在 SQL 裡轉型，也就不會因為某列被人手動填壞而炸。
    """
    me, exp = _me(), (_real_now() + timedelta(seconds=ttl_sec)).strftime(_TS)
    with get_conn().cursor() as cur:
        cur.execute(
            """
            INSERT INTO hackathon_backend_sys_config (key, value) VALUES (%(k)s, %(v)s)
            ON CONFLICT (key) DO UPDATE
               SET value = EXCLUDED.value, updated_at = now()
             WHERE hackathon_backend_sys_config.value IS NULL
                OR hackathon_backend_sys_config.value = ''
                OR split_part(hackathon_backend_sys_config.value, '|', 1) = %(me)s
                OR split_part(hackathon_backend_sys_config.value, '|', 2) < %(now)s
            """,
            {"k": K_DEMO_LEASE, "v": f"{me}|{exp}", "me": me,
             "now": _real_now().strftime(_TS)})
        return cur.rowcount > 0


def lease_holder() -> tuple[str | None, datetime | None]:
    """(持有者, 到期真實時間)。沒人持有或已過期 → (None, None)。"""
    v = get(K_DEMO_LEASE)
    if not v or "|" not in v:
        return None, None
    who, _, exp = v.partition("|")
    t = _to_ts(exp)
    if t is None or t < _real_now():
        return None, t
    return who, t


def release_lease() -> bool:
    """釋放自己持有的租約（別人的不動）。"""
    with get_conn().cursor() as cur:
        cur.execute("DELETE FROM hackathon_backend_sys_config "
                    "WHERE key = %s AND split_part(value, '|', 1) = %s",
                    (K_DEMO_LEASE, _me()))
        return cur.rowcount > 0


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="查看/設定 sys_config")
    p.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"),
                   help="設定一個鍵（VALUE 給 - 代表清空）")
    a = p.parse_args()

    if a.set:
        k, v = a.set
        if k == K_DEMO_SPEED:
            # ★ 擋下來而不是照寫：直接寫這個鍵不會重新錨定，時鐘會跳。
            print(f"✗ {K_DEMO_SPEED} 請用：uv run python -m jobs.demo --speed {v}")
            raise SystemExit(2)
        set(k, None if v == "-" else v)
        print(f"已設定 {k} = {v if v != '-' else '(清空)'}")

    tag = ("  ⚠⚠ 靜態虛擬時間生效中（排程會停在這裡，用完請清掉）" if is_virtual()
           else "  ⏸ demo 已走到 demo_until，時鐘停表中" if demo_ended()
           else f"  ▶ demo 回放中（×{demo_speed():g}）" if is_demo()
           else "  （真實時間）")
    print(f"\n有效 now = {effective_now()}{tag}")
    print(f"排程開關 = {'開' if scheduler_on() else '關'}"
          f"｜回放觸發 Job B = {'是' if replay_predict() else '否（不打 endpoint）'}")
    po, _ = predicting()
    who, exp = lease_holder()
    print(f"現算中   = {po or '否'}｜迴圈租約 = {who or '(無)'}"
          + (f"（到期 {exp}）" if who else "") + "\n")
    for r in all_rows():
        print(f"  {r['key']:22} {str(r['value'] or '(未設定)'):30} "
              f"{r['updated_at'].astimezone(TZ):%m-%d %H:%M:%S}")
