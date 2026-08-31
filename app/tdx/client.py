# ════════════════════════════════════════════════════════════
# tdx/client.py —— 唯一碰 TDX 的地方（對照：endpoint_repo 是唯一碰 AWS 的地方）
#
# 三條硬規則（計劃-TDX排程與初始化.md §1「省點數的硬規則」）：
#   1. gzip 不要關 —— requests 預設就送 Accept-Encoding，別去覆寫它
#   2. $select 只取要的欄位
#   3. ★ $top 預設只回 30 筆，全量必帶 $top=10000 ——
#      漏了不會報錯，只會安靜少站（最難查的那種 bug）
#
# 計費提醒：每次呼叫都扣點。開發期測試一律 $top=3（config.TDX_TOP_DEV），
# 全量只在排程與驗收步驟跑。bytes_in 是點數對帳的唯一資料來源，
# 每次呼叫都回傳，由呼叫端記進 job_run.bytes_in。
#
# 用法：
#   from app.tdx import client
#   rows, bytes_in = client.get(config.TDX_PATH_AVAILABILITY,
#                               {"$select": config.TDX_SELECT_AVAILABILITY,
#                                "$top": config.TDX_TOP_ALL})
#
# 冒煙（★ 會真打 TDX、真扣點，但 $top=3 只花零頭）：
#   uv run python -m app.tdx.client
# ════════════════════════════════════════════════════════════
import json
import time
from pathlib import Path

import requests

from app import config
from app.errors import AppError


# ════════════════════════════════════════════════════════════
# token
# ════════════════════════════════════════════════════════════
def _cache_age_sec(path: Path) -> float | None:
    """快取檔存在幾秒了；不存在回 None。

    ★ 用檔案 mtime 而不是回應的 expires_in：mtime 是 process 重啟後
      仍然存在的事實。cron 每 30 分起一個新 process，記憶體變數一定失效，
      每輪重打 auth 等於一天多花 48 次呼叫（且違反官方「快取重用」要求）。
    """
    try:
        return time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return None


def _fetch_token() -> str:
    """POST auth 換 access_token（計劃 §2 步驟 3）。"""
    if not config.TDX_CLIENT_ID or not config.TDX_CLIENT_SECRET:
        raise AppError("TDX_NO_CREDENTIALS", 500,
                       "backend/.env 缺 TDX_CLIENT_ID / TDX_CLIENT_SECRET")
    try:
        res = requests.post(
            config.TDX_AUTH_URL,
            data={"grant_type": "client_credentials",
                  "client_id": config.TDX_CLIENT_ID,
                  "client_secret": config.TDX_CLIENT_SECRET},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=config.TDX_TIMEOUT_SEC,
        )
    except requests.RequestException as e:
        raise AppError("TDX_UNAVAILABLE", 503, f"TDX auth 連線失敗：{e}") from e

    if res.status_code != 200:
        # 401 = 金鑰錯或被停權（點數用到 105% 會直接停權，錯誤訊息一樣是 401）
        raise AppError("TDX_AUTH_FAILED", 502,
                       f"TDX auth HTTP {res.status_code}：{res.text[:200]}")

    token = res.json().get("access_token")
    if not token:
        raise AppError("TDX_AUTH_FAILED", 502, "TDX auth 回應沒有 access_token")

    # ⚠ 快取檔含有效憑證，權限收成 0600，且 .cache/ 已進 .gitignore
    path = config.TDX_TOKEN_CACHE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"access_token": token,
                                "fetched_at": time.time()}), encoding="utf-8")
    path.chmod(0o600)
    return token


def get_token(force: bool = False) -> str:
    """取 token：快取未滿 23 小時就重用，否則重打 auth。"""
    age = _cache_age_sec(config.TDX_TOKEN_CACHE)
    if not force and age is not None and age < config.TDX_TOKEN_TTL_SEC:
        try:
            return json.loads(config.TDX_TOKEN_CACHE.read_text(encoding="utf-8"))["access_token"]
        except (json.JSONDecodeError, KeyError, OSError):
            pass  # 快取壞掉不是錯誤，重取就是了
    return _fetch_token()


# ════════════════════════════════════════════════════════════
# GET
# ════════════════════════════════════════════════════════════
# 最近一次 200 回應的壓縮診斷字串。純診斷用（冒煙與 job 的 log 會印），
# 不參與任何判斷 —— 想確認 gzip 有沒有生效時看它。
LAST_ENCODING = ""


def _wire_bytes(res: "requests.Response") -> int:
    """線上實際傳輸的位元組數 —— 點數對帳要的是這個。

    ★ len(res.content) 是「解壓後」的長度。requests 預設送
      Accept-Encoding: gzip，伺服器壓縮回來後 requests 會自動解開，
      content 就變回原始 JSON 大小 —— 拿它記帳會高估 5~8 倍。

    Content-Length 標頭不受解壓影響，回的就是壓縮後的線上長度。
    伺服器用 chunked 傳輸時沒有這個標頭，只能退回解壓值（會高估），
    所以另外看 Content-Encoding 判斷該不該信任這個數字。
    """
    cl = res.headers.get("Content-Length")
    if cl and cl.isdigit():
        return int(cl)
    return len(res.content)


def encoding_report(res: "requests.Response") -> str:
    """壓縮診斷字串，冒煙用 —— 確認 gzip 真的有生效。

    ★ 要一起印「Content-Length 有沒有」：沒有這個標頭時 _wire_bytes()
      會退回解壓後的長度，壓縮比就會顯示成 1.0×——那時分不出
      「伺服器沒壓」與「壓了但我們量不到」，而兩者對點數對帳的意義
      完全不同（後者是高估，會讓月耗估算虛胖 8~10 倍）。
    """
    enc = res.headers.get("Content-Encoding", "（無，未壓縮）")
    cl = res.headers.get("Content-Length")
    wire, raw = _wire_bytes(res), len(res.content)
    ratio = f"{raw / wire:.1f}×" if wire else "?"
    src = ("Content-Length" if cl and cl.isdigit()
           else "⚠ 無 Content-Length，退回解壓值（可能高估）")
    return (f"Content-Encoding={enc}｜線上 {wire:,} B／解壓後 {raw:,} B"
            f"（{ratio}，來源 {src}）")


def _request(url: str, query: dict) -> "requests.Response":
    """打一次 TDX 並回 200 的 Response（含 401/429/5xx 重試）。

    ★ 全專案只有這一份重試與退避邏輯 —— get()（JSON）與 get_text()
      （歷史 API 的 CSV）共用它。寫第二份遲早會跟第一份長歪，
      而退避寫歪的症狀是「偶爾少資料」，最難查。

    重試：429（超過 5 次/秒）與 5xx 退避 2 秒，最多 TDX_RETRY_MAX 次。
    401 只重試一次且必定強制重取 token（token 可能提前失效）。
    """
    # ★ 不設 Accept-Encoding —— requests 預設會送 gzip, deflate，
    #   自己填反而容易寫成 identity 而關掉壓縮（月流量 ×8~10）
    headers = {"authorization": f"Bearer {get_token()}"}

    last_err = ""
    retried_401 = False
    for attempt in range(config.TDX_RETRY_MAX + 1):
        try:
            res = requests.get(url, params=query, headers=headers,
                               timeout=config.TDX_TIMEOUT_SEC)
        except requests.RequestException as e:
            last_err = f"連線失敗：{e}"
            if attempt < config.TDX_RETRY_MAX:
                time.sleep(config.TDX_RETRY_WAIT_SEC)
                continue
            raise AppError("TDX_UNAVAILABLE", 503, f"{url} {last_err}") from e

        if res.status_code == 200:
            global LAST_ENCODING
            LAST_ENCODING = encoding_report(res)
            return res

        if res.status_code == 401 and not retried_401:
            retried_401 = True
            headers["authorization"] = f"Bearer {get_token(force=True)}"
            continue

        if res.status_code == 429 or res.status_code >= 500:
            last_err = f"HTTP {res.status_code}"
            if attempt < config.TDX_RETRY_MAX:
                time.sleep(config.TDX_RETRY_WAIT_SEC)
                continue

        # 4xx（除了上面處理過的）重試沒有意義，直接報
        raise AppError("TDX_REQUEST_FAILED", 502,
                       f"{url} HTTP {res.status_code}：{res.text[:200]}")

    raise AppError("TDX_REQUEST_FAILED", 502,
                   f"{url} 重試 {config.TDX_RETRY_MAX} 次仍失敗（{last_err}）")


def get(path: str, params: dict, base: str | None = None) -> tuple[list, int]:
    """打一支 TDX API，回 (rows, bytes_in)。

    path   : 不含 base 的路徑，如 config.TDX_PATH_AVAILABILITY
    params : OData 參數；★ 全量要自己帶 "$top": config.TDX_TOP_ALL
    base   : 預設 basic（基礎服務）；歷史 API 傳 config.TDX_HIST_BASE

    bytes_in = 線上實際傳輸的位元組，呼叫端記進 job_run.bytes_in
    —— 這是點數對帳的唯一依據，量錯就對不上會員中心的使用統計。
    ★ 不能用 len(resp.content)：那是 requests 解壓「後」的長度。
      實測站點主檔 709 KB 解壓值 vs 標頭 Content-Length 差 5~8 倍，
      用錯的那個會把月耗高估一個數量級。見 _wire_bytes()。
    """
    # $format 固定 JSON；要 CSV（歷史 API）請改用 get_text()
    res = _request((base or config.TDX_API_BASE) + path,
                   {"$format": "JSON", **params})
    data = res.json()
    # 多數 v2 端點直接回陣列；少數包成 {"Stations": [...]}，兩種都收
    rows = data if isinstance(data, list) else next(
        (v for v in data.values() if isinstance(v, list)), [])
    return rows, _wire_bytes(res)


def get_text(path: str, params: dict, base: str | None = None) -> tuple[str, int]:
    """打一支 TDX API 並回 (回應原文, bytes_in) —— 歷史 API 的 CSV 用這支。

    ★ 不強加 $format：呼叫端要什麼格式自己在 params 指定
      （歷史回補用 "$format": "CSV"）。get() 會強制 JSON，
      拿它打 CSV 只會得到 JSON。

    ★ res.text 是「解壓後」的原文（正是 CSV parser 要的）；
      bytes_in 仍取 Content-Length = 線上位元組。歷史服務 20 MB/1 點，
      兩者差 8~10 倍，記錯這一欄點數對帳直接失準。
    """
    res = _request((base or config.TDX_API_BASE) + path, params)
    return res.text, _wire_bytes(res)


# ════════════════════════════════════════════════════════════
# 冒煙：uv run python -m app.tdx.client
#   ★ 真打 TDX、真扣點。固定 $top=3，連打兩次驗第二次不重打 auth。
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import datetime as dt

    want = set(config.TDX_SELECT_AVAILABILITY.split(","))

    def _age_str():
        age = _cache_age_sec(config.TDX_TOKEN_CACHE)
        return "（無快取）" if age is None else f"（快取 {age:.1f} 秒前寫入）"

    print(f"token 快取 {config.TDX_TOKEN_CACHE} {_age_str()}")

    for i in (1, 2):
        t0 = time.time()
        rows, n = get(config.TDX_PATH_AVAILABILITY,
                      {"$select": config.TDX_SELECT_AVAILABILITY,
                       "$top": config.TDX_TOP_DEV})
        print(f"\n第 {i} 次：{len(rows)} 站 / bytes_in={n:,} / {time.time() - t0:.2f}s "
              f"{_age_str()}")
        print(f"  {LAST_ENCODING}")
        if rows:
            got = set(rows[0].keys())
            print(f"  欄位 {sorted(got)}")
            print(f"  五欄俱全：{'✓' if want <= got else '✗ 缺 ' + str(want - got)}")
            src = rows[0].get("SrcUpdateTime")
            if src:
                # ★ SrcUpdateTime 帶 +08:00 時區，寫 DB 前要先轉台北再 floor
                ts = dt.datetime.fromisoformat(src)
                print(f"  SrcUpdateTime {src} → 距今 "
                      f"{(dt.datetime.now(ts.tzinfo) - ts).total_seconds() / 60:.1f} 分鐘")
            print(f"  首列 {rows[0]}")

    print("\n✓ 第 2 次的快取寫入時間若與第 1 次相同，代表沒有重打 auth")
