# 指令速查

所有指令都在 `code_backend/` 目錄下執行（除非另有註明）。
在 Claude Code 對話中可以用 `!` 前綴直接跑，例如 `!uv run python -m jobs.demo --status`。

```bash
cd /Volumes/myPro/codes/race/youbike-hackathon/code_backend
```

★ **demo 期間要開兩個終端**：一個跑後端（`uvicorn`），一個跑回放迴圈
（`jobs.demo --run`，前景常駐）。調速、查狀態再另開第三個。完整流程見 §2。

---

## 1. 後端服務

```bash
# 啟動（--reload = 改 code 自動重載，開發時建議帶）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# ★ 2026-09-12 起**不必再帶 DEMO_SPEED**：流速的真相在 sys_config.demo_speed，
#   後端啟動就只是提供 API。要調速請下 `uv run python -m jobs.demo --speed N`。

# 停掉正在跑的
pkill -f "uvicorn app.main:app"

# 確認活著（demo 模式時 now 會是虛擬時間）
curl -s http://127.0.0.1:8000/api/v1/healthz | python3 -m json.tool
```

`/healthz` 的判讀線索：

| 欄位 | 怎麼看 |
|---|---|
| `now` | 連看兩次沒往前 = 時鐘停了（停表、或迴圈沒在跑） |
| `demo_speed` | 目前流速（來自 DB）。`null` = 非 demo 模式，`0` = 停表 |
| `predicting_origin` | 非 null = 後端正在現算，前端此時是全頁遮罩 |
| `data_age_min` | > 60 = 回放迴圈大概停了 |
| `forecast_left_min` | < 0 = 預測已過期，前端不該再顯示 |
| `tick_age_min` | 迴圈上次動作距今多久（迴圈每 20 秒寫一次 `last_tick`） |

⚠️ `--reset` 是 `jobs.demo` 的參數，**不是 uvicorn 的**。

### API 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/v1/healthz` | 系統時間、endpoint 名、排程開關、資料進度 |
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

### 一條龍：從零到畫面會動

```bash
# ① 資料庫在跑（healthy 不是 Up，見 §4）
podman inspect --format '{{.State.Health.Status}}' youbike-pg

# ② 歷史資料灌過了嗎（一次就好，見下一小節）
#    --start 會自己檢查，空的會擋下來並提示

# ③ 起後端（★ 不必帶任何環境變數）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# ④ 設虛擬時鐘 + 流速
uv run python -m jobs.demo --start --speed 30

# ⑤ 開回放迴圈（★ 前景，另開一個終端；這是唯一的時鐘推進者）
uv run python -m jobs.demo --run

# ⑥ 確認在動：now 連看兩次要往前跑
curl -s http://127.0.0.1:8000/api/v1/healthz | python3 -m json.tool
```

收工：迴圈那個終端按 Ctrl-C，再 `uv run python -m jobs.demo --stop`。

### 前置：灌歷史資料（一次就好，9/2 新增）

`demo.py` **不再預載 level30**。歷史區（2026-04-01 ～ 08-01）的單一出處是
一支 SQL，跑一次就好，重跑也安全（它自己 DELETE 再全量重灌）：

```bash
PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
  -v ON_ERROR_STOP=1 -f code_backend/sql/43_level30_carry.sql
```

約 40 秒，寫入 9,024,550 列 / 1,585 站。跑完 `--start` 會自己檢查
context 在不在，空的就擋下來並提示跑這一行。

★ 這支把 `baseline_grid` 裡連缺 3 小時以上的洞**全部 carry 補滿**
（792,237 格，標 `is_imputed=2`）。代價已知並照收：斷訊站與「水位真的沒動」
在資料層分不開了，斷線站會照常進風險清單、照常叫車。
決策全文見 `meet/20260902/計劃-level30灌歷史與無限carry.md`。

### 時鐘開關

```bash
uv run python -m jobs.demo --status           # 虛擬時刻／回放進度／迴圈租約／現算中
uv run python -m jobs.demo --start            # 設 demo 時鐘
uv run python -m jobs.demo --start --speed 30 # 起跑就指定流速
uv run python -m jobs.demo --start --reset    # ★ 先清預測再起跑（會刪掉既有預測，見下）
uv run python -m jobs.demo --speed 1          # ★ 迴圈跑著也能改，另開終端下即生效
uv run python -m jobs.demo --stop             # 清時鐘與書籤
```

`--reset` 會清掉 demo 視窗（2026-04-01 ～ 2026-06-01）的預測列，
只動 4~5 月，不碰真排程資料。

⚠️ **`--reset` 會刪掉 `forecast_history` 的 4~5 月列。**
已經花錢跑好的預測要留著重播，就用不帶 `--reset` 的 `--start`。

⚠️ **`--reset` 與 `--purge` 都不再碰 `level30`**（9/2 決策 13）。
歷史區是「真相的重採樣」不是 demo 狀態，reset demo 不該動它 ——
要重建就跑上面那支 43。`--purge` 已移除。

★ `--start` 與 `--stop` 都會清掉 `demo_predicting` 兩鍵 —— 上一場的殘留
會讓前端一開場就是遮罩。

---

### ★ 回放迴圈 `--run`（2026-09-12 起）

```bash
uv run python -m jobs.demo --run             # 常駐前景迴圈（Ctrl-C 收工）
uv run python -m jobs.demo --run --force     # 搶走別人的租約（確定那份已經死了才用）
uv run python -m jobs.demo --step            # 只推進一格就離開（除錯用，不搶租約）
```

**demo 期間唯一該推進虛擬時鐘的東西。** 睡「距下一格的真實時間」但不超過 2 秒
—— cron 的最小粒度 1 分鐘，×30 快轉下一輪就跨過一整格，逐格判定會失效，
所以這條路不能用 cron。

每跨一格做四件事，順序固定：

| 步 | 做什麼 | 備註 |
|---|---|---|
| ① | `baseline_grid` → `level30`（Job A′） | 落後多格一次搬齊 |
| ② | 這格有預測 → 快轉通過；沒有 → **先降 ×1** 再現算 | ⚠ 降速一定在打 endpoint 之前 |
| ③ | 清該 origin 的 `risk_snapshot`，**無條件**重判 | 不走 `_maybe_risk` 的冪等跳過 |
| ④ | 用本輪 origin 的風險現況收調度單 | ③失敗才退回用最新一輪 |

②③④任一步失敗只印警告，迴圈照樣跑下一輪。①失敗（覆蓋率不足）則跳過②③④，
書籤仍會前進 —— 不然每 2 秒重試同一個壞 slot，永遠卡住。

★ `job_run` 一輪固定兩列（`pull_replay`、`dispatch_sweep`）；`batch_predict`
只在②真的現算時才有一列，③的風險判定不另記 job_run。

實跑長這樣：

```
── 推進 2026-05-01 12:00:00.266｜應拉 12:00:00｜實拉 11:30:00｜落後 0:30:00
   回放 12:00:00 → 本格 1527 列（主檔 1538 站，門檻 80% = 1230）
✓ Job A′ success（stations_ok=1527）
   ② 預測：已有，快轉通過（不打 endpoint）
   ③ 風險：清 1,538 列後重判 1538 站（高 448／中 82／低 101／無 896／無預測 11）
   ④ 調度：檢查 3 筆 → 完成 0／失效 1／續留 2
```

什麼都沒做的那些輪**完全不印** —— 每 2 秒醒一次，有輸出才有意義。
「迴圈還活著嗎」看 `sys_config.last_tick`（20 秒寫一次）與 `demo_loop_lease`。

#### 降速與全頁遮罩

②走到沒有預先算好的格時，先把時鐘降回 ×1 **再**打 endpoint。順序不能顛倒 ——
1,600 站的批次預測真實要跑數十秒，×30 下虛擬時鐘已經跳過十幾小時，
預測會掛在一個早就過去的 origin 上，前端拿不到，畫面空白。

降速之後**不會自動回快轉**（9/12 定案）。要回快轉：另開終端 `--speed 30`。

現算期間後端寫 `sys_config.demo_predicting`，`/healthz` 的
`predicting_origin` 跟著非 null，前端據此上**全頁遮罩「最新資料載入中」**。
遮罩有兩道自癒，不會卡死：

- `--run` 啟動時先清一次殘留
- `predicting_since` 距真實現在超過 10 分鐘，`/healthz` 就回 null

#### 單一實例：DB 租約

靠 `sys_config.demo_loop_lease`（值 = `pid@host|到期真實時間`，TTL 900 秒、每輪續約）。
**不是檔案鎖** —— 檔案鎖擋不到另一個容器或另一台機器。重複啟動會被擋：

```
✗ 已經有一個回放迴圈在跑：36169@zhengmingxundeMacBook-Pro.local（租約到 18:28:06）
  確定那份已經死了 → uv run python -m jobs.demo --run --force
```

⚠️ **舊的 crontab 一定要移除**：

```bash
crontab -l      # 先看裝了什麼
crontab -r      # 整份移除（jobs/crontab.txt 已清空，沒有東西要裝）
```

`jobs/tick.py` 已刪除。沒移除的話輕則每分鐘一次 ModuleNotFoundError，
重則舊 tick 與 `--run` **同時推時鐘** —— job_run 同 slot 兩列、endpoint 被打兩次，
**完全不會報錯**。租約擋得住第二個 `--run`，擋不住舊版的 tick。

#### 要看 log 就導向檔案

```bash
uv run python -m jobs.demo --run 2>&1 | tee logs/demo-$(date +%F).log
```

★ 不要用 `bash jobs/run_job.sh` 跑它 —— 那支的 log 是「跑完才寫檔」，
常駐進程等於永遠不寫。（`--run` 本身已改成行緩衝，導向檔案看得到即時進度。）

---

### 調流速

| 設定 | 值 | 位置 |
|---|---|---|
| 起點 | `2026-05-01 08:00:00` | `config.DEMO_VIRTUAL_T0` |
| 流速 | 預設 `5`（真實 1 分鐘 = 虛擬 5 分鐘） | **`sys_config.demo_speed`**（DB） |

```bash
uv run python -m jobs.demo --speed 30    # ★ 調速只有這一條路
```

#### ★ 2026-09-12：流速搬進 DB 了

舊版這裡寫著「流速的坑」—— `DEMO_SPEED` 是環境變數，每個進程各讀各的，
uvicorn／jobs／另開終端的 `--status` 對著同一個 DB 算出差好幾倍的「現在」，
畫面看起來就像卡住。那個坑已經填掉：

- 真相在 `sys_config.demo_speed`，所有進程讀同一個值
- `config.DEMO_SPEED` 降級成「那個鍵沒設定時的預設值」
- 後端啟動**不必再帶任何環境變數**

`--speed` 會**先重新錨定** `demo_t0_virtual` / `demo_t0_real` 到當前虛擬時刻再寫值 ——
少了這一步，`now = t0v + 經過時間 × 速度` 會在改速度那一瞬間跳走
（×5 改 ×30、已跑 10 分鐘真實 → 虛擬時間當場往前噴 4 小時）。實測：

```
✓ 流速 ×5 → ×30（已重新錨定，虛擬時間停在 2026-05-01 11:34:26 沒有跳）
  2 秒後虛擬走了 60.1 秒　← ×30 正確
```

⚠️ 所以**不要**用 `sys_config_repo --set demo_speed` 直接寫那個鍵 ——
那條路不會重新錨定。已經在程式裡擋下來並提示改用上面那行。

`--speed 0` = 停表（時鐘不走，迴圈不推進任何一格）。調度入口在 `demo_speed > 1`
時**禁用**（0 與 1 都放行，見 `交件註記-調度確認.md` §3）。

### 純重播：不打 endpoint、跑到某一刻停表（9/1 新增）

已經有整段預測、只想再放一次給人看時用。全程零 endpoint 開銷。

| 鍵 | 值 | 作用 |
|---|---|---|
| `replay_predict` | `0` | 迴圈第②步不現算也不降速，只印一行警告 |
| `demo_until` | 時刻 | `effective_now()` 到點就夾住，時鐘停表 |

兩鍵未設定 = 維持原本行為（會現算、一路跑下去）。

```bash
uv run python -m app.repository.sys_config_repo --set virtual_now -            # ① 靜態時鐘優先序比 demo 高，不清起不來
uv run python -m app.repository.sys_config_repo --set replay_predict 0         # ② 不現算
uv run python -m app.repository.sys_config_repo --set demo_until '2026-05-02 00:00:00'
uv run python -m jobs.demo --start                                            # ③ ★ 不可加 --reset（level30 已由 43 灌好，這步不再預載）
uv run python -m app.repository.sys_config_repo --set forecast_end '2026-05-02 03:00:00'   # ④ 沒現算就沒人寫它
uv run python -m app.repository.sys_config_repo --set scheduler_on 1           # ⑤
uv run python -m jobs.demo --speed 30                                         # ⑥ 流速在 DB，不用 export
uv run python -m jobs.demo --run                                              # ⑦ 開迴圈
```

④ 的值 = 最末 origin + 6 格（30 分一格）。不設的話 `/api/v1/healthz` 的
`forecast_left_min` 會是 null。

到終點後 `--status` 的模式那行會變成 `⏸ 已到 demo_until，停表中`，
迴圈每輪判定不落後、安靜離開。

⚠️ **不要改用 `ENDPOINT_MOCK=1` 達成「不打 endpoint」**：
`batch_predict.py` 寫入是 `ON CONFLICT DO UPDATE`，mock 預測會逐格
覆蓋掉既有的真預測。

### sys_config 鍵一覽（demo 相關）

| 鍵 | 誰寫 | 作用 |
|---|---|---|
| `demo_t0_real` / `demo_t0_virtual` | `--start`、`--speed` | 時鐘錨點，**兩鍵成對**才算 demo 生效 |
| `demo_speed` | `--speed` | 流速。★ 只能透過 `--speed` 改（會重新錨定） |
| `demo_until` | 人 | 虛擬時鐘終點，到點停表 |
| `replay_predict` | 人 | `0` = 第②步不現算也不降速 |
| `demo_predicting` / `demo_predicting_since` | 迴圈 | 現算中 → 前端全頁遮罩。10 分鐘 TTL 自癒 |
| `demo_loop_lease` | 迴圈 | `pid@host\|到期`，擋第二份 `--run`。★ 收工會**刪掉這一列**，所以查不到 = 沒有迴圈在跑 |
| `current_slot` | Job A′ | 回放到哪一格（書籤） |
| `forecast_end` | Job B | 預測涵蓋到幾點 |
| `last_tick` | 迴圈 | 「還活著嗎」，20 秒寫一次 |
| `scheduler_on` | 人 | 總開關，`0` = 迴圈安靜空轉 |
| `virtual_now` | 人 | 靜態凍結，**優先序高過 demo 時鐘**，用完要清 |

```bash
# 看全部鍵值（含流速、迴圈租約、現算中）
uv run python -m app.repository.sys_config_repo
# 手動設一個鍵（demo_speed 除外）
uv run python -m app.repository.sys_config_repo --set demo_t0_virtual '2026-05-01 09:00:00'
```

### 排查

| 症狀 | 先看哪裡 |
|---|---|
| 畫面時間不動 | `--status` 的「回放迴圈」那行有沒有租約持有者。沒有 = 迴圈沒在跑 |
| 時間不動且模式寫「靜態 virtual_now」 | 有人設了 `virtual_now`，清掉：`--set virtual_now -` |
| 時間不動且模式寫「停表中」 | 走到 `demo_until` 了。要續走：`--set demo_until -` 或設更晚的值 |
| 迴圈跑著但不推進 | `--status` 的「應拉／實拉」—— 寫「是新的」就是還沒跨格，正常 |
| 遮罩關不掉 | `/healthz` 的 `predicting_origin`；10 分鐘 TTL 會自己回 null，或重啟 `--run` |
| 沒有新預測 | `--status` 的「觸發 Job B」那行 —— `否` 就是 `replay_predict=0` |
| `--run` 說已經有人在跑，但確定沒有 | `--run --force` 搶租約 |
| 起 `--run` 被拒（非 demo 模式） | 先 `--start` |

---

## 3. 排程 job

### 回放推進（取代 tick，2026-09-12）

`jobs/tick.py` 已刪除、`jobs/crontab.txt` 已清空、`docker-entrypoint.sh` 的
`RUN_TICK` 背景迴圈已移除 —— **後端啟動只提供 API**。回放推進改由
`uv run python -m jobs.demo --run` 一支常駐迴圈負責，**完整說明見 §2**。

對照表（舊指令 → 新指令）：

| 舊 | 新 |
|---|---|
| `jobs.tick`（cron 每分鐘） | `jobs.demo --run`（常駐迴圈） |
| `jobs.tick --status` | `jobs.demo --status` |
| `jobs.tick --force` | `jobs.demo --step`（推一格）／`--run --force`（搶租約） |
| `crontab jobs/crontab.txt` | 不再需要 —— 反而要 `crontab -r` 移除舊的 |
| `DEMO_SPEED=30 …` | `jobs.demo --speed 30`（值在 DB） |

### 手動跑單一 job

```bash
# Job A′：從 baseline_grid 搬一格到 level30（demo 回放用）
uv run python -m jobs.replay_pull

# Job B：批次預測，寫 forecast_history
uv run python -m jobs.batch_predict                        # 全站
uv run python -m jobs.batch_predict --limit 50             # 只跑 50 站
uv run python -m jobs.batch_predict --slot '2026-05-01 20:30'
uv run python -m jobs.batch_predict --dry-run              # 只組 payload 不打

# ★ 2026-09-04 移除：Job A（pull_realtime）／Job C（backfill）／sync_stations
#   TDX 拉取邏輯全部刪掉，唯一的資料來源是 baseline_grid（Job A′ 見上）。
#   站點主檔改為手動匯入維護。
#   要重建 level30：psql -f sql/43_level30_carry.sql（冪等，會先 DELETE 4~7 月）
#   出處：meet/20260904/計劃-移除TDX拉取邏輯.md
```

### 透過 wrapper 跑（有鎖、有 log）

```bash
bash jobs/run_job.sh batch_predict --limit 50
bash jobs/run_job.sh dispatch_sweep
# log：code_backend/logs/<job>-YYYY-MM-DD.log（保留 14 天）
```

⚠️ 不要用 wrapper 跑 `demo --run` —— 那是常駐前景進程，而 run_job.sh 的 log
是「跑完才寫檔」，常駐進程等於永遠不寫。

### 讓回放持續跑

見 §2「回放迴圈 `--run`」。一句話版本：前景跑 `uv run python -m jobs.demo --run`，
關掉終端就停，**沒有 cron 這條路**。

⚠️ 迴圈沒在跑 = 資料不會前進、不會有新預測。「為什麼沒有新預測」問過三次，
每次都是這個原因。先用 `uv run python -m jobs.demo --status` 確認
（看「回放迴圈」那行有沒有租約持有者）。

---

## 4. 資料庫

PostgreSQL 17 跑在 podman 容器 `youbike-pg`（對外 port 5433，db `youbike`），
由 **`docker/docker-compose.yml`** 起 —— 那是唯一一份 compose，**第一次 up 就會自己
把 `docker/bak/*.dump` 灌進去**，port / user / password / db 全部對齊 `app/config.py:72-78`
的預設值，後端不用設任何環境變數。

```bash
# 起庫（首次啟動自動還原，要數分鐘）
podman-compose -f ../docker/docker-compose.yml up -d
podman-compose -f ../docker/docker-compose.yml logs -f

# 容器狀態
podman ps

# ★ 灌完了沒看 healthy，不要看 Up —— initdb 階段的暫時 server 也會回 pg_isready，
#   還原到一半就會顯示 Up。healthcheck 認的是還原完成才寫的 marker。
podman inspect --format '{{.State.Health.Status}}' youbike-pg

# 停容器（資料留著）
podman-compose -f ../docker/docker-compose.yml down

# ★ 換一份新 dump 一定要走這條 —— 直接 up 是換不掉資料的：volume 非空
#   官方 image 就整個跳過 initdb，新 dump 一行不進而且不報錯。
podman-compose -f ../docker/docker-compose.yml down -v
podman-compose -f ../docker/docker-compose.yml up -d

# ★ podman machine 睡眠後常假死：list 顯示 running 但 socket 拒連
podman machine stop && podman machine start && podman start youbike-pg

# 進 psql
podman exec -it youbike-pg psql -U youbike -d youbike

# 單句查詢
podman exec youbike-pg psql -U youbike -d youbike -qc "SELECT count(*) FROM hackathon_backend_station;"
```

### 常用查詢

```sql
-- 回放進度與時鐘（含流速、迴圈租約、現算中）
SELECT key, value, updated_at FROM hackathon_backend_sys_config ORDER BY key;

-- 最近的 job 執行紀錄
SELECT job_name, slot, status, error, started_at
  FROM hackathon_backend_job_run ORDER BY started_at DESC LIMIT 20;

-- 迴圈最近幾輪做了什麼。一輪固定有 pull_replay（①）與 dispatch_sweep（④）兩列；
-- batch_predict（②）**只在那一格要現算時才有**，快轉通過的格不會有。
-- ③風險不另記 job_run —— 它是直接呼叫 write_risk，要看結果查下面那支。
SELECT slot, job_name, status, started_at FROM hackathon_backend_job_run
 ORDER BY started_at DESC LIMIT 12;

-- 風險快照是不是「剛重寫的」（③先刪再判，created_at 會跟著更新）
SELECT origin, count(*) AS n, max(created_at) AS 最近寫入
  FROM hackathon_backend_risk_snapshot
 GROUP BY origin ORDER BY 最近寫入 DESC LIMIT 5;

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

★ **平常不用跑這些** —— `docker/docker-compose.yml` 從 dump 起的庫已經含表結構、
來源資料、level30 歷史與 cat 對照。這一節是「沒有 dump、要從最上游重建」的路，
需要 `/Volumes/myPro/pgdump-20260827.sql.gz`（95,399,918 行），只有明勳本機有。

```bash
bash sql/10_restore_source.sh          # 從原始 pgdump 還原來源表（很久）
psql -f sql/20_backend_ddl.sql         # 建後端用的表
bash sql/30_load_cat_map.sh            # ★ 灌 cat 對照表（換模型必跑）
bash sql/31_load_proxy_cat.sh --verify # 只查代理現況，不寫 DB
bash sql/31_load_proxy_cat.sh          # 重算鄰站代理
```

⚠️ `10_restore_source.sh` 自己 `podman run` 建容器，且內含 `podman rm -f youbike-pg`
—— 它會**強制刪掉 compose 起的那座容器**（資料在 volume 裡不會被刪，但容器沒了，
要 `podman-compose ... up -d` 才回得來）。跑之前確認你真的要重建。

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

`.gitignore` 已擋掉 `.env`、`.cache/`、`logs/`、`.venv/`。

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
