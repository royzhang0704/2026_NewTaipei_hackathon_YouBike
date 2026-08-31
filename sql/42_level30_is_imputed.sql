-- ════════════════════════════════════════════════════════════
-- 42_level30_is_imputed.sql —— level30 加「週期補值」旗標
--
-- 出處：meet/20260828/計劃-排程自癒與level30滾動視窗.md §2「週期補值」
--       ★ 8/28 使用者定案改為**值落表**（計劃原本寫「值不落表」）。
--         落表就必須有旗標，否則半年後沒人分得出哪一格是實測、哪一格是猜的
--         —— 那正是 07-10 餵食故障留下的教訓。
--
-- 三種零 + 兩種來源，四個狀態要能分開（缺任一個就會安靜地混用）：
--   avail IS NULL                          該格沒有資料
--   avail = 0                              真的無車
--   is_observed = 1                        這格是實測
--   is_observed = 0, is_imputed = 0        carry-forward 填的（≤6 格）
--   is_observed = 0, is_imputed = 1        週期補值填的（取一週前同 slot）
--
-- ★ 這支是「疊加」不是「重建」（同 40_scheduler_tables.sql 的原則）：
--   level30 是持續累積的營運資料，重跑不能清空。可重複執行。
--
-- 用法：
--   PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
--        -v ON_ERROR_STOP=1 -f backend/sql/42_level30_is_imputed.sql
-- ════════════════════════════════════════════════════════════
\timing on

ALTER TABLE public.hackathon_backend_level30
  ADD COLUMN IF NOT EXISTS is_imputed smallint NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.hackathon_backend_level30.is_imputed IS
  '★ 1 = 這格的 avail 是「週期補值」（取一週前同 slot 的真值），不是實測也不是 carry-forward。0 = 實測或 carry。存在的理由：當日缺格歷史 API 補不到（每日 08:00 才更新至昨日），但 /predict 的 48 格 context 不能整段是 null。⚠ 三條硬規則：① 真值一律可以覆蓋 is_imputed=1 的格（Job A/Job C 的 upsert 規則），否則明天回補的真值進不來；② 缺格率統計（Job C 判定 d）只算 is_imputed=0 的格，把補值算進去會讓 Job C 再也不觸發，真值永遠回不來；③ 補值的來源必須是 is_imputed=0 的真值，不可拿補值再去補值（會一路鏈式傳播上上週的數字）。';

-- ★ 部分索引：缺格率統計與「哪些格還是補值」都只關心真值那一半。
--   level30 是滾動視窗表（14 天，約 77 萬列），這個索引很小。
CREATE INDEX IF NOT EXISTS hackathon_backend_level30_real_idx
  ON public.hackathon_backend_level30 (slot)
  WHERE is_imputed = 0 AND avail IS NOT NULL;

ANALYZE public.hackathon_backend_level30;

-- ══ 驗收 ═════════════════════════════════════════════════════
\echo ''
\echo '── 欄位與註解'
SELECT column_name, data_type, column_default, is_nullable
FROM information_schema.columns
WHERE table_name = 'hackathon_backend_level30' ORDER BY ordinal_position;

\echo ''
\echo '── 四個狀態的分佈（現在應該還沒有 is_imputed=1）'
SELECT is_observed, is_imputed,
       count(*) AS 格數,
       count(*) FILTER (WHERE avail IS NULL) AS 其中值為NULL
FROM public.hackathon_backend_level30
GROUP BY 1, 2 ORDER BY 1, 2;
