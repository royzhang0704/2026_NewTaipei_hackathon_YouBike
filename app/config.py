# ════════════════════════════════════════════════════════════
# config.py —— 常數集中地。每個值都註明來源，不要憑印象改。
# ════════════════════════════════════════════════════════════
import os
from pathlib import Path

from dotenv import load_dotenv

# ── 專案路徑 ──
#   BASE_DIR = backend/（本檔在 backend/app/config.py，往上兩層）
BASE_DIR = Path(__file__).resolve().parent.parent

# ★ .env 只在這裡載入一次，且不覆蓋既有環境變數（override=False）——
#   cron/CI 用真環境變數注入時，檔案不該把它蓋回去。
#   金鑰（TDX_CLIENT_ID / TDX_CLIENT_SECRET）只從 backend/.env 讀，不進版控。
load_dotenv(BASE_DIR / ".env", override=False)

# ── AWS / endpoint ──
REGION        = "ap-northeast-1"                              # ml-deepar/sm_train.py
ENDPOINT_NAME = os.environ.get("ENDPOINT_NAME", "youbike-deepar-demo2604")

# ★ H 與 CONTEXT 寫死不從 meta.json 讀（8/28 教訓：cal_h6 那份 meta 是舊值
#   cp 來的）。demo2604 的 meta 雖是新產的可信版，慣例照舊 ——
#   以下數值抄自 ml-deepar/data/sm_experiments.csv 的 demo2604 列。
H        = 6      # 一次吐 6 格 = 30min × 6 = 3 小時
CONTEXT  = 48     # 過去 24 小時
FREQ_MIN = 30

# ★ q=0.19 是 H=4 校準出來的值；H=6 的逐格 q*_1~q*_6 尚未校準。
#   預測愈遠不確定性愈大，6 格共用一個 q* 會系統性低估缺車，
#   而且愈後面的格子低估愈多 —— 所以每個回應都要帶 CAVEATS。
Q_LO, Q_MID, Q_HI = "0.19", "0.5", "0.9"
NUM_SAMPLES = 100

CAVEATS = ["q=0.19 為 H=4 校準值；H=6 逐格 q* 尚未校準，下界偏鬆，缺車會被低估"]

# ── demo 回放模式（meet/20260831/計劃-demo回放模式與重訓.md §2）──
#   sys_config 的 demo_t0_real / demo_t0_virtual 兩鍵有值即生效
#   （單一事實源在 DB，不在這裡 —— 這裡只放不隨 demo 開關變的參數）。
#   流速：真實經過 1 秒 = 虛擬經過 DEMO_SPEED 秒。
#   5 = 真實 1 分鐘走虛擬 5 分鐘（8/31 定案），一格 30 分 = 真實 6 分鐘。
#   ⚠ 改流速前要先重新錨定 demo_t0_*（以當前虛擬時刻為新起點），
#     否則 now = t0v + 經過時間 × 速度 會讓虛擬時間瞬間跳走。
#     改完 config 記得重啟 uvicorn（tick 每輪新 process 會自動吃到新值）。
DEMO_SPEED = float(os.environ.get("DEMO_SPEED", "5"))
DEMO_VIRTUAL_T0 = "2026-05-01 08:00:00"   # 虛擬時間起點（8/31 使用者定案：早上八點開場）
DEMO_PRELOAD_FROM = "2026-04-01"          # --start 預載 level30 的起日（給足 context）

# mock 模式：不開 endpoint 也能串通鏈路。mock 的數字沒有意義，
# 回應會帶 "mock": true —— 只驗鏈路不驗預測。
MOCK = os.environ.get("ENDPOINT_MOCK", "0") == "1"

# 鄰站 cat 代理（meet/20260828/計劃-鄰站cat代理.md，做法 A）：
# cat IS NULL 的站借鄰站 cat、餵本站歷史。關掉即回到 422 STATION_UNKNOWN。
PROXY_CAT_ENABLED = os.environ.get("PROXY_CAT_ENABLED", "1") == "1"

# ── 模型卡（回應的 model 區塊）──
# ★ demo 用模型：訓練集 2025-08-01 ~ 2026-03-31（回放 2026-05 時沒見過答案），
#   test 為 2026-04 整月 119 原點。對照表 ml-deepar/data/sagemaker_demo2604/。
MODEL_INFO = {
    "job": "youbike-deepar-demo2604-20260831-084604",
    "H": H, "context": CONTEXT,
    "test_wql": 0.19688, "test_rmse": 4.5235,
    "names_sha": "a14aff9be3e733bc",
}

# ── PostgreSQL（backend/sql/10_restore_source.sh 建的容器，port 5433）──
PG = dict(
    host=os.environ.get("PGHOST", "127.0.0.1"),
    port=int(os.environ.get("PGPORT", "5433")),
    user=os.environ.get("PGUSER", "postgres"),
    password=os.environ.get("PGPASSWORD", "postgres"),
    dbname=os.environ.get("PGDATABASE", "youbike"),
)

# ════════════════════════════════════════════════════════════
# TDX（運輸資料流通服務平臺）
#   出處：meet/20260828/計劃-TDX排程與初始化.md §0~§2（官方費率頁與 swagger 查證）
#   方案：銅級（月費 200 元 / 200 點 / 5 次每秒每金鑰）
# ════════════════════════════════════════════════════════════

# 金鑰：只從 backend/.env 讀（上面 load_dotenv）。缺值不在 import 時炸，
# 讓 client 取 token 那一刻才報 —— 不打 TDX 的服務啟動不該被卡住。
TDX_CLIENT_ID     = os.environ.get("TDX_CLIENT_ID", "")
TDX_CLIENT_SECRET = os.environ.get("TDX_CLIENT_SECRET", "")

# ── 端點 ──（計劃 §2 步驟 3；basic/historical 兩條 base 分屬不同計費類別）
TDX_AUTH_URL = ("https://tdx.transportdata.tw/auth/realms/TDXConnect"
                "/protocol/openid-connect/token")
TDX_API_BASE = "https://tdx.transportdata.tw/api/basic"        # 基礎服務：1,500 次/1 點
TDX_HIST_BASE = "https://tdx.transportdata.tw/api/historical"  # 歷史服務：10 次/1 點（貴 150 倍）
TDX_CITY = "NewTaipei"                                          # 本專案只做新北

# 常用 path（給 client.get() 的第一參數，不含 base）
TDX_PATH_AVAILABILITY = f"/v2/Bike/Availability/City/{TDX_CITY}"  # 即時可借可還
TDX_PATH_STATION      = f"/v2/Bike/Station/City/{TDX_CITY}"       # 站點主檔

# $select（站點主檔）：v2/Bike/Station 的巢狀欄位整包取回
#   StationName {Zh_tw,En} / StationPosition {PositionLat,PositionLon} /
#   StationAddress {Zh_tw,En}。主檔一天只拉一次，欄位多一點不影響點數。
#   ⚠ LocationTown 官方回傳恆為空字串，行政區只能靠 station_uid 第 8~9 位
#     或地址字串（raw/importdata/…站位歷史資料 欄位說明.md:63 已記載）。
TDX_SELECT_STATION = ("StationUID,StationName,StationPosition,"
                      "StationAddress,BikesCapacity")

# ★ $select 五欄：不要全欄位。全欄位 ≈230 B/站 × 1,600 站 = 0.37 MB/次，
#   一個月 550 MB ≈ 4 點；只取五欄再加 gzip 降到 ≈1 點。（計劃 §1 省點規則 2）
TDX_SELECT_AVAILABILITY = ("StationUID,AvailableRentBikes,AvailableReturnBikes,"
                           "ServiceStatus,SrcUpdateTime")

# ★★ $top 預設只回 30 筆（swagger 查證）—— 漏帶不會報錯，只會安靜少站。
#    全量呼叫一律帶 TDX_TOP_ALL；開發期測試一律用 $top=3。（計劃 §1 省點規則 3）
TDX_TOP_ALL = 10000
TDX_TOP_DEV = 3

# ★ 資料新鮮度門檻：SrcUpdateTime 距 slot 超過這個時數的站，
#   level30 不寫列（按缺格處理）。3 小時 = 重採樣規則的 carry-forward
#   上限 6 格 × 30 分（計劃 §3「30 分重採樣規則」第 2 條）——
#   站台斷訊時 TDX 仍回舊值，照寫進 level30 會變成假的水平線。
#   actual_history 仍照寫快照（保留證據），兩張表在這裡刻意不一致。
STALE_MAX_HOURS = 3

# 台北時區：slot 一律台北時間 naive timestamp（對齊 level30 既有慣例）。
# ★ 不能用系統本地時區 —— cron 的 TZ 可能與開發機不同，
#   算出來的 slot 會整批偏移，而且不會報錯。
TZ_TAIPEI = "Asia/Taipei"

# Job B 一次 invoke 塞幾站。DeepAR 的 instances 是陣列，1,600 站分 32 批
# 打完；不是 1,600 次單發（計劃 §3 Job B 步驟 2）。
PREDICT_BATCH_SIZE = 50

# Job A 的成功判定：寫入 level30 的站數 ≥ 主檔站數 × 這個比例才觸發 Job B。
# 半份資料打出來的預測比沒有更糟（計劃 §3 Job A 步驟 4）。
JOB_A_MIN_COVERAGE = 0.8

# ── token 快取 ──（官方要求快取重用，效期 24h、建議 23h 重取；計劃 §0）
#   檔案存 backend/.cache/tdx_token.json，判斷用「檔案 mtime」而不是回應的
#   expires_in —— mtime 是重啟後仍在的事實，記憶體變數不是。
TDX_TOKEN_CACHE = BASE_DIR / ".cache" / "tdx_token.json"
TDX_TOKEN_TTL_SEC = 23 * 3600

# ── 逾時與重試 ──（銅級超過 5 次/秒回 HTTP 429，不另行通知，要自己 backoff）
TDX_TIMEOUT_SEC = 30
TDX_RETRY_MAX = 3      # 429/5xx 的重試次數上限（不含第一次）
TDX_RETRY_WAIT_SEC = 2  # 每次退避固定 2 秒（計劃 §3 Job A 步驟 2）

# ════════════════════════════════════════════════════════════
# 歷史 API 與 Job C backfill（自癒回補）
#   出處：meet/20260828/計劃-排程自癒與level30滾動視窗.md §0~§2
# ════════════════════════════════════════════════════════════

# 歷史服務端點（base 用 TDX_HIST_BASE，計費類別與 basic 不同）
#   GET /api/historical/v2/Historical/Bike/Availability/NewTaipei
#       ?Dates=2026-09-05~2026-09-11&$format=CSV&$top=99999999
TDX_PATH_HIST_AVAIL = f"/v2/Historical/Bike/Availability/{TDX_CITY}"

# ★ Dates 一刀最多 7 日（計劃-TDX排程與初始化.md §1 swagger 查證）。
#   超過要自己分刀 —— 給 8 天不會報錯，只會安靜地少回幾天。
TDX_HIST_MAX_DAYS = 7
# 歷史 API 的 $top：CSV 是逐筆回報列（單日 27 萬列），不是站數
TDX_HIST_TOP = 99999999

# ★ 歷史 API 每日 08:00 才更新至「昨日」（計劃-TDX排程與初始化.md §3 查證）。
#   → 當日缺格當天補不到，最快隔天 08:00 後自癒。這是 tick 判定 c 的依據。
TDX_HIST_READY_HOUR = 8

# ── 風險門檻（8/31 定案，依 2026-04 全月 201 萬格實測校準）──
#   T = clamp(round(RISK_PCT × 車柱), RISK_MIN, RISK_MAX)
#     缺車風險：可借 avail <= T　／　滿站風險：可還 cap-avail <= T
#   為什麼不是純比例 20%：實測會標紅 31.4% 的格子，警報疲勞、沒有資訊量。
#   為什麼不是純絕對 <=2：對 99 柱大站太鬆（滿站集中在小站，命中站
#     capacity 中位數 15 vs 全市 25），封頂 5 是折衷。
#   這組參數的實測觸發率：缺車 20.1%／滿站 2.4%／任一 22.5%。
RISK_PCT, RISK_MIN, RISK_MAX = 0.10, 2, 5

# ── Job C 的四道判定（計劃-排程自癒 §1 ②，由便宜到貴）──
#   a 今日尚未有 backfill success   b 失敗退避   c 08:00   d 缺格率
BACKFILL_WINDOW_DAYS = 10       # 缺格偵測與回補的視窗（= seed 天數，理由見計劃 §2）
BACKFILL_GAP_THRESHOLD = 0.10   # 單日缺格率超過這個才值得花點數拉
BACKFILL_RETRY_AFTER_MIN = 60   # 今日最後一次 failed 距今未滿這麼久就不重試

# ── 用量護欄（計劃-TDX排程與初始化.md §3「用量護欄」）──
#   本月估算點數超過就停 Job C，只保 Job A/B。銅級 200 點、用到 105% 停權。
BACKFILL_POINT_LIMIT = 150

# ★ 扣點換算表（113.4.1 定價表，計劃-TDX排程與初始化.md §0）——
#   次數與流量「合併」計算，兩邊都要算進去。
#   歷史服務比基礎服務貴 150 倍（次數）／7.5 倍（流量），不可共用一組數字。
TDX_RATE = {
    "basic":      {"calls_per_point": 1500, "mb_per_point": 150},
    "historical": {"calls_per_point": 10,   "mb_per_point": 20},
}
# job_name → 計費類別。新增 job 時要一起加，漏了會被當成 basic 而低估點數。
TDX_JOB_CATEGORY = {
    "pull_realtime": "basic",
    "sync_stations": "basic",
    "backfill":      "historical",
    "init_backfill": "historical",
}

# ── level30 滾動視窗（計劃-排程自癒 §2）──
#   ★ 留 14 天而非 7：給 Job C 補洞與驗證重跑留餘裕。retention 在階段②實作。
LEVEL30_RETENTION_DAYS = 14

# ── 週期補值（計劃-排程自癒 §2「週期補值」，8/28 使用者定案直接上線）──
#   當日缺格歷史 API 補不到（每日 08:00 才更新至昨日），改在組 payload
#   那一層用「一週前同 slot」補。★ 值不落 level30 —— 理由見
#   predict_service.build_payload() 的註解。
#   取 7 天而不是 1 天：星期幾決定作息，週五的早上像上週五不像昨天週四。
IMPUTE_LOOKBACK_DAYS = 7

# ★ 8/28 使用者定案：補值**寫進 level30**（計劃原文是「值不落表」）。
#   落表就必須有 is_imputed 旗標與三條硬規則，見 sql/42_level30_is_imputed.sql
#   的欄位註解。每輪 Job A 寫完當下這格之後，順手補這個天數內的洞。
#   2 天：足夠蓋住 48 格（24 小時）的服務視窗還有餘裕，又不必每輪掃 14 天。
IMPUTE_WINDOW_DAYS = 2
