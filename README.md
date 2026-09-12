# YouBike 智慧調度後端

> 2026 新北市 AI 智慧城市黑客松 — 交通局命題
> 「新北市公共自行車營運調度數據視覺化及預測模型」

用 **DeepAR 時序模型**預測新北市 1,538 個 YouBike2.0 站點未來 3 小時的車輛水位，
把預測翻成調度單位看得懂的**風險燈號**與**派車台數**。

---

## 1. 參賽資訊

| 項目 | 內容 |
|---|---|
| 賽事 | 2026 新北市 AI 智慧城市黑客松（DIGITIMES 主辦） |
| 組別 | 交通局 —— YouBike 智慧調度 |
| 決賽 | 2026/9/12–13　新北市政府大禮堂 |
| 資料集 | 新北市資料開放平台 — YouBike2.0 租賃站歷史數據（6 個月）|
| 技術限制 | 生成式／預測模型僅限 Amazon Bedrock、SageMaker AI，不可用外部 API |

### 團隊成員

林義旻、黃一珊、陳冠年、張志強、鄭明勳

### 命題痛點 → 本專案的對應

| 命題痛點 | 本專案作法 |
|---|---|
| ① 尖峰時段「無車可借／無位可還」 | 風險門檻 `T = max(2, 15% × 車柱)`，缺車與滿站雙邊判定 |
| ② 現行系統只有即時資料，無法預測 | SageMaker DeepAR，H=6（未來 3 小時，每 30 分一格），每半小時全站批次預測 |
| ③ 無警示通知機關的功能 | 時間制四級風險燈號 + 具體調度動作（補車／取車 N 台、由哪一格起算） |

---

## 2. 系統架構

```
                 ┌──────────────┐
  baseline_grid ─│ Job A′  tick │──► level30（30 分水位歷程）
  （歷史真相）    └──────────────┘        │
                                          ▼
                                  ┌─────────────┐
                                  │  Job B      │──► SageMaker Endpoint
                                  │ batch_predict│    (DeepAR H=6)
                                  └─────────────┘
                                          │
                                          ▼
                              forecast_history（q19/q50/q90）
                                          │
   單站檢視.html ◄── FastAPI /api/v1/stations/{uid}/day ◄──┘
                     （查詢端只讀 DB，毫秒級，不打 SageMaker）
```

**核心設計決定：查詢端與推論端分離。**
Job B 每 30 分鐘把全站預測寫進 `forecast_history`，前端查詢只讀 DB。
`POST /predict` 的即時推論保留給 what-if 與交叉驗證，頁面不使用。

### 技術選型

| 層 | 技術 |
|---|---|
| 服務 | Python 3.12 + FastAPI + uvicorn（uv 管相依） |
| 資料庫 | PostgreSQL 17（podman 容器 `youbike-pg`，port 5433，由 `docker/docker-compose.yml` 起） |
| 模型 | AWS SageMaker DeepAR，`ap-northeast-1` |
| 資料源 | `baseline_grid`（歷史數據重採樣成 30 分格，2026-04~07）|
| 前端 | 單檔 HTML（`meet/20260831/單站檢視.html`），`file://` 直開 |

---

## 3. 模型

`youbike-deepar-demo2604-20260831-084604`

| 項目 | 值 |
|---|---|
| 演算法 | DeepAR（negative-binomial） |
| prediction_length | 6 格 = 3 小時 |
| context_length | 48 格 = 過去 24 小時 |
| 超參 | epochs 80 / lr 5e-4 / cells 40 / layers 2 / dropout 0.1 / batch 64 |
| 特徵 | `feat_static_cat`（站別，cardinality 1550）+ `num_dynamic_feat=1`（is_holiday） |
| 訓練集 | 2025-08-01 ~ 2026-03-31（8 個月、1,528 站） |
| 測試集 | 2026-04 整月、119 個原點 |
| 成績 | **test wQL 0.19688 / RMSE 4.5235** |

### 推論 payload 的四條硬規則（每條都踩過）

1. `target` 給滿 48 格，不是只給當下 1 格 —— 只餵 1 格會退化成查表，MAE 1.78 → 4.42，且不報錯
2. `start` 是序列起點，不是預測起點 —— 偏移了也不報錯
3. `dynamic_feat` 推論時長度 = `len(target) + H` = 54
4. 缺格填 `null`，不是 `0` —— `0` 代表真的沒車

---

## 4. 風險判定與調度建議

### 門檻

```
T = max(2, int(0.15 × 車柱 + 0.5))     # 不封頂，99 柱大站 T = 15
缺車風險：avail       <= T
滿站風險：capacity-avail <= T
```

為什麼不是純比例 20%：實測會標紅 31.4% 的格子，警報疲勞、沒有資訊量。
為什麼不是純絕對 ≤2：對 99 柱大站太鬆（滿站命中站 capacity 中位數 15 vs 全市 25）。

### 分級（時間制，一律看 q50）

| 等級 | 條件 |
|---|---|
| 高 | 現況實測已越線 **且** 近 1 小時（2 格）仍全部越線 |
| 中 | 1 小時內會越線 |
| 低 | 1 ~ 3 小時內會越線 |
| 無 | 整段預測都不越線 |

等級講「**多快會發生**」而不是「多確定會發生」—— 對調度人員可操作。
三分位降級成信心註記 `confidence = almost_certain / likely / possible`。
副效果：q50 判定讓同一格雙邊觸發在數學上不可能（分位制下有 115 站是假的雙邊）。

### 調度台數

目標不是「剛好脫離紅區」，是「**回到這站這個時段的常態水位**」：

```
補車台數 = ceil(max(現況缺口, 窗內六格缺口平均))     缺口 = 歷史同時段平均 − q50
取車台數 = 反向
```

依據 `hackathon_backend_station_slot_average`（站 × 平日假日 × 48 時刻的歷史平均，
145,635 列）。**只取 `is_observed=1`** —— baseline_grid 1,965 萬格裡真觀測只有 740 萬
(37.7%)，其餘是 carry-forward 補值，算進平均等於同一筆觀測投票幾千次。
切平日／假日的實證：同站 08:00 平日均 19.9 / 假日 9.4，18:00 平日 1.6 / 假日 5.7。

**為什麼不比「最嚴重那一格」**：模型已經把自然消退算進預測了，調度只該搬
「三小時後仍然不會自己消退的那部分」。origin 17:00 實測 —— 取車 12 站，
比最嚴重格算出 113 台、比窗尾 49 台、本算法 20 台，其中 3 站算出 0 台
（尖峰卡住但會自己退燒，派車是白跑）。

---

## 5. API

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/v1/healthz` | 系統時間、endpoint 名、排程開關、資料進度 |
| GET | `/api/v1/towns` | 29 個行政區與站數 |
| GET | `/api/v1/stations[?town_code=18]` | 站表（全量 1,538 站約 330 KB，前端開頁抓一次） |
| GET | `/api/v1/stations/{uid}` | 單站基本資料 |
| GET | `/api/v1/stations/{uid}/day` | **主要查詢**：9h 實況 + 3h 預測 + 風險 + 調度建議（只讀 DB） |
| POST | `/api/v1/predict` | 即時推論（打 SageMaker；支援 `is_holiday` what-if） |

```bash
curl -s "http://127.0.0.1:8000/api/v1/stations/NWT500218133/day" | python3 -m json.tool
```

`day` 回應含 18 格實況（缺格 carry-forward 並標 `carried=true`，前端畫空心點，
線連續但不謊稱實測）+ 6 格預測（q19/q50/q90）+ `risk`（threshold / shortage / full /
overall / dispatch）+ `truth`（對答案用，demo 回放時給 baseline 真值）+ 模型卡 + caveats。

---

## 6. 排程

`jobs/demo.py --run` 是**常駐前景迴圈**，demo 期間唯一該推進虛擬時鐘的東西。
睡「距下一格的真實時間」但不超過 2 秒，判定 `floor(now, 30min) > current_slot`
才推進；推進一格做四件事：

| 步 | 做什麼 | 備註 |
|---|---|---|
| ① | `baseline_grid` → `level30`（Job A′） | 落後多格一次搬齊 |
| ② | 有預測 → 快轉通過；沒有 → **先降 ×1** 再現算 | ⚠ 降速一定在打 endpoint 之前，否則預測寫進去就過期 |
| ③ | 清該 origin 的 `risk_snapshot`，**無條件**重判 | 不走冪等跳過 |
| ④ | 用本輪 origin 的風險現況收調度單 | ③失敗才退回用最新一輪 |

判定不成立就什麼都不印 —— 否則 log 一天會多幾萬行廢話。
「迴圈還活著嗎」看 `sys_config.last_tick` 與 `demo_loop_lease`，不看 log。

### ★ 2026-09-12：tick / cron 退場，流速搬進 DB

出處：`meet/20260912/計劃-demo回放邏輯重整.md`。三件事：

1. **流速**由環境變數 `DEMO_SPEED` 改為 `sys_config.demo_speed`。舊制每個進程
   各讀各的，對著同一個 DB 算出差好幾倍的「現在」。調速只有一條路：
   `uv run python -m jobs.demo --speed N`（它會先重新錨定 `demo_t0_*`，時鐘不跳）。
2. **推進迴圈**從 cron + `jobs/tick.py` 搬到 `jobs/demo.py --run`。tick.py 已刪除、
   `docker-entrypoint.sh` 的 `RUN_TICK` 背景迴圈已移除 —— **後端啟動只提供 API**。
   單一實例靠 DB 租約 `sys_config.demo_loop_lease`（檔案鎖擋不到另一個容器）。
   ⚠ 裝過舊 crontab 的機器要 `crontab -r`，否則會有兩個迴圈同時推時鐘而**不報錯**。
3. **現算中**寫 `sys_config.demo_predicting`，前端據此上全頁遮罩「最新資料載入中」。
   有 10 分鐘 TTL 自癒，迴圈被 kill 也不會讓遮罩卡死。

### ★ 2026-09-04：TDX 拉取邏輯已移除

Job A（即時 API）／Job C（歷史 API 自癒回補）／`sync_stations`（主檔同步）三支與
`app/tdx/` client 全部刪除，連帶 `actual_history` 表、週期補值、TDX 用量護欄與點數對帳。
唯一的資料來源是 `baseline_grid`，站點主檔改為手動匯入維護。
**回放迴圈只在 demo 模式下有事做** —— 非 demo 時 `--run` 直接拒絕啟動。
出處：`meet/20260904/計劃-移除TDX拉取邏輯.md`。

---

## 7. 快速開始

```bash
cd code_backend

# 1. 資料庫 —— 第一次 up 就會自己把 docker/bak/*.dump 灌進去（要數分鐘）
#    表結構、來源資料、level30 歷史、cat 對照全都在 dump 裡，不用再跑任何 sql/
podman-compose -f ../docker/docker-compose.yml up -d
podman-compose -f ../docker/docker-compose.yml logs -f          # 看還原進度

# ★ 灌完了沒看 healthy，不要看 Up —— initdb 階段的暫時 server 也會回 pg_isready
podman inspect --format '{{.State.Health.Status}}' youbike-pg

# 2. SageMaker endpoint
uv run python aws/deploy_endpoint.py

# 3. 起服務（DB 參數全對齊 app/config.py 預設，不用設環境變數）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
curl -s http://127.0.0.1:8000/api/v1/healthz | python3 -m json.tool

# 4. 回放（★ 不要裝 crontab，2026-09-12 起那份已經沒有內容）
uv run python -m jobs.demo --start --speed 30    # 設虛擬時鐘與流速
uv run python -m jobs.demo --run                 # 常駐迴圈（前景，Ctrl-C 收工）
```

★ 換一份新的 dump 時**一定要先 `down -v`** —— volume 非空的話官方 postgres image
會整個跳過 initdb，新 dump 一行都不會灌，而且不報錯：

```bash
podman-compose -f ../docker/docker-compose.yml down -v
podman-compose -f ../docker/docker-compose.yml up -d
```

### 從最上游重建（只有明勳本機能跑）

`sql/` 那串是「沒有 dump、要從原始 pgdump 重建」時才走的路，需要
`/Volumes/myPro/pgdump-20260827.sql.gz`（95,399,918 行）。平常拿到 dump 的人不必跑：

```bash
bash sql/10_restore_source.sh                    # ★ 它自己 podman run 建容器，見下方警告
psql -f sql/20_backend_ddl.sql                   # 建 4 張服務用表
bash sql/30_load_cat_map.sh                      # ★ 灌 cat 對照表（換模型必跑）
bash sql/31_load_proxy_cat.sh                    # 鄰站 cat 代理
psql -f sql/40_scheduler_tables.sql -f sql/41_sys_config.sql -f sql/50_station_slot_average.sql
psql -v ON_ERROR_STOP=1 -f sql/42_level30_is_imputed.sql -f sql/43_level30_carry.sql
```

⚠️ `10_restore_source.sh` 內含 `podman rm -f youbike-pg` —— 它會**強制刪掉 compose
起的那座容器**。跑之前先確認你真的要重建，不是只想連 DB。

前端：瀏覽器直接開 `meet/20260831/單站檢視.html`（右上角可改 API 位址）。

完整指令請見 **[COMMANDS.md](COMMANDS.md)**。

### demo 回放模式

現場不依賴任何外部 API —— 用虛擬時鐘回放 2026-05 的歷史（模型訓練只到 2026-03-31，
回放時等於沒看過答案，可以當場「對答案」）：

```bash
uv run python -m jobs.demo --start --reset      # 清既有預測 + 設虛擬時鐘
uv run python -m jobs.demo --run                # ★ 開迴圈（唯一的時鐘推進者）
uv run python -m jobs.demo --speed 1            # 迴圈跑著也能改（另開終端）
uv run python -m jobs.demo --status             # 虛擬時刻／回放進度／迴圈租約
uv run python -m jobs.demo --stop               # 收工
```

虛擬時間起點 `2026-05-01 08:00`，預設流速 5（真實 1 分鐘 = 虛擬 5 分鐘，一格 30 分 = 真實 6 分鐘）。
迴圈一律走 Job A′（從 baseline_grid 逐格搬）—— 2026-09-04 起這是唯一的資料來源。
走到沒有預先算好的格時會自動降回 ×1 現算，**算完不自動回快轉**（9/12 定案）。

---

## 8. 目錄結構

```
backend/
├── app/
│   ├── main.py              FastAPI 進入點（router 註冊、CORS、錯誤處理）
│   ├── config.py            ★ 常數集中地，每個值都註明來源
│   ├── controller/          路由層（station / predict / assistant / health）
│   ├── service/             業務層（overview 風險判定／predict payload／station）
│   ├── repository/          資料層（每張表一支）
│   └── schema/              Pydantic DTO
├── jobs/
│   ├── tick.py              ★ 每分鐘輪詢，排程入口
│   ├── replay_pull.py       Job A′：baseline_grid → level30（唯一資料來源）
│   ├── batch_predict.py     Job B：全站批次預測（50 站一批）
│   ├── predict_range.py     一段 origin 逐輪批打（歷史區補預測）
│   ├── demo.py              demo 時鐘 start/stop/status
│   ├── run_job.sh           wrapper：鎖 + log（cron 走這支）
│   └── crontab.txt          cron 設定（由使用者自行安裝）
├── sql/                     建置腳本 10 ~ 50
├── aws/deploy_endpoint.py   SageMaker Model → Config → Endpoint
└── COMMANDS.md              指令速查
```

資料表（皆 `hackathon_backend_` 前綴，與訓練管線的表切開）：
`station` / `town` / `level30` / `calendar` / `forecast_history` / `forecast_run` /
`risk_snapshot` / `job_run` / `sys_config` / `station_slot_average`

---

## 9. 已知限制

誠實記錄，簡報時不迴避：

- **q\* 未逐格校準**　`q=0.19` 是 H=4 校準出來的值，H=6 的 `q*_1~q*_6` 尚未校準。
  預測愈遠不確定性愈大，6 格共用一個 q\* 會系統性低估缺車 —— 所以每個回應都帶 `CAVEATS`。
- **門檻未用真值驗證**　15% 這個數字有實測觸發率支撐（缺車 26.3%／滿站 3.3%），
  但沒拿 baseline 真值算過精確率／涵蓋率／提前量，也沒掃 5~20% 找最佳點。
- **需求在水位資料上隱形**　`avail` 永遠 ≥ 0，「來了沒車、走人」看不出來。
  22:30 中位數 0 分不出「沒需求」與「每晚借光還撲空」，要租借事件流才分得開。
- **結構性誤報**　歷史常態本來就低於 T 的站（板橋站 86 柱夜間常態 3.5、景安站 96 柱常態 1.8）
  每晚必亮燈，是否加註記未拍板。
- **cat 對照表是最危險的一步**　cat 編號由訓練時的字典序決定，錯了不會報錯 ——
  每一站都拿到別站的預測，數字看起來完全合理。換模型必跑 `sql/30_load_cat_map.sh`。
- **資料品質**　2026-07-10 上午餵食故障，07:00 那個快照全表 1,572 站 `avail` 全部 = 0
  （清晨全空城不可能，是謊報）。約 1.17 萬個謊報格，佔全表 0.04%，訓練影響可忽略但已記錄在案。

---

## 10. 成本與收尾

🔴 **demo 結束務必刪 SageMaker endpoint**（`ml.m5.large` 按秒計費）：

```bash
aws sagemaker delete-endpoint --endpoint-name youbike-deepar-demo2604 --region ap-northeast-1
aws sagemaker list-endpoints --region ap-northeast-1     # 確認回空
```

只有 endpoint 計費；endpoint-config 與 model 是中繼資料，零元。
模型檔在 S3（全 bucket 36 物件 177 MB，每月不到 US$0.005）。

賽後主辦會暫停 AWS 帳號權限且不負責保管資料 —— Day2 收尾必須排備份時段
（程式碼、模型、資料、截圖）。
