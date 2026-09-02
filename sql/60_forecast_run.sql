-- ════════════════════════════════════════════════════════════
-- 60_forecast_run.sql —— 建預測主檔 hackathon_backend_forecast_run
--
--   主檔／明細的粒度（9/1 使用者定案）：
--     主檔 forecast_run      「一站一個半小時 = 一次預測」一列
--     明細 forecast_history   那次預測的 6 格分位數，用 run_id 寫回主檔
--     風險 risk_snapshot      那次預測的風險判定結果，同樣掛 run_id
--   主檔回答兩個問題：
--     ① 這站這個時段預測過了沒？→ predict_status / predicted_at
--     ② 這站這個時段風險判過沒？→ risk_done_at（有填 = 判過）
--
-- ★ 鍵：id bigserial 當 PK（明細的 run_id 參照它），
--   (station_uid, origin) 設 UNIQUE 當業務 UID。明細只存一個 bigint。
--
-- ★ 為什麼不用 job_run：那是「執行歷程」——每次執行 insert 一列，
--   同 origin 重跑是新列，crash 還刻意留 status='running' 殘列當診斷訊號
--   （40_scheduler_tables.sql 已載明「不要用 ON CONFLICT 清掉」）。
--   冪等判定要的是「一個 origin 有且只有一列」的狀態主檔，拿 job_run 判
--   會卡在「多列挑哪一列／殘留 running 算不算做過」。兩張並存，分工是
--   歷程 vs 狀態。
--
-- ★ 兩階段狀態分開存，是這張表最大的價值：
--   predict_status='done' 但 risk_done_at IS NULL = 預測有了、風險還沒判。
--   改風險演算法時 TRUNCATE risk_snapshot、清 risk_done_at，就能只重算
--   風險、完全不打 SageMaker（判定的輸入 forecast_history + level30 +
--   station_slot_average 全在庫裡）。主檔一列都不用動。
--
-- ★ 冪等的粒度也跟著變成「站 × 時段」：該站該時段查得到 done 的列就不重打。
--   STALE_ANCHOR（本輪沒有新鮮資料）等被跳過的站也寫一列
--   predict_status='skipped' + skip_reason —— 「處理過但沒打」與「還沒處理」
--   必須分得開，否則每輪都會重試同一批注定跳過的站。
--
-- 疊加表（同 40_）：一律 IF NOT EXISTS，重跑不清空。
--
-- 用法：
--   PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
--        -v ON_ERROR_STOP=1 -f backend/sql/60_forecast_run.sql
-- ════════════════════════════════════════════════════════════
\timing on

BEGIN;

CREATE TABLE IF NOT EXISTS public.hackathon_backend_forecast_run (
  id                 bigserial   PRIMARY KEY,
  station_uid        text        NOT NULL,
  origin             timestamp   NOT NULL,
  -- ══ 階段一：預測 ══
  predict_status     text        NOT NULL DEFAULT 'running',
  predicted_at       timestamptz,
  model_job          text,
  skip_reason        text,
  slots              int,
  -- ══ 階段二：風險判定 ══
  risk_done_at       timestamptz,
  risk_algo_ver      text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT hackathon_backend_forecast_run_uid_key UNIQUE (station_uid, origin)
);

-- 「這一輪整批的狀態」現在是聚合查詢（主檔沒有 batch 層的列了）：
-- 錨點 origin、全市統計都走這支索引。
CREATE INDEX IF NOT EXISTS hackathon_backend_forecast_run_origin_idx
  ON public.hackathon_backend_forecast_run (origin, predict_status);

COMMENT ON TABLE public.hackathon_backend_forecast_run IS
  '★ 預測主檔：一站一個半小時（= 一次預測）一列，是冪等判定的唯一依據。forecast_history 是它的明細（run_id 參照）、risk_snapshot 是它的風險判定結果。與 job_run 的分工：job_run 記「執行過程」（每次執行一列、含失敗與 running 殘列），這張記「完成狀態」。兩張都要留，不要互相取代。';

COMMENT ON COLUMN public.hackathon_backend_forecast_run.id IS
  '★ 流水號 PK —— forecast_history.run_id 與 risk_snapshot.run_id 參照的就是它。明細只存一個 bigint，不必扛 (站, 時間) 兩欄。';
COMMENT ON COLUMN public.hackathon_backend_forecast_run.station_uid IS
  '被預測的站。與 origin 合成 UNIQUE（業務 UID）—— 同一站同一時段只會有一列，重跑走 upsert。第 8~9 碼是行政區碼。未設 FK 到主檔：Job A 可能先看到新站。';
COMMENT ON COLUMN public.hackathon_backend_forecast_run.origin IS
  '預測原點 = 觸發批打的 slot（台北時間 naive，對齊 :00/:30）。明細的 at = origin + 30m … +3h。';
COMMENT ON COLUMN public.hackathon_backend_forecast_run.predict_status IS
  '★ 冪等判定第一層（站層）：running（開跑/中斷殘留）／done（明細寫完）／skipped（本輪不打這站，原因見 skip_reason）／failed。done 且 model_job 與現行模型相同 → 這站這輪不重打。--force 可覆寫。';
COMMENT ON COLUMN public.hackathon_backend_forecast_run.predicted_at IS
  '明細寫完的時刻（帶時區）。與 origin 的差距 = 批打延遲。';
COMMENT ON COLUMN public.hackathon_backend_forecast_run.model_job IS
  '★ 這一列是哪個模型打的（SageMaker training job／endpoint 名稱，MOCK 前綴代表 ENDPOINT_MOCK=1）。與現行 config.MODEL_INFO[''job''] 不同時冪等必須失效並重打 —— cat 對照是訓練時的字典序決定的，混了不會報錯只會全錯。';
COMMENT ON COLUMN public.hackathon_backend_forecast_run.skip_reason IS
  '★ 被跳過的原因：STALE_ANCHOR（本輪 level30 沒有新鮮資料，anchor != origin —— 硬打的話模型吐的 6 格會有一部分落在過去，寫成 origin=本輪的預測是在說謊）／INSUFFICIENT_HISTORY／STATION_UNKNOWN。「處理過但沒打」與「還沒處理」必須分得開，否則每輪都會重試同一批注定跳過的站。';
COMMENT ON COLUMN public.hackathon_backend_forecast_run.slots IS
  '本列寫進 forecast_history 的格數（正常 = config.H = 6）。skipped 為 0。';
COMMENT ON COLUMN public.hackathon_backend_forecast_run.risk_done_at IS
  '★★ 風險判定完成時刻。**有值 = 這站這輪判過**，這就是冪等判定第二層。NULL 且 predict_status=''done'' = 預測有了風險還沒判（下一輪或 --risk-only 會補）。清成 NULL 即可強制重判，不必動明細、不必打 endpoint。';
COMMENT ON COLUMN public.hackathon_backend_forecast_run.risk_algo_ver IS
  '★ 判定當下的演算法版本（config.RISK_ALGO_VER，如 time-v1/pct15 = 時間制分級 + 15% 門檻）。與現行值不同 → 重算風險（只讀 DB）。streak 也只在同版本內連續 —— 門檻或分級規則改了，跨版本的「連續 N 輪」沒有意義。';
COMMENT ON COLUMN public.hackathon_backend_forecast_run.created_at IS
  '本列建立時刻。';
COMMENT ON COLUMN public.hackathon_backend_forecast_run.updated_at IS
  '最後一次更新時刻。兩個階段各更新一次，重跑也會更新。';

COMMIT;

ANALYZE public.hackathon_backend_forecast_run;

-- ══ 驗收 ═════════════════════════════════════════════════════
\echo
\echo '── 表與索引（期望 12 欄；PK + UNIQUE(站,時) + origin 索引 = 3 支）'
SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid
          AND a.attnum > 0 AND NOT a.attisdropped) AS 欄數,
       (SELECT count(*) FROM pg_indexes i WHERE i.schemaname = 'public'
          AND i.tablename = c.relname) AS 索引數,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS 大小
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'hackathon_backend_forecast_run';

\echo
\echo '── 註解覆蓋率（缺註解欄數應為 0）'
SELECT count(*) AS 欄數,
       count(*) FILTER (WHERE col_description(c.oid, a.attnum) IS NULL) AS 缺註解,
       CASE WHEN obj_description(c.oid, 'pg_class') IS NULL THEN '✗' ELSE '✓' END AS 表註解
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relname = 'hackathon_backend_forecast_run'
GROUP BY c.oid;

\echo
\echo '── 現有列數'
SELECT count(*) AS n FROM public.hackathon_backend_forecast_run;
