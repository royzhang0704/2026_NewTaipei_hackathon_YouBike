-- ════════════════════════════════════════════════════════════
-- 41_sys_config.sql —— 系統狀態表（排程書籤 + 前端可讀的「系統現在在哪」）
--
-- 為什麼要這張：排程從「固定 :01/:31 開跑」改成「每分鐘輪詢、資料落後才補」，
-- 就需要一個地方存「已經拉到哪一格 / 已經預測到哪個時間點」。
-- 放 DB 不放檔案的理由：後端 API 與 job 是不同 process，要看同一份狀態。
--
-- 設計成 key-value 而不是單列多欄：之後加開關不用改 DDL，
-- 代價是值一律 text，讀的一方要自己轉型（sys_config_repo 負責）。
--
-- 用法（同 40，port 5433；可重跑，不會覆蓋既有值）：
--   PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
--        -v ON_ERROR_STOP=1 -f backend/sql/41_sys_config.sql
-- ════════════════════════════════════════════════════════════
\timing on

BEGIN;

CREATE TABLE IF NOT EXISTS public.hackathon_backend_sys_config (
  key        text        PRIMARY KEY,
  value      text,
  note       text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.hackathon_backend_sys_config IS
  '系統狀態表（key-value）。排程的書籤與開關都在這裡，後端 API 也讀它回報「系統現在在哪個時間點」。值一律 text，轉型由 app/repository/sys_config_repo.py 負責。';
COMMENT ON COLUMN public.hackathon_backend_sys_config.key IS
  '固定鍵名，見 sys_config_repo 的常數區。新增鍵不必改 DDL —— 這正是選 key-value 的原因。';
COMMENT ON COLUMN public.hackathon_backend_sys_config.value IS
  '★ 一律 text。NULL 與空字串都當「沒設定」處理（virtual_now 尤其重要：NULL = 用真實時間）。';
COMMENT ON COLUMN public.hackathon_backend_sys_config.note IS
  '這個鍵是幹嘛的，給直接查 DB 的人看。程式不讀。';
COMMENT ON COLUMN public.hackathon_backend_sys_config.updated_at IS
  '最後寫入時刻。last_tick 靠它就能看出「每分鐘那支輪詢還活著嗎」。';

-- 種子：只在鍵不存在時插入，重跑不會覆蓋線上值
INSERT INTO public.hackathon_backend_sys_config (key, value, note) VALUES
  ('current_slot',  NULL, '最新已拉到的 slot（Job A 成功後寫）。每分鐘輪詢拿它跟 floor(now,30min) 比，落後才補拉'),
  ('forecast_end',  NULL, '預測涵蓋到哪個時刻（Job B 成功後寫 = origin + 3h）'),
  ('scheduler_on',  '1',  '排程總開關。設 0 則每分鐘那支只更新 last_tick，不拉不預測'),
  ('virtual_now',   NULL, '★ 手動覆寫的「現在時間」（台北 naive）。NULL = 用真實時間。設了它排程會停在那個時間點，demo 用完務必清掉'),
  ('last_tick',     NULL, '最後一次輪詢時刻。與 now() 差超過 2 分鐘 = cron 沒在跑（或機器睡了）')
ON CONFLICT (key) DO NOTHING;

COMMIT;

-- ══ 驗收 ═════════════════════════════════════════════════════
\echo
\echo '── sys_config 現況'
SELECT key, coalesce(value, '(未設定)') AS value,
       to_char(updated_at, 'MM-DD HH24:MI:SS') AS updated_at, note
FROM public.hackathon_backend_sys_config ORDER BY key;

\echo
\echo '── 註解覆蓋率（1 表 + 4 欄，缺註解應為 0）'
SELECT count(*) AS 欄數,
       count(*) FILTER (WHERE col_description(c.oid, a.attnum) IS NULL) AS 缺註解
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relname = 'hackathon_backend_sys_config';
