-- ════════════════════════════════════════════════════════════
-- 63_drop_actual_history.sql —— 刪掉 actual_history 與兩張 staging 表
--
-- 出處：meet/20260904/計劃-移除TDX拉取邏輯.md §2
--       （使用者 2026-09-04 決定：TDX 拉取邏輯全砍，只保留預測）
--
-- 為什麼可以刪：三個引用端都已刪檔
--   寫入① jobs/pull_realtime.py       Job A：TDX 即時水位 → actual_history
--   寫入② hist_repo.ingest()          歷史 API CSV → actual_history
--   讀取  hist_repo.rebuild_level30() 從 actual_history 重建 level30
--   模型讀的一直是 hackathon_backend_level30，不是這張。
--   level30 的權威來源現在是 baseline_grid → sql/43_level30_carry.sql。
--
-- ⚠⚠ 不可逆。這張表獨有的三個欄位在 level30 沒有對應，也無法反推：
--       src_update_time  唯一能分辨「站點真的沒動」與「站台斷訊、TDX 回舊值」
--       service_status   TDX ServiceStatus（0 停止 / 1 正常 / 2 暫停）
--       fetched_at       fetched_at−slot = 排程延遲；−src_update_time = 新鮮度
--     使用者已明示不備份。真要留一份的話是：
--       pg_dump -t public.hackathon_backend_actual_history --no-owner -f <檔>
--
-- ★ 這支與 40 是一組：40 已把 actual_history 的 CREATE 段刪掉，
--   否則 40 一重跑就把空表建回來（那支是「疊加」設計，每次都會重跑）。
--
-- ★ 兩張 staging 表一起刪：hist_repo 的 stage_csv/ingest 已移除，
--   它們是每次 ingest 重建的暫存表，沒有資料價值。
--
-- 用法（連線慣例同 40_scheduler_tables.sql，port 5433）：
--   PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
--        -v ON_ERROR_STOP=1 -f backend/sql/63_drop_actual_history.sql
-- ════════════════════════════════════════════════════════════
\timing on

-- ══ 前置對帳：刪之前把規模印出來 ══════════════════════════════
--   2026-09-04 執行時的實測值：actual_history 398,745 列 / 57 MB、
--   hist_obs 32 kB、hist_stage 16 kB。數字差很多就先停下來看看為什麼。
\echo
\echo '── 刪除前的規模（★ 這是最後一次看到它們）'
SELECT c.relname                                      AS 表名,
       pg_size_pretty(pg_total_relation_size(c.oid))  AS 大小,
       CASE c.relname
            WHEN 'hackathon_backend_actual_history'
            THEN (SELECT count(*) FROM public.hackathon_backend_actual_history)
       END                                            AS 列數
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relname IN ('hackathon_backend_actual_history',
                     'hackathon_backend_hist_stage',
                     'hackathon_backend_hist_obs')
 ORDER BY 1;

\echo
\echo '── actual_history 的時間範圍與獨有欄位的覆蓋率（刪掉就沒有了）'
SELECT min(slot)                                              AS 最早,
       max(slot)                                              AS 最晚,
       count(*) FILTER (WHERE src_update_time IS NOT NULL)    AS 有來源時間,
       count(*) FILTER (WHERE service_status IS NOT NULL)     AS 有服務狀態,
       count(DISTINCT station_uid)                            AS 站數
  FROM public.hackathon_backend_actual_history;

-- ══ 執行 ═════════════════════════════════════════════════════
--   ★ 一個交易內做完。PK 索引隨表消失，不必另外處理。
--   IF EXISTS：重跑這支腳本不該報錯（與 40/41/42/43 的冪等慣例一致）。
BEGIN;

DROP TABLE IF EXISTS public.hackathon_backend_actual_history;
DROP TABLE IF EXISTS public.hackathon_backend_hist_stage;
DROP TABLE IF EXISTS public.hackathon_backend_hist_obs;

COMMIT;

-- ══ 驗收 ═════════════════════════════════════════════════════
\echo
\echo '── 三張表都不該在了（期望 0 列）'
SELECT c.relname AS 還在的表
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relname IN ('hackathon_backend_actual_history',
                     'hackathon_backend_hist_stage',
                     'hackathon_backend_hist_obs');

\echo
\echo '── 該留的表都還在（期望 6 列：baseline_grid / level30 / station /'
\echo '   forecast_history / job_run / sys_config）'
SELECT c.relname                                     AS 表名,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS 大小
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relname IN ('baseline_grid',
                     'hackathon_backend_level30',
                     'hackathon_backend_station',
                     'hackathon_backend_forecast_history',
                     'hackathon_backend_job_run',
                     'hackathon_backend_sys_config')
 ORDER BY 1;
