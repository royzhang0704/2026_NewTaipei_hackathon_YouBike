-- ════════════════════════════════════════════════════════════
-- 61_risk_snapshot.sql —— 建風險快照表 hackathon_backend_risk_snapshot
--
--   每個 origin × 每站一列，存「當下這一輪的風險判定結果」。
--   Job B 打完預測後同程序寫入（階段二），查詢端（/alerts）零計算。
--
-- ★ PK 是 (origin, station_uid)，origin 在前 —— 與 forecast_history 的
--   (station_uid, origin, at) 刻意相反。理由是主查詢不同：
--     forecast_history  「某一站的最新一輪」  → station 在前
--     risk_snapshot     「某一個 origin 的全市／同區排行」→ origin 在前
--   origin 在前，一輪 1,538 列連續躺在一起，PK 本身就是那筆掃描的索引；
--   streak 遞推查上一輪（origin = 本輪-30min AND station_uid = ?）也吃同
--   一個索引。反過來擺每輪都會變成全表掃。
--
-- ★ 定位：可重建的衍生快取，不是新的事實來源。每一列都能從
--   forecast_history + level30 + station_slot_average 重算出來，
--   所以可以隨時 TRUNCATE 重灌（風險演算法還在動，這條路要留著）。
--
-- ★ 判定邏輯的唯一實作在 app/service/risk_service.py（純函式），
--   單站頁 /stations/{uid}/day 與這張表共用同一份，不得各自複製。
--
-- 疊加表（同 40_）：一律 IF NOT EXISTS，重跑不清空。
--
-- 用法：
--   PGPASSWORD=postgres psql -h 127.0.0.1 -p 5433 -U postgres -d youbike \
--        -v ON_ERROR_STOP=1 -f backend/sql/61_risk_snapshot.sql
-- ════════════════════════════════════════════════════════════
\timing on

BEGIN;

CREATE TABLE IF NOT EXISTS public.hackathon_backend_risk_snapshot (
  origin        timestamp   NOT NULL,
  station_uid   text        NOT NULL,
  status        text        NOT NULL DEFAULT 'ok',
  -- ── 判定結果 ──
  level_n       smallint,
  shortage_n    smallint,
  full_n        smallint,
  side          text,
  conflict      text,
  confidence    text,
  threshold     int,
  onset         timestamp,
  -- ── 現況（錨點格）──
  now_avail     int,
  now_carried   boolean,
  now_crossed   boolean,
  baseline      real,
  -- ── 調度 ──
  action        text,
  bikes         int,
  basis         text,
  -- ── 持續 ──
  streak_n      int         NOT NULL DEFAULT 0,
  streak_since  timestamp,
  -- ── 溯源 ──
  algo_ver      text        NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (origin, station_uid)
);

-- 排行查詢：只索引有風險的站（每輪約 300~400 列，none 的 1,000 多列不進索引）
CREATE INDEX IF NOT EXISTS hackathon_backend_risk_snapshot_rank_idx
  ON public.hackathon_backend_risk_snapshot (origin, level_n DESC, streak_n DESC)
  WHERE level_n > 0;

-- 單站風險歷程（「這站連續亮了幾輪」的下鑽；streak 遞推走 PK 不走這支）
CREATE INDEX IF NOT EXISTS hackathon_backend_risk_snapshot_station_idx
  ON public.hackathon_backend_risk_snapshot (station_uid, origin DESC);

COMMENT ON TABLE public.hackathon_backend_risk_snapshot IS
  '★ 風險快照：每個 origin × 每站一列，Job B 打完預測後同程序寫入。全市／同區告警清單直接讀這張表排序，查詢端零計算。定位是「可重建的衍生快取」—— 每列都能從 forecast_history + level30 + station_slot_average 重算，可隨時 TRUNCATE 重灌。判定邏輯的唯一實作在 app/service/risk_service.py，與單站頁共用。';

COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.origin IS
  '預測原點，= forecast_run.origin = forecast_history.origin。★ PK 第一欄（origin 在前），理由見檔頭。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.station_uid IS
  '站點。第 8~9 碼即行政區碼（town_code），同區過濾用 substring 即可，不必為此複製欄位。未設 FK：主檔可能被除名（8/31 除名 62 個 5/1 後才設立的站）。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.status IS
  '★ ok = 本輪有預測、判定有效；no_forecast = 本輪 Job B 沒打這站（STALE_ANCHOR／INSUFFICIENT_HISTORY／STATION_UNKNOWN），此時 level_n 為 NULL。「不知道」與「安全（level_n=0）」必須分得開 —— 每輪寫全站就是為了讓 streak 遞推不必分辨「查無列」的歧義。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.level_n IS
  '★ 整體等級：3 高／2 中／1 低／0 無（= 兩側取大）。存數字不存字串 —— 存 high/mid/low 再用 CASE 排序，每次查詢都要算一次表達式且無法吃索引；字串轉換交給服務層。時間制分級（8/31 定案）：高=現況實測已越線且近 1 小時仍越線／中=1 小時內會越線／低=1~3 小時內會越線／無=整段不越線。越線一律看 q50。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.shortage_n IS
  '缺車側等級（q50 <= threshold）。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.full_n IS
  '滿站側等級（capacity - q50 <= threshold）。★ 同格「高+高」在 q50 判定下數學上不可能（需 cap <= 2T，最小站 8 柱 > 4）—— 出現即有 bug。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.side IS
  '較嚴重的一側：shortage／full／NULL（無風險）。同級時以缺車優先（與 dispatch 的取邊規則相同）。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.conflict IS
  'swing = 兩側都有事，路徑在 3 小時內從一端擺到另一端（潮汐站，調度看時機用）。⚠ overall 單一 streak 會把這種站算成「持續風險」，但它其實是兩種風險交替 —— 這是 9/1 的定案取捨，前段大量出現時再補 shortage_streak／full_streak 兩欄。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.confidence IS
  '信心註記 almost_certain／likely／possible／none —— 三分位降級後的用途。缺車側連 q90 都越線 = 運氣好也缺 = 幾乎確定；滿站側對稱用 q19。⚠ 不可從三分位反推機率百分比（H=6 逐格 q* 未校準，見 config.CAVEATS）。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.threshold IS
  '★ 風險門檻 T = clamp(15% × capacity, 2, 不封頂)（8/31 使用者定案，config.RISK_PCT/MIN/MAX）。用 int(x+0.5) 不用 round() —— Python 是銀行家捨入（2.5→2、4.5→4），跟 SQL 統計對不起來，25/45 柱共 55 站會差 1 台。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.onset IS
  '預計越線時刻。level=高時 = origin 本身（現況就已越線）；無風險為 NULL。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.now_avail IS
  '錨點格的實測可借量（缺格已 carry-forward，語意與 /stations/{uid}/day 的 now.avail 一致）。⚠ avail 永遠 >= 0，「來了沒車、走人」在水位資料上完全隱形，要租借事件流才分得開（8/31 未解問題）。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.now_carried IS
  '錨點格無觀測、延用前一個實測值。true 時 now_avail 不是當下實測，前端要誠實標示。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.now_crossed IS
  '★ 現況實測已越線 —— 這是「高風險」的第一條件（最重的等級要靠最硬的證據，不是未校準的 q*）。獨立存下來，事後查「為什麼這站是高」不必回推。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.baseline IS
  '該站該時段的歷史常態可借量（hackathon_backend_station_slot_average，統計至訓練截止日 2026-04-30）。樣本不足（n < config.SLOT_AVG_MIN_N = 10）為 NULL —— 寧可退回門檻算法，也不要拿 n=3 的平均當目標。搭配 streak 可識別結構性誤報：streak_n >= 20 且 baseline < threshold = 歷史常態本來就低於門檻，每晚必亮（板橋站 86 柱夜間常態 3.5、景安站 96 柱常態 1.8）。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.action IS
  '調度動作 refill（補車）／remove（取車）／hold（預計自行消退，暫不派車）／NULL（無風險）。★ level_n=3 不得為 hold —— 高風險的定義是現況已越線，人現在就借不到車，不可能得到「不用去」的結論（9/1 修掉的 bug）。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.bikes IS
  '★ 建議調度台數。basis=slot_average 時 = max(現況缺口, 窗內六格缺口平均)，缺口 = 該格歷史平均 − q50（取車反向）—— 目標是回到這站這個時段的常態水位，不是剛好脫離紅區。模型已把自然消退算進預測，只該搬消退不掉的那部分。窗內取平均不取窗尾單格：單格會被 origin 對齊的偶然綁架（差 0.11 台就從補 6 台翻成不派車）。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.basis IS
  'slot_average = 用歷史同時段常態算的（正常路徑）；threshold = 查無該桶或樣本不足，退回「門檻 − 路徑極值 + 1」的保底算法。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.streak_n IS
  '★ 連續處於風險的輪數（overall 口徑，9/1 定案不分缺車／滿站兩側）。遞推規則：本輪有風險且上一輪（origin-30min）也有風險且 algo_ver 相同 → 前值+1；上一輪無風險／查無列／換版 → 1；本輪無風險 → 0；status=no_forecast → 沿用前值不遞增（漏打一站不代表風險消失，但也沒有證據說又持續了一輪）。排行的第二排序鍵。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.streak_since IS
  '★ 這段連續風險的起始 origin。前端顯示「已持續 N 小時」用這個，比「連續 N 輪」耐漏批；streak_n 主要供排序。無風險時 NULL。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.algo_ver IS
  '★ 判定當下的演算法版本（config.RISK_ALGO_VER，如 time-v1/pct15）。改門檻或改分級規則就換這個字串，streak 隨即重新起算 —— 跨版本的「連續 N 輪」沒有意義。用 jobs/batch_predict --risk-only 重算時，務必照 origin 由舊到新逐輪跑，streak 才接得起來。';
COMMENT ON COLUMN public.hackathon_backend_risk_snapshot.created_at IS
  '本列寫入時刻。同 origin 重判會覆蓋並更新這一欄；要看歷次執行請查 job_run。';

COMMIT;

ANALYZE public.hackathon_backend_risk_snapshot;

-- ══ 驗收 ═════════════════════════════════════════════════════
\echo
\echo '── 表是否建起來（期望 1 列、22 欄）'
SELECT c.relname AS 表名,
       (SELECT count(*) FROM pg_attribute a
         WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS 欄數,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS 大小
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'hackathon_backend_risk_snapshot';

\echo
\echo '── PK / 索引（期望 3 支：pkey + rank_idx + station_idx）'
SELECT indexname AS 索引名, indexdef AS 定義
FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'hackathon_backend_risk_snapshot'
ORDER BY 1;

\echo
\echo '── 註解覆蓋率（缺註解欄數應為 0）'
SELECT count(*) AS 欄數,
       count(*) FILTER (WHERE col_description(c.oid, a.attnum) IS NULL) AS 缺註解,
       CASE WHEN obj_description(c.oid, 'pg_class') IS NULL THEN '✗' ELSE '✓' END AS 表註解
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relname = 'hackathon_backend_risk_snapshot'
GROUP BY c.oid;

\echo
\echo '── 現有列數（首次建表應為 0）'
SELECT count(*) AS n FROM public.hackathon_backend_risk_snapshot;
