-- ════════════════════════════════════════════════════════════
-- 40_scheduler_tables.sql —— 建排程用的 2 張 hackathon_backend_* 表
--
--   forecast_history 每次批打的完整分位數（前端可直接讀現成結果）
--   job_run          每次排程執行一列，開跑 insert / 結束 update
--
-- ★★ 2026-09-04：原本的第 3 張 actual_history 已廢止（見下方 ══ 1 段）。
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

-- ══ 1　站點實際歷史 ══ ★★ 2026-09-04 廢止，本段已移除 ══════════
--
--   原本這裡建 public.hackathon_backend_actual_history（每 30 分的 TDX
--   原始快照，8 欄 + PK(station_uid, slot)）。連同 TDX 拉取邏輯一起移除：
--   寫入端（Job A pull_realtime、hist_repo.ingest）與讀取端
--   （hist_repo.rebuild_level30）三者都已刪檔。
--   出處：meet/20260904/計劃-移除TDX拉取邏輯.md §1-0 / §2。
--
--   ★ 為什麼是「刪掉 CREATE」而不是只跑 DROP：這支腳本是「疊加」設計，
--     每次都會重跑一遍。CREATE IF NOT EXISTS 留在這裡的話，63 那支
--     DROP 完，下一次跑 40 就把空表又建回來了。
--
--   ⚠ 那張表獨有的三個欄位在 level30 沒有對應，也無法反推：
--       src_update_time  分辨「站點沒動」與「站台斷訊、TDX 回舊值」
--       service_status   TDX ServiceStatus 0/1/2
--       fetched_at       fetched_at−slot = 排程延遲；−src_update_time = 新鮮度
--     使用者 2026-09-04 決定不備份，DROP 前的資料就到此為止。
--
--   要重建 level30 請走 sql/43_level30_carry.sql（baseline_grid → level30，
--   冪等，會先 DELETE 04-01~08-01 再全量重灌）。


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
  'running（開跑/中斷殘留）／success／skipped（Job A′ 覆蓋率 <80% 而不觸發 Job B）／failed。★ 2026-09-04 起 Job C 已移除，skipped 只剩覆蓋率一種成因。';
COMMENT ON COLUMN public.hackathon_backend_job_run.stations_ok IS
  'Job A 成功寫入的站數。≥ 主檔站數 × 80% 才觸發 Job B —— 半份資料打出來的預測比沒有更糟。Job B 則記成功預測的站數（跳過的站不算）。';
COMMENT ON COLUMN public.hackathon_backend_job_run.rows_written IS
  '本輪實際寫入的資料列數。Job A′ ≈ 站數 ×1（只寫 level30）、Job B ≈ 站數 ×6（每站 6 格）—— 與 stations_ok 的比例不對就是有站沒寫齊。★ 2026-09-04 之前 Job A 是站數 ×2（同時寫 actual_history），舊列與新列不可直接比較。';
COMMENT ON COLUMN public.hackathon_backend_job_run.bytes_in IS
  '原本是 TDX 點數對帳的唯一資料來源（實際收到的位元組）。★ 2026-09-04 起 TDX 拉取邏輯已移除，Job A′ 與 Job B 都不打外部 API，這欄一律 NULL；job_run_repo.month_usage() 也一併刪了。歷史列裡的值仍是當時的實際流量，留著不動。';
COMMENT ON COLUMN public.hackathon_backend_job_run.error IS
  '失敗/跳過原因摘要（429 重試耗盡、timeout、覆蓋率不足 x/y…）。一句話可讀即可，完整 traceback 留在 backend/logs/ 的 log 檔，不塞這裡。';
COMMENT ON COLUMN public.hackathon_backend_job_run.detail IS
  'jsonb 雜項統計：Job A 記重試次數／http status／新站數，Job B 記 invoke 批數／跳過站數（INSUFFICIENT_HISTORY 等）／各批耗時。欄位不固定，查詢端一律用 ->> 並容忍缺鍵。';

COMMIT;

-- ══ 驗收 ═════════════════════════════════════════════════════
\echo
\echo '── 兩張表是否都在（期望 2 列）'
SELECT c.relname AS 表名,
       (SELECT count(*) FROM pg_attribute a
         WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS 欄數,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS 大小
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('hackathon_backend_forecast_history',
                    'hackathon_backend_job_run')
ORDER BY 1;

\echo
\echo '── PK / 索引（期望：forecast 2、job_run 2）'   -- forecast 的第 2 個是 sql/62 加的 run_id_idx
SELECT tablename AS 表名, indexname AS 索引名, indexdef AS 定義
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('hackathon_backend_forecast_history',
                    'hackathon_backend_job_run')
ORDER BY 1, 2;

\echo
\echo '── 註解覆蓋率（2 張表 + 20 欄，缺註解欄數應為 0）'
SELECT c.relname AS 表名,
       count(*) AS 欄數,
       count(*) FILTER (WHERE col_description(c.oid, a.attnum) IS NULL) AS 缺註解,
       CASE WHEN obj_description(c.oid, 'pg_class') IS NULL THEN '✗' ELSE '✓' END AS 表註解
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'public'
  AND c.relname IN ('hackathon_backend_forecast_history',
                    'hackathon_backend_job_run')
GROUP BY c.oid, c.relname
ORDER BY 1;

\echo
\echo '── 現有列數（首次建表應為 0）'
SELECT 'forecast_history' AS t, count(*) AS n FROM public.hackathon_backend_forecast_history
UNION ALL SELECT 'job_run',          count(*) FROM public.hackathon_backend_job_run
ORDER BY 1;
