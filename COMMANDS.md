# 指令速查

所有指令都在 `backend/` 目錄下執行（除非另有註明）。
在 Claude Code 對話中可以用 `!` 前綴直接跑，例如 `!uv run python -m jobs.demo --status`。

```bash
cd /Volumes/myPro/codes/race/youbike-hackathon/backend
```

---

## 1. 後端服務

```bash
# 啟動（--reload = 改 code 自動重載，開發時建議帶）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# ★ demo 回放中要帶 DEMO_SPEED（與 tick 同值）——理由見 §2「流速的坑」
DEMO_SPEED=30 uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 停掉正在跑的
pkill -f "uvicorn app.main:app"

# 確認活著（demo 模式時 now 會是虛擬時間）
curl -s http://127.0.0.1:8000/healthz | python3 -m json.tool
```

⚠️ `--reset` 是 `jobs.demo` 的參數，**不是 uvicorn 的**。

### API 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/healthz` | 系統時間、endpoint 名、排程開關、資料進度 |
| GET | `/api/v1/towns` | 29 個行政區與站數 |
| GET | `/api/v1/stations` | 全量站表（1,538 站，約 330 KB） |
| GET | `/api/v1/stations?town_code=18` | 限單一行政區 |
| GET | `/api/v1/stations/{uid}` | 單站基本資料 |
| GET | `/api/v1/stations/{uid}/day` | **主要查詢**：9h 實況 + 3h 預測 + 風險判定（只讀 DB） |
| POST | `/api/v1/predict` | 即時推論（會打 SageMaker，頁面已不使用） |

```bash
# day API
curl -s "http://127.0.0.1:8000/api/v1/stations/NWT500218133/day" | python3 -m json.tool

# 即時推論（at 可省略 = 該站最末格；is_holiday 可做 what-if）
curl -s -X POST http://127.0.0.1:8000/api/v1/predict \
  -H 'Content-Type: application/json' \
  -d '{"station_uid":"NWT500218133"}' | python3 -m json.tool
```

---

## 2. demo 回放模式

### 前置：灌歷史資料（一次就好，9/2 新增）

`demo.py` **不再預載 level30**。歷史區（2026-04-01 ～ 08-01）的單一出處是
一支 SQL，跑一次就好，重跑也安全（它自己 DELETE 再全量重灌）：

```bash
PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
  -v ON_ERROR_STOP=1 -f backend/sql/43_level30_carry.sql
```

約 40 秒，寫入 9,024,550 列 / 1,585 站。跑完 `--start` 會自己檢查
context 在不在，空的就擋下來並提示跑這一行。

★ 這支把 `baseline_grid` 裡連缺 3 小時以上的洞**全部 carry 補滿**
（792,237 格，標 `is_imputed=2`）。代價已知並照收：斷訊站與「水位真的沒動」
在資料層分不開了，斷線站會照常進風險清單、照常叫車。
決策全文見 `meet/20260902/計劃-level30灌歷史與無限carry.md`。

```bash
uv run python -m jobs.demo --status          # 虛擬時刻／回放進度
uv run python -m jobs.demo --start           # 設 demo 時鐘
uv run python -m jobs.demo --start --reset   # ★ 先清預測再起跑（會刪掉既有預測，見下）
uv run python -m jobs.demo --stop            # 清時鐘與書籤
```

`--reset` 會清掉 demo 視窗（2026-04-01 ～ 2026-06-01）的預測列，
只動 4~5 月，不碰真排程資料。

⚠️ **`--reset` 會刪掉 `forecast_history` 的 4~5 月列。**
已經花錢跑好的預測要留著重播，就用不帶 `--reset` 的 `--start`。

⚠️ **`--reset` 與 `--purge` 都不再碰 `level30`**（9/2 決策 13）。
歷史區是「真相的重採樣」不是 demo 狀態，reset demo 不該動它 ——
要重建就跑上面那支 43。`--purge` 已移除。

### 純重播：不打 endpoint、跑到某一刻停表（9/1 新增）

已經有整段預測、只想再放一次給人看時用。全程零 endpoint 開銷。

| 鍵 | 值 | 作用 |
|---|---|---|
| `replay_predict` | `0` | Job A′ 只搬 `baseline_grid`，**不觸發 Job B** |
| `demo_until` | 時刻 | `effective_now()` 到點就夾住，時鐘停表 |

兩鍵未設定 = 維持原本行為（會觸發 Job B、一路跑下去）。

```bash
uv run python -m app.repository.sys_config_repo --set virtual_now -            # ① 靜態時鐘優先序比 demo 高，不清起不來
uv run python -m app.repository.sys_config_repo --set replay_predict 0         # ② 不打 endpoint
uv run python -m app.repository.sys_config_repo --set demo_until '2026-05-02 00:00:00'
uv run python -m jobs.demo --start                                            # ③ ★ 不可加 --reset（level30 已由 43 灌好，這步不再預載）
uv run python -m app.repository.sys_config_repo --set forecast_end '2026-05-02 03:00:00'   # ④ Job B 不跑就沒人寫它
uv run python -m app.repository.sys_config_repo --set scheduler_on 1           # ⑤
export DEMO_SPEED=30 && while true; do bash jobs/run_job.sh tick; sleep 60; done   # ⑥
```

④ 的值 = 最末 origin + 6 格（30 分一格）。不設的話 `/healthz` 的
`forecast_left_min` 會是 null。

到終點後 `--status` 的模式那行會變成 `⏸ 已到終點，時鐘停表中`，
tick 每輪判定不落後、安靜離開。

⚠️ **不要改用 `ENDPOINT_MOCK=1` 達成「不打 endpoint」**：
`batch_predict.py` 寫入是 `ON CONFLICT DO UPDATE`，mock 預測會逐格
覆蓋掉既有的真預測。

### 虛擬時鐘

| 設定 | 值 | 位置 |
|---|---|---|
| 起點 | `2026-05-01 08:00:00` | `config.DEMO_VIRTUAL_T0` |
| 流速 | `5`（真實 1 分鐘 = 虛擬 5 分鐘） | `config.DEMO_SPEED` |

⚠️ **改流速前必須先重新錨定** `demo_t0_virtual` / `demo_t0_real` 到當前虛擬時刻，
否則 `now = t0v + 經過時間 × 速度` 會讓虛擬時間瞬間跳走。

#### ⚠️ 流速的坑：`DEMO_SPEED` 是環境變數，不是 DB 鍵

每個進程各讀各的。`export DEMO_SPEED=30` 只對那個 shell 底下的 tick 生效 ——
uvicorn、另開終端下的 `--status`、crontab 跑的 tick，全都會退回預設 `5`，
**同一個 DB 卻算出差好幾倍的「現在」**，前端看起來就像卡住不動。

demo 期間每個要問「現在幾點」的進程都得帶同一個值：

```bash
DEMO_SPEED=30 uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
DEMO_SPEED=30 uv run python -m jobs.demo --status
export DEMO_SPEED=30 && while true; do bash jobs/run_job.sh tick; sleep 60; done
```

（crontab 讀不到 shell 的 export —— 要調流速跑 demo，用前景迴圈，別用 cron。）

```bash
# 看時鐘三鍵
uv run python -m app.repository.sys_config_repo
# 手動設一個鍵
uv run python -m app.repository.sys_config_repo --set demo_t0_virtual '2026-05-01 09:00:00'
```

---

## 3. 排程 job

### tick（每分鐘輪詢，cron 的入口）

```bash
uv run python -m jobs.tick            # 兩條判定並執行
uv run python -m jobs.tick --status   # 只印狀態，不做事
uv run python -m jobs.tick --force    # 無視判定強制跑一輪
```

demo 模式下 tick 會走 **Job A′（replay_pull）**，並自動停用 Job C。
`--status` 在 demo 中會多印「回放終點」與「回放觸發 JobB」兩行。

`sys_config.replay_predict = 0` 時 Job A′ 不觸發 Job B（見 §2 純重播）。

### 手動跑單一 job

```bash
# Job A′：從 baseline_grid 搬一格到 level30（demo 回放用）
uv run python -m jobs.replay_pull

# Job B：批次預測，寫 forecast_history
uv run python -m jobs.batch_predict                        # 全站
uv run python -m jobs.batch_predict --limit 50             # 只跑 50 站
uv run python -m jobs.batch_predict --slot '2026-05-01 20:30'
uv run python -m jobs.batch_predict --dry-run              # 只組 payload 不打

# Job A：拉 TDX 即時水位　★ demo 模式下有斷路器，會直接拒絕
uv run python -m jobs.pull_realtime --dry-run

# Job C：歷史 API 回補　★ 會扣 TDX 點數（月上限 150）
uv run python -m jobs.backfill --status     # 只印判定與缺格率
uv run python -m jobs.backfill --dry-run    # 印日期區間/URL，不真打
uv run python -m jobs.backfill --days 3     # 縮小視窗

# 站點主檔同步　★ 會打一次全量 TDX
uv run python -m jobs.sync_stations --dry-run
uv run python -m jobs.sync_stations --force-proxy   # 沒新站也重算代理
```

### 透過 wrapper 跑（有鎖、有 log、cron 也用這個）

```bash
bash jobs/run_job.sh tick
bash jobs/run_job.sh batch_predict --limit 50
# log：backend/logs/<job>-YYYY-MM-DD.log（保留 14 天）
```

### 讓排程持續跑

```bash
# 方式一：前景迴圈（關掉終端就停）
while true; do bash jobs/run_job.sh tick; sleep 60; done

# 方式二：crontab（macOS 需給 /usr/sbin/cron 完全磁碟取用權限）
crontab -e   # 內容參考 jobs/crontab.txt
```

⚠️ 排程沒在跑 = 資料不會前進、不會有新預測。「為什麼沒有新預測」問過三次，
每次都是這個原因。先用 `uv run python -m jobs.tick --status` 確認。

---

## 4. 資料庫

PostgreSQL 17 跑在 podman 容器 `youbike-pg`（對外 port 5433，db `youbike`）。

```bash
# 容器狀態
podman ps
podman start youbike-pg

# ★ podman machine 睡眠後常假死：list 顯示 running 但 socket 拒連
podman machine stop && podman machine start && podman start youbike-pg

# 進 psql
podman exec -it youbike-pg psql -U youbike -d youbike

# 單句查詢
podman exec youbike-pg psql -U youbike -d youbike -qc "SELECT count(*) FROM hackathon_backend_station;"
```

### 常用查詢

```sql
-- 回放進度與時鐘
SELECT * FROM sys_config ORDER BY key;

-- 最近的 job 執行紀錄
SELECT job, slot, status, note, created_at FROM job_run ORDER BY created_at DESC LIMIT 20;

-- 最新一輪預測原點
SELECT max(origin) FROM hackathon_backend_forecast_history;

-- 某站的實況
SELECT slot, avail, docks FROM hackathon_backend_level30
 WHERE station_uid = 'NWT500218133' ORDER BY slot DESC LIMIT 20;
```

⚠️ `VACUUM` 不能包在交易裡 —— `psql -c` 塞多句會被包成一個交易而失敗，
要一句一個 `-qc`：

```bash
for t in hackathon_backend_level30 hackathon_backend_forecast_history; do
  podman exec youbike-pg psql -U youbike -d youbike -qc "VACUUM FULL ANALYZE $t;"
done
```

---

## 5. 建置腳本（SQL）

```bash
bash sql/10_restore_source.sh          # 從 dump 還原來源資料（很久）
psql -f sql/20_backend_ddl.sql         # 建後端用的表
bash sql/30_load_cat_map.sh            # ★ 灌 cat 對照表（換模型必跑）
bash sql/31_load_proxy_cat.sh --verify # 只查代理現況，不寫 DB
bash sql/31_load_proxy_cat.sh          # 重算鄰站代理
```

⚠️ **換模型時 `30_load_cat_map.sh` 是最危險的一步**：cat 編號由訓練時的字典序
決定，錯了不會報錯 —— 每一站都拿到別站的預測，數字看起來完全合理。
腳本已內建「先全清再灌」，掉出新對照表的站會回到 `cat = NULL`。

---

## 6. AWS SageMaker

```bash
uv run python aws/deploy_endpoint.py    # 建 endpoint（Model → Config → Endpoint）
aws sagemaker describe-endpoint --endpoint-name youbike-deepar-demo2604 \
  --region ap-northeast-1 --query 'EndpointStatus'
```

🔴 **demo 結束務必刪 endpoint**（`ml.m5.large` 按秒計費）：

```bash
aws sagemaker delete-endpoint --endpoint-name youbike-deepar-demo2604 --region ap-northeast-1
aws sagemaker list-endpoints --region ap-northeast-1     # 確認回空
```

**只有 endpoint 計費。** endpoint-config 與 model 是中繼資料，零元，
不必刪；模型檔放在 S3（全 bucket 36 物件 177 MB，每月不到 US$0.005）。
要清理的話名字別抄錯 —— config 有 `-config` 後綴，model 沒有：

```bash
aws sagemaker delete-endpoint-config --endpoint-config-name youbike-deepar-demo2604-config --region ap-northeast-1
aws sagemaker delete-model           --model-name           youbike-deepar-demo2604        --region ap-northeast-1
```

⚠️ 用 `&&` 串三段會斷在中間那段（8/31 實際踩過：endpoint 刪掉了、
config 因名字不符失敗、model 因此也沒刪）。先跑 `list-endpoint-configs`
`list-models` 確認名字再串。

💡 費率查證不到 —— IAM user `charles-cli` 沒有 `pricing:GetProducts`
權限，要看實際金額只能進 Billing Console / Cost Explorer。

---

## 7. git

```bash
git status --short
git add -A && git commit -m "訊息"
git log --oneline
```

⚠️ **只在 `backend/` 版控**（跟 `ml-deepar/`、`ml-xgboost/` 一樣各自一個 repo）。
專案根目錄不可 `git init` —— `raw/` 21 GB、`data/` 298 MB、`.venv*` 595 MB。

`.gitignore` 已擋掉 `.env`（TDX 憑證）、`.cache/`（token 快取）、`logs/`、`.venv/`。

---

## 8. 風險判定參數

改 `app/config.py` 這三個值，前後端會一起跟著動（有 `--reload` 時不用重啟）：

```python
RISK_PCT, RISK_MIN, RISK_MAX = 0.15, 2, None
# 門檻 T = max(2, int(0.15 × 車柱 + 0.5))，不封頂
#   缺車：可借 <= T　／　滿站：可還 <= T
```

分級（時間制，看 q50）：

| 等級 | 條件 |
|---|---|
| 高 | 現況實測已越線 **且** 近 1 小時（2 格）仍全部越線 |
| 中 | 1 小時內會越線 |
| 低 | 1～3 小時內會越線 |
| 無 | 整段預測都不越線 |

---

## 9. 前端

單站檢視頁：`meet/20260831/單站檢視.html`，用瀏覽器直接開（`file://`）。
右上角可改 API 位址，預設 `http://127.0.0.1:8000`。

顯示「離線」= 後端沒起來或 API 位址錯。
