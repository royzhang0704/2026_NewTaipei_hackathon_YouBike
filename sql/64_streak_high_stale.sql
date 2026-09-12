-- ════════════════════════════════════════════════════════════
-- 64_streak_high_stale.sql —— streak 改高風險口徑 ＋ 新增水位停滯兩欄
--
-- ① streak 口徑：overall（level_n >= 1）→ **只算高風險**（level_n = 3）
--    低／中風險的定義本來就是「預測 1~3 小時內會越線」，會隨每輪預測
--    擺動反覆亮滅，「已持續 N 小時」對它們沒有調度意義。真正要盯的是
--    「現況已經越線、而且連續幾輪都沒被處理」。
--    ★ 欄位不動，只換寫入規則（jobs/batch_predict.write_risk）+ 換
--      config.RISK_ALGO_VER（time-v1 → time-v2），streak 隨即重新起算。
--      舊列留在庫裡是舊口徑，要正確歷史請用
--      jobs/batch_predict --risk-only 照 origin 由舊到新重跑。
--
-- ② 新增 stale_avail / stale_since：該站可借數「停在同一個值多久」。
--    用來辨識卡住／疑似斷線的站 —— 水位一動也不動，比風險燈號更早
--    透露「這站的資料或車輛都沒在流動」。
--    ★ 刻意**不**吃 algo_ver：水位有沒有變動跟風險演算法無關，
--      換版把停滯時數歸零是錯的（與 streak 相反，這是兩套遞推）。
--
-- 疊加表（同 62_／63_）：ADD COLUMN IF NOT EXISTS，重跑不壞。
-- 既有列的兩欄留 NULL —— 下一輪判定時自然從當輪 origin 重新起算。
--
-- 用法：
--   PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
--        -v ON_ERROR_STOP=1 -f backend/sql/64_streak_high_stale.sql
-- ════════════════════════════════════════════════════════════
\timing on

BEGIN;

-- ══ 1　加欄（可重跑）══════════════════════════════════════════
ALTER TABLE public.hackathon_backend_risk_snapshot
  ADD COLUMN IF NOT EXISTS stale_avail int;
ALTER TABLE public.hackathon_backend_risk_snapshot
  ADD COLUMN IF NOT EXISTS stale_since timestamp;

-- ══ 2　註解 ══════════════════════════════════════════════════
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.stale_avail IS
  '★ 目前這段停滯「鎖住」的可借數。正常情況等於 now_avail；錨點查無實測（now_avail 為 NULL）時沿用前值，好讓下一輪還比得出來 —— 所以不能用 now_avail 自我比較取代這一欄。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.stale_since IS
  '★ 目前這個可借數是從哪一輪 origin 開始沒變。查詢端換算成 stale.hours（與 streak 同一套：(origin − since)/3600 + 0.5）。遞推規則：本輪 avail = 上輪 stale_avail → since 沿用；不同 → since = 本輪 origin；now_avail 為 NULL → 凍結沿用。★ 不吃 algo_ver —— 水位有沒有動跟風險演算法無關。★ now_carried=true（該輪無觀測、延用前值）照樣累加：那本來就是「這站沒回報」，與水位不動是同一件事的兩種表現。';

-- ══ 3　streak 兩欄改口徑，註解一併改掉（欄位型別不動）══════════
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.streak_n IS
  '★ 連續處於**高風險**的輪數（9/12 改口徑，原本是 overall level_n >= 1）。遞推規則：本輪 level_n = 3 且上一輪（origin-30min）也是高風險且 algo_ver 相同 → 前值+1；上一輪非高／查無列／換版 → 1；本輪非高（含中、低、無）→ 0；status=no_forecast → 沿用前值不遞增（漏打一站不代表風險消失，但也沒有證據說又持續了一輪）。★ 為什麼不算中低：中低的定義是「預測 1~3 小時內會越線」，會隨預測擺動反覆亮滅，持續時數沒有調度意義。排行的第二排序鍵 —— 改口徑後中低風險群組內的 streak 全為 0，該群組退化成以 bikes 排序，這是已知且接受的取捨。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.streak_since IS
  '★ 這段連續**高風險**的起始 origin。前端顯示「已持續 N 小時」用這個，比「連續 N 輪」耐漏批；streak_n 主要供排序。非高風險時 NULL。';

COMMIT;

ANALYZE public.hackathon_backend_risk_snapshot;

-- ══ 驗收 ═════════════════════════════════════════════════════
\echo
\echo '── 兩欄是否加起來（期望 2 列）'
SELECT a.attname AS 欄名, format_type(a.atttypid, a.atttypmod) AS 型別,
       CASE WHEN col_description(c.oid, a.attnum) IS NULL THEN '✗' ELSE '✓' END AS 有註解
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relname = 'hackathon_backend_risk_snapshot'
  AND a.attname IN ('stale_avail', 'stale_since')
ORDER BY 1;

\echo
\echo '── 既有列的 algo_ver 分佈（換版前的舊列仍是舊口徑 streak）'
\echo '   要正確歷史：uv run python -m jobs.batch_predict --risk-only（由舊到新逐輪）'
SELECT algo_ver AS 版本, count(DISTINCT origin) AS origin數, count(*) AS 列數,
       count(*) FILTER (WHERE streak_n > 0) AS streak非零列
FROM public.hackathon_backend_risk_snapshot
GROUP BY algo_ver ORDER BY 1;
