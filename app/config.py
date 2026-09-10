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
#   ★ 2026-09-04：TDX 金鑰兩鍵已隨拉取邏輯移除（本檔不再讀它們）。
#     .env 裡留著無害，現在用到的是 PG 連線與 ENDPOINT_* 那幾個。
load_dotenv(BASE_DIR / ".env", override=False)

# ── AWS / endpoint ──
REGION        = "ap-northeast-1"                              # ml-deepar/sm_train.py
ENDPOINT_NAME = os.environ.get("ENDPOINT_NAME", "youbike-deepar-d2604v2-r2")

# ★ H 與 CONTEXT 寫死不從 meta.json 讀（8/28 教訓：cal_h6 那份 meta 是舊值
#   cp 來的）。慣例照舊 —— 以下數值抄自
#   ml-deepar/data/sm_experiments.csv 的 d2604v2-r2 列。
H        = 6      # 一次吐 6 格 = 30min × 6 = 3 小時
CONTEXT  = 48     # 過去 24 小時
FREQ_MIN = 30

# ⚠ CONTEXT = 48 讓模型效益打對折。實測（think-report/training/evaluation.md §4）：
#   同一批序列、只改 context 長度，對 baseline 的改善是
#     完整（≤1,200 格） 21.4%
#     336 格（一週）    19.4%
#     48 格（現值）     11.7%
#   機制：DeepAR 對 30min 頻率的 lag 最遠取到約一週，48 格讓那些 lag 落空，
#   預測系統性偏低（h6 平均 −2.14 台、大站 −3.35 台），而且完全沒有錯誤訊息。
#   ⚠ 改成 336 要一起動：history_repo.tail()、calendar_repo.holiday_seq() 的
#     長度、「不足 N 格歷史」的擋門（會擋掉更多新站），payload 0.02→0.11 MB/批，
#     以及**重新校準 Q_LO**（336 格的 q* 是 0.14，不是 0.175）。

# ★ 2026-09-10 校準（think-report/training/evaluation.md §6）：
#   d2604v2-r2、context 48 格、2026-04 前半校準／後半驗證（按時間切，不隨機），
#   目標覆蓋率 90%（cov = P(實際 >= 下界)）。
#     擬合 q* = 0.175   套用時六格覆蓋 91.9 / 89.3 / 88.1 / 88.3 / 89.3 / 89.6
#                       全落在 88~92 容忍帶內 → **逐格 q* 不值得做**
#     採用 0.18        與 0.175 覆蓋率完全相同（都是 89.40%）——
#                       100 個樣本下兩者內插到同一個界。取兩位小數是為了
#                       不引入沒驗證過的三位小數 quantile 字串（endpoint 已刪，
#                       無法實測），也與 calib_fit.py 自己印的建議一致。
#     舊值 0.19        全體覆蓋 88.78%（略偏樂觀）
#
# ★★ 真正該分層的是**時段**，不是 horizon。逐窗 q*：
#     早尖峰 06:30–09:00   0.115   ← 現行 0.19 只蓋 85.2%
#     晚尖峰 15:30–18:00   0.145
#     晚尖峰 18:30–21:00   0.145
#     日間離峰 12:30–15:00  0.185
#     晚離峰 21:30–00:00   0.255
#     轉離峰 09:30–12:00   0.255
#   全距 0.14，是逐 horizon（0.07）的兩倍。
#   ⚠ **早尖峰是缺車最密的時段**（08:30 有 42.3% 的站在門檻下），
#     卻也是下界最不準的時段 —— 缺車在最該抓的地方被漏掉。
#   逐時段要改三處（Q_LO 改 map、送多組 quantiles、按時段取值），本次未做。
#
# ⚠ q* 綁 context 長度（336 格是 0.14）與時段組成，但**不綁隨機種子**
#   —— 同組態的兩顆模型在相同窗上量到的 q* 完全一致（evaluation.md §6-3）。
Q_LO, Q_MID, Q_HI = "0.18", "0.5", "0.9"
NUM_SAMPLES = 100

# ★ 方向：cov = P(實際 >= 下界)。覆蓋率低於目標＝下界壓得不夠低＝
#   缺車發生了卻沒被標記。舊版註解寫「下界偏鬆，缺車會被低估」，
#   結論方向對，但沒說出時段差異 —— 而時段差異是 0.14 的全距。
CAVEATS = ["q=0.18 為 d2604v2-r2 @ context 48 格的全時段校準值（目標覆蓋 90%，實測 89.4%）；"
           "尖峰時段的 q* 更低（早尖峰 0.115），單一值在早尖峰覆蓋率僅 86.0%，"
           "該時段的缺車可能被漏掉"]

# ⚠⚠ 命名陷阱：下界的欄位名一路叫 `q19`（DB 欄位 forecast_history.q19、
#   API 回應欄位、前端），那是 Q_LO = "0.19" 時代留下的字面值。
#   Q_LO 現在是 0.18，**名稱已經與值不符**。
#   沒有一起改名的理由：改名要動 DB 欄位 ＋ API 契約 ＋ 前端三層，
#   而 endpoint_repo 是用 `q[config.Q_LO]` 動態索引，功能不受影響。
#   ⇒ 讀 `q19` 時請以本檔的 Q_LO 為準。若之後做逐時段 q*，
#     那次一起把欄位改成 `q_lo` 比較划算。

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
# ★ 2026-09-02：名字還叫 PRELOAD 但已經不預載了（決策 13，demo.py 不再碰
#   level30）。現在它的角色是「demo 視窗的下界」—— --reset 清
#   forecast_history / risk_snapshot / forecast_run 與 --status 統計都用它。
#   沒改名是因為三處引用都還在用，改名的收益不抵風險；語意以這段註解為準。
DEMO_PRELOAD_FROM = "2026-04-01"          # demo 視窗下界（歷史區由 sql/43 灌）

# mock 模式：不開 endpoint 也能串通鏈路。mock 的數字沒有意義，
# 回應會帶 "mock": true —— 只驗鏈路不驗預測。
MOCK = os.environ.get("ENDPOINT_MOCK", "0") == "1"

# 鄰站 cat 代理（meet/20260828/計劃-鄰站cat代理.md，做法 A）：
# cat IS NULL 的站借鄰站 cat、餵本站歷史。關掉即回到 422 STATION_UNKNOWN。
PROXY_CAT_ENABLED = os.environ.get("PROXY_CAT_ENABLED", "1") == "1"

# ── 模型卡（回應的 model 區塊）──
# ★ demo 用模型：訓練集 2025-08-01 ~ 2026-03-31（回放 2026-05 時沒見過答案），
#   test 為 2026-04 整月 179 原點、評分窗 06:30–00:00 無縫（排除無人調度的凌晨）。
#   對照表 ml-deepar/data/sagemaker_demo2604_v2/。
#
# ★ 為什麼是 r2：同組態獨立重訓三次（r1 0.22153 / r2 0.22422 / r3 0.22567），
#   全距 0.00414，統計上等價。r2 的分數最接近三輪平均 0.22381 ——
#   部署那顆自己的成績與對外講的數字一致。挑最好的 r1 等於把種子的運氣
#   算進模型的成績裡（think-report/training/evaluation.md §3）。
#
# ⚠ test_wql 不可與 demo2604 的 0.19688 並排 —— 那是覆蓋時段不同的考卷
#   （舊考卷含凌晨、缺早尖峰）。可比的是「對 baseline 的相對改善」：
#   舊考卷 12.6%，本考卷 **20.6%**。
MODEL_INFO = {
    "job": "youbike-deepar-d2604v2-r2-20260910-152245",
    "H": H, "context": CONTEXT,
    "test_wql": 0.22422, "test_rmse": 5.0417,
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
# 台北時區：slot 一律台北時間 naive timestamp（對齊 level30 既有慣例）。
# ★ 不能用系統本地時區 —— cron 的 TZ 可能與開發機不同，
#   算出來的 slot 會整批偏移，而且不會報錯。
TZ_TAIPEI = "Asia/Taipei"

# ════════════════════════════════════════════════════════════
# ★★ 2026-09-04：TDX 相關設定全部移除（約 30 個常數）
#   出處：meet/20260904/計劃-移除TDX拉取邏輯.md §1-5。
#   移除的有：金鑰兩鍵／auth 與 basic/historical 兩條 base／四支 path／
#   兩組 $select／$top 兩個／token 快取兩項／逾時重試三項／
#   歷史 API 四項／Job C 四道判定與用量護欄／TDX_RATE／TDX_JOB_CATEGORY。
#
#   一併移除的零使用常數（原使用者是被刪掉的那些檔案）：
#     STALE_MAX_HOURS       原本由 pull_realtime 與 hist_repo 用。
#       ⚠ 這條規則本身沒有消失 —— 它寫死在 sql/43_level30_carry.sql
#         與 baseline_grid 的重採樣裡（>6 格留 NULL），不是這個常數在管。
#     IMPUTE_LOOKBACK_DAYS / IMPUTE_WINDOW_DAYS   週期補值（impute_weekly）
#     BACKFILL_*（4 個）    Job C 的判定門檻
#
#   保留的（不屬 TDX，仍有使用者）：
#     TZ_TAIPEI／FREQ_MIN／JOB_A_MIN_COVERAGE（replay_pull:163 在用）／
#     DEMO_*／LEVEL30_RETENTION_DAYS（零使用，但 retention 未實作，
#     sql/43 的註解仍指著它）。
# ════════════════════════════════════════════════════════════

# Job B 一次 invoke 塞幾站。DeepAR 的 instances 是陣列，1,600 站分 32 批
# 打完；不是 1,600 次單發（計劃 §3 Job B 步驟 2）。
PREDICT_BATCH_SIZE = 50

# Job A′ 的成功判定：寫入 level30 的站數 ≥ 主檔站數 × 這個比例才觸發 Job B。
# 半份資料打出來的預測比沒有更糟（計劃 §3 Job A 步驟 4）。
# ★ 常數名保留 JOB_A_ 前綴：replay_pull:163 就是拿它當門檻，改名沒有好處。
JOB_A_MIN_COVERAGE = 0.8

# ── 風險門檻（8/31 定案，依 2026-04 全月 201 萬格實測校準）──
#   T = clamp(round(RISK_PCT × 車柱), RISK_MIN, RISK_MAX)
#     缺車風險：可借 avail <= T　／　滿站風險：可還 cap-avail <= T
#   為什麼不是純比例 20%：實測會標紅 31.4% 的格子，警報疲勞、沒有資訊量。
#   為什麼不是純絕對 <=2：對 99 柱大站太鬆（滿站集中在小站，命中站
#     capacity 中位數 15 vs 全市 25），封頂 5 是折衷。
#   這組參數的實測觸發率：缺車 20.1%／滿站 2.4%／任一 22.5%。
#   ★ 8/31 使用者定案改 15%／85%（原 10% 封頂 5 是我挑的，缺車觸發率
#     20.1% → 24.5%，更敏感）。RISK_MAX=None 表示不封頂（99 柱大站 T=15）。
RISK_PCT, RISK_MIN, RISK_MAX = 0.15, 2, None

# ── 調度台數基準（8/31 使用者定案）──
#   目標不是「剛好脫離紅區」，是「回到這站這個時段的常態水位」：
#     補車 = ceil(歷史平均 − 窗尾 q50)　／　取車 = ceil(窗尾 q50 − 歷史平均)
#   ★ 為什麼比「窗尾」不比「最嚴重那一格」——模型已經把自然消退算進去了，
#     調度只該搬「三小時後仍然不會自己消退的那部分」。實測 origin 17:00：
#     取車 12 站舊算法 20 台／比最嚴重格 113 台／比窗尾 49 台，其中 3 站
#     算出 0 台（尖峰時卡住但會自己退燒，派車是白跑）。
#   ★ 風險燈號不受影響 —— 燈號仍看窗內最嚴重的一格，卡人的事實不會被抹掉。
#   樣本數不足或查無該桶時退回原本的門檻算法（補到 T+1 = 安全範圍）。
SLOT_AVG_MIN_N = 10

# ── 風險判定的演算法版本（9/1）──
#   寫進 risk_snapshot.algo_ver 與 forecast_run.risk_algo_ver。
#   ★ 改門檻（RISK_PCT/MIN/MAX）或改分級規則（risk_service）就要改這個字串：
#     ① 冪等判定會失效 → 舊 origin 會被重判（--risk-only，不打 SageMaker）
#     ② streak 隨即重新起算 —— 跨版本的「連續 N 輪」沒有意義
#   格式：<分級制>/<門檻參數>。time-v1 = 8/31 定案的時間制分級
#   （高=現況已越線且近 1 小時仍越線／中=1HR 內／低=1~3HR 內／無=不越線）。
RISK_ALGO_VER = "time-v1/pct15"

# ── level30 滾動視窗（計劃-排程自癒 §2）──
#   ★ 留 14 天而非 7：給 Job C 補洞與驗證重跑留餘裕。retention 在階段②實作。
LEVEL30_RETENTION_DAYS = 14

