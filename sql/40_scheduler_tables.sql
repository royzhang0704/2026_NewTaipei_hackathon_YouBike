-- ════════════════════════════════════════════════════════════
-- 40_scheduler_tables.sql —— 建排程用的 3 張 hackathon_backend_* 表
--
--   actual_history   每 30 分的 TDX 原始快照（審計 / 重算的唯一依據）
--   forecast_history 每次批打的完整分位數（前端可直接讀現成結果）
--   job_run          每次排程執行一列，開跑 insert / 結束 update
--
-- 出處：meet/20260828/計劃-TDX排程與初始化.md §3（DDL 照抄）
--
-- ★ 與 20_backend_ddl.sql 的差別：那支是「重建」（DROP 再 CREATE AS），
--   這支是「疊加」—— 表裡是持續累積的營運資料，重跑不能清空。
--   所以一律 IF NOT EXISTS，索引也給明確名稱（匿名索引無法 IF NOT EXISTS）。
--   ⚠ 要改欄位定義得自己寫 ALTER，這支腳本不會幫你 migrate。
--
-- 用法（連線慣例同 10_restore_source.sh / 31_load_proxy_cat.sh，port 5433）：
--   PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
--        -v ON_ERROR_STOP=1 -f backend/sql/40_scheduler_tables.sql
-- ════════════════════════════════════════════════════════════
\timing on

BEGIN;

-- ══ 1　站點實際歷史 ══════════════════════════════════════════
--   Job A 每 30 分寫一批。這是「TDX 當下的認知」的原始快照，
--   不做任何守門 / carry-forward —— 那些是 level30 那一層的事。
CREATE TABLE IF NOT EXISTS public.hackathon_backend_actual_history (
  station_uid     text        NOT NULL,
  slot            timestamp   NOT NULL,   -- floor(now, 30min)，台北時間 naive
  avail           int,                    -- AvailableRentBikes
  return_slots    int,                    -- AvailableReturnBikes
  service_status  int,                    -- 0/1/2（1=正常營運）
  src_update_time timestamptz,            -- TDX 來源時間，可驗資料新鮮度
  fetched_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (station_uid, slot)
);

COMMENT ON TABLE public.hackathon_backend_actual_history IS
  '站點實際歷史：每 30 分的 TDX 原始快照，slot 對齊 :00/:30（台北時間 naive，與 level30 慣例一致）。模型讀的不是這張而是 hackathon_backend_level30 —— 這張留原始值供審計與重算，重採樣規則若日後修正，可以只從這裡重跑而不必再打 TDX。';
COMMENT ON COLUMN public.hackathon_backend_actual_history.station_uid IS
  'TDX 的 StationUID，與 hackathon_backend_station 同鍵（未設 FK：快照裡可能出現主檔還沒有的新站，Job A 會另行 insert 主檔 cat=NULL，不能讓 FK 把整批寫入擋下）。第 8~9 位是行政區碼。';
COMMENT ON COLUMN public.hackathon_backend_actual_history.slot IS
  '★ 台北時間 naive timestamp，floor 至 :00/:30。TDX 的 SrcUpdateTime 帶 +08:00 時區，先轉再比。與 level30.slot 同慣例 —— 兩邊對不齊，模型就讀不到當輪資料。';
COMMENT ON COLUMN public.hackathon_backend_actual_history.avail IS
  '★ AvailableRentBikes（可借車數）。三種零嚴格區分：NULL=TDX 沒回這個欄位／0=真的無車／填補值只存在於 level30 的 is_observed=0，這張表沒有填補值。這一欄就是 level30.avail 與模型 target 的來源。';
COMMENT ON COLUMN public.hackathon_backend_actual_history.return_slots IS
  'AvailableReturnBikes（可還空位數）。留著供審計與前端顯示；level30.docks 用的是主檔 capacity 而不是這一欄（重採樣規則第 4 條：docks 按日 join 當天容量）。';
COMMENT ON COLUMN public.hackathon_backend_actual_history.service_status IS
  'TDX ServiceStatus：0=停止服務、1=正常營運、2=暫停營運。非 1 時 avail 仍可能有值但不可信，重採樣的守門與異常查核會看這一欄。';
COMMENT ON COLUMN public.hackathon_backend_actual_history.src_update_time IS
  '★ 站台斷訊時 TDX 仍會回舊值。距 slot 超過 3 小時（= carry-forward 上限 6 格）的站，本表照寫快照（保留證據），但 level30 不寫列 —— 缺格 = NULL 語意。這是唯一能分辨「真的沒動」與「站台掛了」的欄位。';
COMMENT ON COLUMN public.hackathon_backend_actual_history.fetched_at IS
  '本機實際寫入時刻（帶時區）。與 slot 的差距 = 排程延遲，與 src_update_time 的差距 = 資料新鮮度，兩者診斷不同的問題，不要互相取代。';

-- ══ 2　站點預測歷史 ══════════════════════════════════════════
--   Job B 每輪寫 ≈1,594 站 × 6 格。同 origin 重跑走 upsert 覆蓋
--   （執行歷程不覆蓋，那是 job_run 的事）。
CREATE TABLE IF NOT EXISTS public.hackathon_backend_forecast_history (
  station_uid text      NOT NULL,
  origin      timestamp NOT NULL,        -- 預測原點 = 觸發它的 slot
  at          timestamp NOT NULL,        -- 預測目標時刻（origin+30m … +3h）
  q19 real, q50 real, q90 real,
  model_job   text      NOT NULL,        -- 溯源：哪個模型打的
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (station_uid, origin, at)
);

COMMENT ON TABLE public.hackathon_backend_forecast_history IS
  '站點預測歷史：每次批打（Job B）的完整分位數，每站每 origin 6 格（+30m … +3h）。PK 撞到 = 同 origin 重跑 → upsert 覆蓋。/predict 與 /view 可優先讀同 origin 的現成結果，沒有才即時 invoke。';
COMMENT ON COLUMN public.hackathon_backend_forecast_history.station_uid IS
  '被預測的站。對象是主檔 cat 非 NULL 或有 proxy 的站（≈1,594）；用 proxy 的站這裡照樣有列，服務層必須在回應標示 proxy 區塊 + caveat（計劃-鄰站cat代理.md）。';
COMMENT ON COLUMN public.hackathon_backend_forecast_history.origin IS
  '預測原點 = 觸發這次批打的 slot（台北時間 naive）。同一站同一 origin 只留最後一次的結果。查「當下最新預測」= 取該站 max(origin) 那一組 6 列。';
COMMENT ON COLUMN public.hackathon_backend_forecast_history."at" IS
  '★ 預測目標時刻 = origin + 30m … +3h，每 origin 每站 6 格（H=6 是模型結構的一部分，不是參數）。at 是保留字，SQL 裡要寫成 "at"。';
COMMENT ON COLUMN public.hackathon_backend_forecast_history.q19 IS
  '19% 分位數（保守下界）。「借得到車嗎」看這個 —— q19 仍 ≥1 才算穩。已套 predict_one 的 clip（0 ≤ q ≤ capacity）。';
COMMENT ON COLUMN public.hackathon_backend_forecast_history.q50 IS
  '中位數，前端主要顯示值。沿用 predict_one 的 clip 規則後的值（0 ≤ q ≤ capacity）。單調性 q19 ≤ q50 ≤ q90 由寫入端保證，DB 不設 CHECK —— 模型偶爾會交叉，擋下來會整批失敗，寧可寫進去事後查。';
COMMENT ON COLUMN public.hackathon_backend_forecast_history.q90 IS
  '90% 分位數（樂觀上界）。「還得了車嗎」看 capacity - q90 —— 上界都塞不滿才算有空位。已套 clip。';
COMMENT ON COLUMN public.hackathon_backend_forecast_history.model_job IS
  '★ 溯源用：SageMaker 的 training job / endpoint 名稱。cat 對照是靠訓練時的字典序決定的，換模型沒換這欄就會查不出是哪一版打的。ENDPOINT_MOCK=1 跑出來的列也要能從這欄認出來，別混進驗收統計。';
COMMENT ON COLUMN public.hackathon_backend_forecast_history.created_at IS
  '本列寫入時刻（帶時區）。與 origin 的差距 = Job B 的批打延遲；同 origin 重跑會覆蓋，這一欄跟著更新，要看歷次執行請查 job_run。';

-- ══ 3　job 執行歷程 ══════════════════════════════════════════
--   ★ process crash 會留下 status='running' 的殘列 —— 這是刻意的：
--     下一輪看到即知上輪中斷，不必翻 log。不要用 ON CONFLICT 清掉。
CREATE TABLE IF NOT EXISTS public.hackathon_backend_job_run (
  id           bigserial   PRIMARY KEY,
  job_name     text        NOT NULL,   -- pull_realtime / batch_predict / backfill / init_backfill
  slot         timestamp,              -- Job A/B=觸發 slot；Job C/初始化=目標日 00:00
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  status       text        NOT NULL DEFAULT 'running',  -- running/success/skipped/failed
  stations_ok  int,                    -- 成功寫入站數（Job A 的 80% 判定依據）
  rows_written int,
  bytes_in     bigint,                 -- 本次 API 回應位元組（點數對帳的資料來源）
  error        text,                   -- 失敗/跳過原因摘要（429、timeout、覆蓋率不足…）
  detail       jsonb                   -- 其餘統計（重試次數、invoke 批數、http status…）
);

CREATE INDEX IF NOT EXISTS hackathon_backend_job_run_job_name_started_at_idx
  ON public.hackathon_backend_job_run (job_name, started_at);

COMMENT ON TABLE public.hackathon_backend_job_run IS
  'job 執行歷程：每次排程執行一列（開跑 insert status=running，結束 update）。Job B 同 origin 重跑是新列，歷程不覆蓋。殘留的 running 列 = 上輪 process 中斷，是刻意保留的診斷訊號。';
COMMENT ON COLUMN public.hackathon_backend_job_run.id IS
  'bigserial 流水號。Job 開跑時 insert 拿到 id，結束用它 update —— 同一輪不會有第二列，跨輪不重用。';
COMMENT ON COLUMN public.hackathon_backend_job_run.job_name IS
  'pull_realtime（Job A）／batch_predict（Job B）／backfill（Job C）／init_backfill（9/12 初始化）。與 (job_name, started_at) 索引搭配查單一 job 的近期歷程。';
COMMENT ON COLUMN public.hackathon_backend_job_run.slot IS
  'Job A/B = 觸發的 slot（台北時間 naive，對齊 :00/:30）；Job C 與初始化 = 目標日 00:00。可為 NULL（不綁特定 slot 的手動執行）。';
COMMENT ON COLUMN public.hackathon_backend_job_run.started_at IS
  '開跑時刻（帶時區，DB now()）。索引第二欄；月耗對帳的時間依據也是它（date_trunc(''month'', started_at)），不要改用 finished_at —— 跨月那輪會算錯邊。';
COMMENT ON COLUMN public.hackathon_backend_job_run.finished_at IS
  '結束時刻。★ NULL 且 status=running = process 中斷的殘列，這是刻意保留的診斷訊號，不要寫清理排程把它掃掉。';
COMMENT ON COLUMN public.hackathon_backend_job_run.status IS
  'running（開跑/中斷殘留）／success／skipped（Job A 覆蓋率 <80% 而不觸發 Job B、Job C 未達回補門檻）／failed。';
COMMENT ON COLUMN public.hackathon_backend_job_run.stations_ok IS
  'Job A 成功寫入的站數。≥ 主檔站數 × 80% 才觸發 Job B —— 半份資料打出來的預測比沒有更糟。Job B 則記成功預測的站數（跳過的站不算）。';
COMMENT ON COLUMN public.hackathon_backend_job_run.rows_written IS
  '本輪實際寫入的資料列數。Job A ≈ 站數 ×2（actual_history + level30）、Job B ≈ 站數 ×6（每站 6 格）—— 與 stations_ok 的比例不對就是有站沒寫齊。';
COMMENT ON COLUMN public.hackathon_backend_job_run.bytes_in IS
  '★ 點數對帳的唯一資料來源：len(resp.content)，實際收到的位元組（gzip 後）。月耗查 sum(bytes_in) WHERE started_at >= date_trunc(''month'', now())，超過 150 點估算值就停 Job C。TDX 銅級用到 105% 直接停權。Job B 不打 TDX，這欄留 NULL。';
COMMENT ON COLUMN public.hackathon_backend_job_run.error IS
  '失敗/跳過原因摘要（429 重試耗盡、timeout、覆蓋率不足 x/y…）。一句話可讀即可，完整 traceback 留在 backend/logs/ 的 log 檔，不塞這裡。';
COMMENT ON COLUMN public.hackathon_backend_job_run.detail IS
  'jsonb 雜項統計：Job A 記重試次數／http status／新站數，Job B 記 invoke 批數／跳過站數（INSUFFICIENT_HISTORY 等）／各批耗時。欄位不固定，查詢端一律用 ->> 並容忍缺鍵。';

COMMIT;

-- ══ 驗收 ═════════════════════════════════════════════════════
\echo
\echo '── 三張表是否都在（期望 3 列）'
SELECT c.relname AS 表名,
       (SELECT count(*) FROM pg_attribute a
         WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS 欄數,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS 大小
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('hackathon_backend_actual_history',
                    'hackathon_backend_forecast_history',
                    'hackathon_backend_job_run')
ORDER BY 1;

\echo
\echo '── PK / 索引（期望：actual 1、forecast 1、job_run 2）'
SELECT tablename AS 表名, indexname AS 索引名, indexdef AS 定義
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('hackathon_backend_actual_history',
                    'hackathon_backend_forecast_history',
                    'hackathon_backend_job_run')
ORDER BY 1, 2;

\echo
\echo '── 註解覆蓋率（3 張表 + 26 欄，缺註解欄數應為 0）'
SELECT c.relname AS 表名,
       count(*) AS 欄數,
       count(*) FILTER (WHERE col_description(c.oid, a.attnum) IS NULL) AS 缺註解,
       CASE WHEN obj_description(c.oid, 'pg_class') IS NULL THEN '✗' ELSE '✓' END AS 表註解
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'public'
  AND c.relname IN ('hackathon_backend_actual_history',
                    'hackathon_backend_forecast_history',
                    'hackathon_backend_job_run')
GROUP BY c.oid, c.relname
ORDER BY 1;

\echo
\echo '── 現有列數（首次建表應為 0）'
SELECT 'actual_history'   AS t, count(*) AS n FROM public.hackathon_backend_actual_history
UNION ALL SELECT 'forecast_history', count(*) FROM public.hackathon_backend_forecast_history
UNION ALL SELECT 'job_run',          count(*) FROM public.hackathon_backend_job_run
ORDER BY 1;
