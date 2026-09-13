-- ════════════════════════════════════════════════════════════
-- 42_level30_is_imputed.sql —— level30 加「週期補值」旗標
--
-- 出處：meet/20260828/計劃-排程自癒與level30滾動視窗.md §2「週期補值」
--       ★ 8/28 使用者定案改為**值落表**（計劃原本寫「值不落表」）。
--         落表就必須有旗標，否則半年後沒人分得出哪一格是實測、哪一格是猜的
--         —— 那正是 07-10 餵食故障留下的教訓。
--
-- 三種零 + 三種來源，五個狀態要能分開（缺任一個就會安靜地混用）：
--   avail IS NULL                          該格沒有資料
--   avail = 0                              真的無車
--   is_observed = 1                        這格是實測
--   is_observed = 0, is_imputed = 0        carry-forward 填的（≤6 格）
--   is_observed = 0, is_imputed = 1        週期補值填的（取一週前同 slot）
--   is_observed = 0, is_imputed = 2        ★ 無限 carry（9/2 新增）
--
-- ★ 2026-09-02：is_imputed 從兩態變三態。
--   出處：meet/20260902/計劃-level30灌歷史與無限carry.md 決策 7。
--   43_level30_carry.sql 把 baseline_grid 4~7 月灌進 level30 時，
--   把 >6 格的洞（792,237 格）全部 carry 掉，那批就是 2。
--   ⚠ avail IS NULL 這個狀態在 04-01 ~ 08-01 區間已經不存在了 ——
--     那段是「值不缺」的（決策 2）。8 月之後的真排程區仍會有 NULL。
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
  '★ 三態（9/2 從兩態擴充，出處 meet/20260902/計劃-level30灌歷史與無限carry.md）。0 = 實測，或 baseline_grid 自己填的 ≤6 格 carry-forward。1 = 「週期補值」，取一週前同 slot 的真值。2 = 「無限 carry」，延用該站最後一個 is_imputed=0 的值、不設上限，由 43_level30_carry.sql 灌 4~7 月時填的（792,237 格）。1 與 2 的差別是可信度：週期補值還有日內節律，長 carry 沒有——07-11 那天全市幾乎整天是同一個數字。存在的理由：當日缺格歷史 API 補不到（每日 08:00 才更新至昨日），但 /predict 的 48 格 context 不能整段是 null。⚠ 三條硬規則：① 真值一律可以覆蓋 is_imputed <> 0 的格（Job A/Job C 的 upsert 規則）——★ 9/2 從「= 1」放寬成「<> 0」，寫成 = 1 的話真值蓋不掉 2，明天回補的真值就永遠進不來；② 缺格率統計（Job C 判定 d）只算 is_imputed = 0 的格，把補值算進去會讓 Job C 再也不觸發，真值永遠回不來；③ 補值的來源必須是 is_imputed = 0 的真值，不可拿補值再去補值（會一路鏈式傳播上上週的數字）。★ 1 與 2 互不覆蓋（決策 8）：carry 值已經是值，換成週期補值只是換一種猜法、不會更真，卻讓同一格的來源隨 Job 執行而反覆變動。';

-- ★ 部分索引：缺格率統計與「哪些格還是補值」都只關心真值那一半。
--   ⚠ 2026-09-02 起這個「很小」不再成立：43_level30_carry.sql 灌了 4~7 月，
--     level30 從 77 萬列變成約 1,130 萬列，而其中 823 萬列是 is_imputed=0
--     且 avail IS NOT NULL —— 幾乎整張表都進得了這支部分索引。
--     retention（config.py:225，尚未實作）若把歷史區豁免掉，它就會一直這麼大。
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
\echo '── 五個狀態的分佈。★ is_imputed=2 由 43_level30_carry.sql 產生，本支不會有'
SELECT is_observed, is_imputed,
       count(*) AS 格數,
       count(*) FILTER (WHERE avail IS NULL) AS 其中值為NULL
FROM public.hackathon_backend_level30
GROUP BY 1, 2 ORDER BY 1, 2;
